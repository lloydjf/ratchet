# DNS for ratchetci.dev

Two layouts, chosen by `RATCHET_EDGE` in `/etc/ratchet/env` (`bootstrap.sh --edge`):

| | `tunnel` (Cloudflare in front) | `caddy` (default; one public VM) |
| --- | --- | --- |
| `ratchetci.dev`, `www` | Cloudflare static assets: Worker `ratchetci-site` (`.github/workflows/site.yml`) | Caddy on the VM, files in `/var/www/ratchet-site` |
| `demo.ratchetci.dev` | Cloudflare Tunnel -> `cloudflared` on the VM -> Caddy on `127.0.0.1:80` | Caddy on the VM, ports 80/443 |
| Certificates | Cloudflare's edge | Caddy (ACME HTTP-01) |
| Records | none by hand: wrangler and the tunnel create them (proxied) | A records to the VM, DNS only |
| VM inbound ports | 22 only | 22, 80, 443 |
| Failover to a standby VM | start the connector there; no DNS change | move the A records (`failover.sh dns`) |

## Tunnel mode

No A record for any name. Every record is proxied (orange cloud) and created by Cloudflare:

| Name | Type | Value | Created by |
| --- | --- | --- | --- |
| `ratchetci.dev` | Worker custom domain | Worker `ratchetci-site` | `wrangler deploy` (site.yml) |
| `www.ratchetci.dev` | Worker custom domain | Worker `ratchetci-site`, plus the Redirect Rule below | `wrangler deploy` |
| `demo.ratchetci.dev` | CNAME | `<tunnel-id>.cfargotunnel.com` | the tunnel's public hostname |

Before the first deploy, **delete** any A, AAAA or CNAME record left over for `ratchetci.dev`,
`www.ratchetci.dev` and `demo.ratchetci.dev` (from caddy mode): a custom domain or a public
hostname cannot take a name another record holds. No origin certificate is involved (the
tunnel is an outbound connection; the assets live at Cloudflare), so the zone's SSL/TLS
mode does not matter for these names. `.dev` is on the HSTS preload list: HTTPS only.

### 1. The tunnel (demo host)

Cloudflare dashboard -> Zero Trust -> Networks -> Tunnels -> **Create a tunnel** ->
Cloudflared, name `ratchet-demo`. Copy the token from the install command it shows (the long
argument after `service install`); do not run that command. Then **Public hostnames** ->
Add:

| Field | Value |
| --- | --- |
| Subdomain / domain | `demo` / `ratchetci.dev` |
| Path | empty |
| Service | `HTTP` `127.0.0.1:80` |

