# DNS for ratchetci.dev

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

With a token scoped to **Zone → DNS → Edit** for the zone. Keep the token in your shell, read
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