Caddy on the VM matches the Host header (`demo.ratchetci.dev`), so no HTTP Host header
override is needed. Requests for any other hostname through the tunnel get the catch-all,
`http_status:404` (the dashboard's default last rule). This also creates the CNAME.

On the VM (the token never touches the shell history or a command line):

```bash
sudo /opt/ratchet/deploy/vm/edge-tunnel.sh set-token < token.txt && shred -u token.txt
#   or: read -rs T && printf %s "$T" | sudo /opt/ratchet/deploy/vm/edge-tunnel.sh set-token; unset T
sudo /opt/ratchet/deploy/vm/edge-tunnel.sh install        # cloudflared + ratchet-tunnel.service
sudo /opt/ratchet/deploy/vm/edge-tunnel.sh status
```

Same with the API (token with Account -> Cloudflare Tunnel: Edit and Zone -> DNS: Edit;
define `api` and `ZONE_ID` as in "Cloudflare API" at the end; `ACCOUNT_ID` from the
dashboard):

```bash
TUNNEL_ID="$(api "/accounts/${ACCOUNT_ID}/cfd_tunnel" -X POST \
  --data '{"name":"ratchet-demo","config_src":"cloudflare"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["id"])')"
api "/accounts/${ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/configurations" -X PUT --data '{"config":{
  "ingress":[{"hostname":"demo.ratchetci.dev","service":"http://127.0.0.1:80"},
             {"service":"http_status:404"}]}}' >/dev/null
api "/zones/${ZONE_ID}/dns_records" -X POST --data "{\"type\":\"CNAME\",\"name\":\"demo.ratchetci.dev\",
  \"content\":\"${TUNNEL_ID}.cfargotunnel.com\",\"proxied\":true}" >/dev/null
# The token goes straight into a file for the VM; it is never printed.
( umask 077; api "/accounts/${ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/token" \
  | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["result"])' > token.txt )
```

The SSE stream (`/api/v1/events/stream`) works through the tunnel as is: the app sends a
heartbeat every 15 s (Cloudflare closes connections idle for 100 s), and Caddy marks it
`Cache-Control: no-cache` and never compresses it.

### 2. The apex site (Cloudflare static assets)

One-time setup is in the header of `.github/workflows/site.yml`: an API token (Workers
Scripts: Edit, Workers Routes: Edit, DNS: Edit), the repository secrets
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, then a run on `main`. `wrangler deploy`
attaches `ratchetci.dev` and `www.ratchetci.dev` as custom domains of the Worker and creates
their records itself; nothing to add in DNS.

### 3. www -> apex (Redirect Rule)

The assets' `_redirects` cannot match on host, so the 301 is a zone rule. Dashboard: the
zone -> Rules -> Overview -> Templates -> **Redirect from WWW to root**: when hostname equals
`www.ratchetci.dev`, dynamic redirect to `concat("https://ratchetci.dev", http.request.uri.path)`,
301, preserve query string. Until it exists, `www` serves the same pages as the apex. API
(a token that may edit the zone's Redirect Rules; this PUT replaces all of them):

```bash
api "/zones/${ZONE_ID}/rulesets/phases/http_request_dynamic_redirect/entrypoint" -X PUT --data '{
  "rules":[{"description":"www -> apex","expression":"(http.host eq \"www.ratchetci.dev\")",
    "action":"redirect","action_parameters":{"from_value":{"status_code":301,
      "target_url":{"expression":"concat(\"https://ratchetci.dev\", http.request.uri.path)"},
      "preserve_query_string":true}}}]}' >/dev/null
```

Optional alt domains (`ratchetci.com`, `www.ratchetci.com`) in tunnel mode: add the zone to
the account and the same kind of Redirect Rule there (a proxied placeholder record, e.g.
`AAAA 100::`, so the rule has traffic to act on). `RATCHET_ALT_ADDRESSES` is caddy mode only.

Check (tunnel mode):

```bash
dig +short ratchetci.dev demo.ratchetci.dev @1.1.1.1          # Cloudflare addresses, not the VM
curl -sSI https://ratchetci.dev/docs | grep -iE '^(HTTP|location)'   # 301 -> /docs/
curl -sSI https://ratchetci.dev/ | grep -iE '^(strict-transport|content-security)'
curl -sSI https://www.ratchetci.dev/x?y=1 | grep -i '^location'      # https://ratchetci.dev/x?y=1
curl -fsS https://demo.ratchetci.dev/healthz; echo
curl -sSN --max-time 20 https://demo.ratchetci.dev/api/v1/events/stream | head -n 3   # events/heartbeat
sudo /opt/ratchet/deploy/vm/edge-tunnel.sh status             # on the VM: ready 200
```

## Caddy mode

One VM serves every name; Caddy on it holds the certificates. Create the records **before**
`bootstrap.sh` if you want TLS on the first try (Caddy keeps retrying on its own otherwise).

| Name | Type | Value | Proxy | TTL |
| --- | --- | --- | --- | --- |
| `ratchetci.dev` (apex) | A | VM public IP | DNS only | 300 (60 during judging) |
| `demo.ratchetci.dev` | A | VM public IP | DNS only | 300 (60 during judging) |
| `www.ratchetci.dev` | CNAME | `ratchetci.dev` | DNS only | 300 |
| `ratchetci.com`, `www.ratchetci.com` (optional) | A / CNAME | VM IP / `ratchetci.com` | DNS only | 300 |

- **DNS only, no proxy.** Caddy gets certificates from Let's Encrypt with the HTTP-01
  challenge on port 80, so the names must resolve to the VM itself. A CDN proxy in front
  (Cloudflare's orange cloud) answers the challenge itself, and Caddy never gets a certificate.
  For Cloudflare in front, use tunnel mode instead.
- **No AAAA** unless the VM has a public IPv6 address and the security group allows 80/443 on
  it: a wrong AAAA record breaks the challenge for IPv6-first resolvers.
- The security group (and `ufw`, if `RATCHET_ENABLE_UFW=1`) must allow 80 and 443 from anywhere.
- `.dev` is on the HSTS preload list: browsers only ever use HTTPS for these names, so the
  site is unreachable until the certificate is issued.
- Optional alt domains: list them in `RATCHET_ALT_ADDRESSES` in `/etc/ratchet/env` (e.g.
  `ratchetci.com www.ratchetci.com`) and re-run `bootstrap.sh`; each answers 301 to the apex.

Check:

```bash
dig +short ratchetci.dev demo.ratchetci.dev www.ratchetci.dev @1.1.1.1   # VM IP (www: CNAME + IP)
curl -sSI http://ratchetci.dev/ | head -n 3                              # 308 -> https (Caddy)
curl -fsS https://demo.ratchetci.dev/healthz; echo
curl -sSI https://www.ratchetci.dev/ | grep -i '^location'               # https://ratchetci.dev/
journalctl -u caddy --since -15min -o cat | grep -i 'certificate obtained'
```

## Cloudflare API

The `api` helper and `ZONE_ID` are used by both modes; `upsert` and the A records are caddy
mode. With a token scoped to **Zone → DNS → Edit** for the zone (plus what the tunnel and
redirect calls above need, when you use them). Keep the token in your shell, read
from a password manager; never write it to a file or print it. The calls below send it only
in the `Authorization` header.

```bash
read -rs CLOUDFLARE_API_TOKEN && export CLOUDFLARE_API_TOKEN    # paste; nothing is echoed
VM_IP=203.0.113.10                                               # the VM's public IP
api() {  # api <endpoint> [curl args...]  (bash or zsh)
  local endpoint="$1"; shift
  curl -fsS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4${endpoint}" "$@"
}

ZONE_ID="$(api "/zones?name=ratchetci.dev" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][0]["id"])')"

# Create or update one record (proxied=false: DNS only).
upsert() {  # upsert <type> <name> <content> [ttl, default 300]
  local id body
  body="$(printf '{"type":"%s","name":"%s","content":"%s","ttl":%s,"proxied":false}' "$1" "$2" "$3" "${4:-300}")"
  id="$(api "/zones/${ZONE_ID}/dns_records?type=$1&name=$2" \
        | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r[0]["id"] if r else "")')"
  if [[ -n "$id" ]]; then api "/zones/${ZONE_ID}/dns_records/${id}" -X PUT --data "$body" >/dev/null
  else api "/zones/${ZONE_ID}/dns_records" -X POST --data "$body" >/dev/null; fi
  echo "$1 $2 -> $3"
}

upsert A     ratchetci.dev      "$VM_IP"
upsert A     demo.ratchetci.dev "$VM_IP"
upsert CNAME www.ratchetci.dev  ratchetci.dev
unset CLOUDFLARE_API_TOKEN
```

The same `upsert A <host> <standby-ip> 60` for the apex and `demo` is the DNS failover
(`deploy/vm/failover.sh dns <standby-ip>` prints the steps). To automate it, wrap `upsert A
"$1" "$2"` in a script that reads the token from a root-only file, and set
`RATCHET_DNS_HOOK=/usr/local/bin/<script>` in `/etc/ratchet/env`; `failover.sh dns` calls it
once per host as `<script> <host> <ip>`.
