"""deploy/vm: the Caddy layout in both edge modes (caddy, tunnel), the static site (VM and
Cloudflare static assets), the tunnel connector, and the Nebius token refresh.

Static checks plus small runs (render_site.py, nebius-token.sh without credentials, the
failover help); none needs systemd or the network. `caddy validate` runs when a caddy binary
is on PATH, and with Docker under `-m docker`.
"""

from __future__ import annotations

import base64
import configparser
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
VM = ROOT / "deploy" / "vm"
SENTENCE = (
    "Your agent learns new skills every day. Ratchet makes sure it never unlearns the old ones."
)


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(VM / "systemd" / name)
    return parser


def _blocks(caddyfile: str) -> dict[str, str]:
    """Top-level blocks by address line. Line based, as `caddy fmt` lays files out: a block
    opens with a line ending in `{` and closes with a line that is only `}` (placeholders
    such as {$VAR} or {uri} never end a line)."""
    blocks: dict[str, str] = {}
    address, depth, body = "", 0, []
    for raw in caddyfile.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if depth == 0:
            if line.endswith("{"):
                address, depth, body = line[:-1].strip(), 1, []
            continue
        if line.endswith("{"):
            depth += 1
        elif line == "}":
            depth -= 1
            if depth == 0:
                blocks[address] = "\n".join(body)
                continue
        body.append(line)
    return blocks


def _render_site() -> ModuleType:
    spec = importlib.util.spec_from_file_location("render_site", VM / "render_site.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Caddy ----------------------------------------------------------------------------------------

CADDYFILE = (VM / "Caddyfile").read_text(encoding="utf-8")
BLOCKS = _blocks(CADDYFILE)
EDGE_CADDY = _blocks(BLOCKS["(edge_caddy)"])
EDGE_TUNNEL = _blocks(BLOCKS["(edge_tunnel)"])
DEMO = BLOCKS["(demo_site)"]
MODE_ENV = {
    "caddy": {
        "RATCHET_EDGE": "caddy",
        "RATCHET_SITE_ADDRESS": "ratchet.example.com",
        "RATCHET_DEMO_ADDRESS": "demo.ratchet.example.com",
    },
    "tunnel": {"RATCHET_EDGE": "tunnel", "RATCHET_DEMO_ADDRESS": "demo.ratchet.example.com"},
}


def test_the_edge_mode_picks_one_snippet_and_defaults_to_caddy() -> None:
    assert "import edge_{$RATCHET_EDGE:caddy}" in CADDYFILE.splitlines()
    tops = [a for a in BLOCKS if not a.startswith("(")]
    assert tops == [""], tops  # only the global options: every site lives in a mode snippet


def test_caddy_mode_serves_apex_www_and_demo() -> None:
    apex, www, demo = (
        EDGE_CADDY["{$RATCHET_SITE_ADDRESS}"],
        EDGE_CADDY["www.{$RATCHET_SITE_ADDRESS}"],
        EDGE_CADDY["{$RATCHET_DEMO_ADDRESS}"],
    )
    assert "root * /var/www/ratchet-site" in apex and "file_server" in apex
    assert "reverse_proxy" not in apex  # the apex never reaches the app
    assert "redir https://{$RATCHET_SITE_ADDRESS}{uri} permanent" in www  # 301
    assert demo.strip() == "import demo_site"
    assert "import /etc/caddy/ratchet.d/redirects/*.caddy" in BLOCKS["(edge_caddy)"]
    for block in (apex, www, DEMO):
        assert "import security_headers" in block
    assert "reverse_proxy {$RATCHET_UPSTREAM_ADDR:127.0.0.1:8080}" in DEMO
    assert "/var/www/ratchet-fallback" in DEMO and 'respond "Not found" 404' in DEMO


def test_tunnel_mode_is_the_demo_host_over_plain_http_on_loopback_only() -> None:
    assert set(EDGE_TUNNEL) == {"http://{$RATCHET_DEMO_ADDRESS}", "http://"}
    for block in EDGE_TUNNEL.values():
        assert "bind 127.0.0.1 [::1]" in block.splitlines()
    assert "import demo_site" in EDGE_TUNNEL["http://{$RATCHET_DEMO_ADDRESS}"]
    assert 'respond "Not found" 404' in EDGE_TUNNEL["http://"]  # any other Host
    tunnel = BLOCKS["(edge_tunnel)"]
    assert "RATCHET_SITE_ADDRESS" not in tunnel and "/var/www/ratchet-site" not in tunnel
    # cloudflared connects from loopback: trust it for the client address, nothing else.
    assert "trusted_proxies static 127.0.0.1/32 ::1/128" in BLOCKS[""]
    assert "client_ip_headers Cf-Connecting-Ip" in BLOCKS[""]


def test_the_event_stream_is_never_compressed_or_cached() -> None:
    demo = _blocks(DEMO)
    assert "@sse path /api/v1/events/stream /api/events/stream" in DEMO
    assert "@compressible not path /api/v1/events/stream /api/events/stream" in DEMO
    assert "encode @compressible zstd gzip" in DEMO
    assert not re.search(r"^encode (?!@compressible)", DEMO, re.M)
    sse = demo["handle @sse"]
    assert 'header_down Cache-Control "no-cache"' in sse
    assert "flush_interval -1" in sse and "read_timeout 0" in sse


def test_every_site_keeps_the_security_headers_and_a_strict_csp() -> None:
    snippet = BLOCKS["(security_headers)"]
    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
    ):
        assert header in snippet, header
    for name, block in (("apex", EDGE_CADDY["{$RATCHET_SITE_ADDRESS}"]), ("demo", DEMO)):
        csp = re.search(r'Content-Security-Policy "([^"]+)"', block)
        assert csp, name
        script_src = re.search(r"script-src ([^;]+)", csp.group(1))
        assert script_src and "unsafe-inline" not in script_src.group(1), name
        assert "frame-ancestors 'none'" in csp.group(1)
    apex = EDGE_CADDY["{$RATCHET_SITE_ADDRESS}"]
    assert "{vars.docs_script_src}" in apex  # docs theme scripts by hash only
    assert "import /etc/caddy/ratchet.d/site/*.caddy" in apex
    assert CADDYFILE.count("import access_log") == 2


def test_the_cloudflare_csp_is_the_caddy_apex_csp() -> None:
    csp = re.search(r'Content-Security-Policy "([^"]+)"', EDGE_CADDY["{$RATCHET_SITE_ADDRESS}"])
    assert csp and csp.group(1) == _render_site().SITE_CSP


def _validate(caddy: list[str], mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*caddy, "validate", "--config", str(VM / "Caddyfile"), "--adapter", "caddyfile"],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **MODE_ENV[mode]},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("caddy") is None, reason="needs a caddy binary")
@pytest.mark.parametrize("mode", ["caddy", "tunnel"])
def test_caddyfile_validates_in_both_modes(mode: str) -> None:
    result = _validate(["caddy"], mode)
    assert result.returncode == 0, result.stderr[-2000:]
    adapted = subprocess.run(
        ["caddy", "adapt", "--config", str(VM / "Caddyfile"), "--adapter", "caddyfile"],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **MODE_ENV[mode]},
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(adapted.stdout)
    listen = sorted(
        addr for server in config["apps"]["http"]["servers"].values() for addr in server["listen"]
    )
    if mode == "tunnel":
        assert listen == ["127.0.0.1:80", "[::1]:80"]
        assert "tls" not in config["apps"]  # no ACME, no certificates
    else:
        assert listen == [":443"]


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["caddy", "tunnel"])
def test_caddyfile_validates_in_both_modes_with_docker(mode: str) -> None:
    flags = [f"-e{key}={value}" for key, value in MODE_ENV[mode].items()]
    result = subprocess.run(
        ["docker", "run", "--rm", *flags, "-v", f"{VM / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
         "caddy:2.11", "caddy", "validate", "--config", "/etc/caddy/Caddyfile",
         "--adapter", "caddyfile"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr[-2000:]


def test_bootstrap_and_deploy_follow_the_edge_mode() -> None:
    bootstrap = (VM / "bootstrap.sh").read_text(encoding="utf-8")
    deploy = (VM / "deploy.sh").read_text(encoding="utf-8")
    lib = (VM / "lib.sh").read_text(encoding="utf-8")
    for script in (bootstrap, deploy):
        assert "--edge) EDGE=" in script and "env_set RATCHET_EDGE" in script
    assert "--site-url" in bootstrap and "--public-url" in bootstrap
    assert "configure_edge" in bootstrap and "edge_firewall" in bootstrap
    configure = lib[lib.index("configure_edge() {") : lib.index("edge_firewall() {")]
    for line in (
        "Environment=RATCHET_EDGE=${edge}",
        "Environment=RATCHET_SITE_ADDRESS=${apex}",
        "Environment=RATCHET_DEMO_ADDRESS=${demo}",
    ):
        assert line in configure, line
    # The apex is built on the VM only in caddy mode; the fallback page in both.
    caddy_only = re.search(r'if \[\[ "\$edge" == "caddy" \]\]; then\n(.*?)\n  fi', configure, re.S)
    assert caddy_only and "render_site ||" in caddy_only.group(1)
    assert configure.index("render_fallback") < configure.index('if [[ "$edge" == "caddy" ]]')
    assert 'edge-tunnel.sh" install' in configure
    assert re.search(
        r'if \[\[ "\$\(edge_mode\)" == "caddy" \]\]; then\n\s+render_site \|\|', deploy
    )
    assert "install_caddyfile" in deploy and "render_fallback" in deploy
    template = (VM / "env.template").read_text(encoding="utf-8").splitlines()
    for key in ("RATCHET_PUBLIC_URL=", "RATCHET_SITE_URL=", "RATCHET_ALT_ADDRESSES="):
        assert key in template, key
    assert "RATCHET_EDGE=caddy" in template  # today's layout stays the default


def test_tunnel_mode_opens_no_web_port() -> None:
    lib = (VM / "lib.sh").read_text(encoding="utf-8")
    firewall = lib[lib.index("edge_firewall() {") :]
    tunnel = firewall[firewall.index('if [[ "$edge" == "tunnel" ]]') : firewall.index("else")]
    assert "ufw allow 80" not in tunnel and "ufw allow 443" not in tunnel
    assert "ufw delete allow 80/tcp" in tunnel and "ufw delete allow 443/tcp" in tunnel
    assert firewall.index("ufw allow 22/tcp") < firewall.index("ufw --force enable")


def test_bootstrap_configures_the_firewall_before_anything_can_fail_late() -> None:
    bootstrap = (VM / "bootstrap.sh").read_text(encoding="utf-8")
    main = bootstrap[bootstrap.index("main() {") :]
    order = [main.index(step) for step in ("setup_env_file", "setup_firewall", "build_app")]
    assert order == sorted(order)
    assert main.index("setup_firewall") < main.index("setup_caddy")
    assert "--ufw) UFW=" in bootstrap and "env_set RATCHET_ENABLE_UFW" in bootstrap
    assert "RATCHET_ENABLE_UFW=" in (VM / "env.template").read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    ("edge", "knob", "expected"),
    [
        ("tunnel", "", "on"),  # tunnel: nothing listens publicly, so on by default
        ("caddy", "", "off"),  # caddy: opt-in, as before
        ("caddy", "1", "on"),
        ("tunnel", "0", "off"),
    ],
)
def test_ufw_default_follows_the_edge(tmp_path: Path, edge: str, knob: str, expected: str) -> None:
    env_file = tmp_path / "env"
    # The knob is read from the env file, so `sudo` dropping the caller's variables is moot.
    env_file.write_text(f"RATCHET_EDGE={edge}\nRATCHET_ENABLE_UFW={knob}\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", f'source "{VM / "lib.sh"}"; ufw_wanted && echo on || echo off'],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RATCHET_ENV_FILE": str(env_file)},
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert result.stdout.strip() == expected


def test_caddy_logs_stay_writable_by_the_caddy_user() -> None:
    """`caddy validate` opens the access-log writers: as root it leaves root-owned 0600 files
    in /var/log/caddy that the service (user caddy) cannot open, and Caddy then fails to start
    ("setting up custom log ... opening log writer")."""
    log_paths = re.findall(r"output file (\S+)", CADDYFILE)
    assert log_paths and all(path.startswith("/var/log/caddy/") for path in log_paths)
    lib = (VM / "lib.sh").read_text(encoding="utf-8")
    helper = lib[lib.index("caddy_log_dir() {") : lib.index("caddy_validate() {")]
    assert "install -d -m 0755 -o caddy -g caddy /var/log/caddy" in helper
    assert "chown caddy:caddy" in helper
    validate = lib[lib.index("caddy_validate() {") : lib.index("install_caddyfile() {")]
    assert "runuser -u caddy --" in validate and "caddy_log_dir" in validate
    # Every validation in the ops scripts goes through the helper.
    for script in VM.glob("*.sh"):
        text = script.read_text(encoding="utf-8")
        if script.name == "lib.sh":
            text = text.replace(validate, "")
        code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        assert not any("caddy validate" in ln for ln in code), script.name
    configure = lib[lib.index("configure_edge() {") : lib.index("ufw_wanted() {")]
    assert configure.index("caddy_log_dir") < configure.index("systemctl restart caddy")
    reload = lib[lib.index("caddy_reload() {") :]
    assert reload.index("caddy_log_dir") < reload.index("systemctl reload caddy")
    restart = (VM / "restart.sh").read_text(encoding="utf-8")
    assert restart.index("caddy_log_dir") < restart.index("systemctl restart caddy")


# --- the static site ------------------------------------------------------------------------------


def test_index_html_is_static_and_says_what_it_should() -> None:
    page = (VM / "www" / "index.html").read_text(encoding="utf-8")
    assert "<script" not in page.lower()
    assert not re.search(r"\son[a-z]+\s*=", page, re.I), "inline event handler"
    assert not re.search(
        r"""(?:src|href)=["']https?://(?!demo\.ratchetci\.dev|github\.com/lloydjf/ratchet|ratchetci\.dev)""",
        page,
    )
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", page))
    assert SENTENCE in text
    assert "uv tool install ratchet-ci" in page
    for href in (
        "https://demo.ratchetci.dev",
        "/docs/",
        "https://github.com/lloydjf/ratchet",
        "/bench/",
    ):
        assert f'href="{href}"' in page, href
    css = (VM / "www" / "site.css").read_text(encoding="utf-8")
    assert "prefers-color-scheme: light" in css and "@import" not in css


def test_render_site_builds_bench_and_hashes_only_inline_docs_scripts(tmp_path: Path) -> None:
    docs = tmp_path / "docs-site"
    (docs / "guide").mkdir(parents=True)
    inline = "var a=1;"
    (docs / "guide" / "index.html").write_text(
        f'<script>{inline}</script><script src="x.js"></script>'
        '<script id="__config" type="application/json">{"a": 1}</script>',
        encoding="utf-8",
    )
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the web", encoding="utf-8")
    (docs / "leak.txt").symlink_to(secret)
    out, csp = tmp_path / "out", tmp_path / "ratchet.d" / "site" / "docs-csp.caddy"
    module = _render_site()
    code = module.main(
        [
            "--app-dir", str(ROOT), "--out", str(out), "--docs", str(docs),
            "--bench", str(ROOT / "bench" / "results" / "2026-10-16"),
            "--ref", "abc123", "--csp-out", str(csp),
        ]
    )  # fmt: skip
    assert code == 0
    assert (out / "index.html").is_file() and (out / "docs" / "guide" / "index.html").is_file()
    assert not (out / "docs" / "leak.txt").exists()  # symlinks are never published
    digest = base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode()
    fragment = csp.read_text(encoding="utf-8")
    assert f"vars /docs/* docs_script_src \"'sha256-{digest}'\"" in fragment  # JSON skipped
    bench = (out / "bench" / "index.html").read_text(encoding="utf-8")
    assert "<script" not in bench.lower()
    assert "<table>" in bench and 'src="cost_vs_recall.svg"' in bench
    assert "https://github.com/lloydjf/ratchet/blob/abc123/bench/configs/cascade.yaml" in bench
    assert 'href="../../' not in bench  # no link escapes /bench/
    logo = (out / "logo.svg").read_text(encoding="utf-8")
    assert "currentColor" not in logo and "prefers-color-scheme" in logo


def test_render_site_refuses_anything_but_hash_sources(tmp_path: Path) -> None:
    module = _render_site()
    with pytest.raises(SystemExit):
        module.write_csp_fragment(tmp_path / "x.caddy", ["'unsafe-inline'"])
    with pytest.raises(SystemExit):
        module.write_csp_fragment(tmp_path / "x.caddy", ["'sha256-a'\"\n}\nevil {"])


def test_render_site_cloudflare_mode_needs_no_vm_paths(tmp_path: Path) -> None:
    docs = tmp_path / "site"
    docs.mkdir()
    inline = "document.body.dataset.x=1"
    (docs / "index.html").write_text(f"<script>{inline}</script>", encoding="utf-8")
    (docs / "404.html").write_text("not found", encoding="utf-8")
    out = tmp_path / "dist"
    module = _render_site()
    # No --app-dir (defaults to this checkout), no --bench (newest dated run), no --csp-out.
    assert module.main(["--out", str(out), "--docs", str(docs), "--cloudflare"]) == 0
    for name in ("index.html", "404.html", "docs/index.html", "docs/404.html", "bench/index.html"):
        assert (out / name).is_file(), name
    assert module.latest_bench(ROOT).name[:4].isdigit()  # bench/results/smoke-* is skipped
    headers = (out / "_headers").read_text(encoding="utf-8")
    rules = [line for line in headers.splitlines() if line and not line.startswith(("#", " "))]
    assert rules == ["/*"]  # one rule: two CSP headers would be joined into two policies
    for name, value in module.SECURITY_HEADERS:
        assert f"  {name}: {value}" in headers, name
    assert "Strict-Transport-Security: max-age=31536000" in headers
    assert "X-Content-Type-Options: nosniff" in headers and "Referrer-Policy:" in headers
    csp = re.search(r"^  Content-Security-Policy: (.+)$", headers, re.M)
    assert csp
    digest = base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode()
    script_src = re.search(r"script-src ([^;]+)", csp.group(1))
    assert script_src and script_src.group(1) == f"'self' 'sha256-{digest}'"
    assert "unsafe-inline" not in script_src.group(1) and "unsafe-eval" not in csp.group(1)
    assert all(len(line) <= 2000 for line in headers.splitlines())  # Cloudflare's line limit
    redirects = (out / "_redirects").read_text(encoding="utf-8").splitlines()
    assert "/docs /docs/ 301" in redirects and "/bench /bench/ 301" in redirects
    # Host-level rules are not supported in _redirects; www -> apex is a zone Redirect Rule.
    assert not any(line.split()[0].startswith("http") for line in redirects if line[:1] != "#")
    with pytest.raises(SystemExit):  # never wipes the checkout
        module.main(["--out", str(ROOT)])


def _jsonc(path: Path) -> dict[str, object]:
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )  # fmt: skip
    data = json.loads(text)
    assert isinstance(data, dict)
    return data


def test_site_workflow_builds_and_deploys_the_static_assets_worker() -> None:
    import yaml

    config = _jsonc(ROOT / "deploy" / "site" / "wrangler.jsonc")
    assert config["name"] == "ratchetci-site"
    assert config["assets"] == {
        "directory": "./dist",
        "not_found_handling": "404-page",
        "html_handling": "auto-trailing-slash",
    }
    assert config["routes"] == [
        {"pattern": "ratchetci.dev", "custom_domain": True},
        {"pattern": "www.ratchetci.dev", "custom_domain": True},
    ]
    assert config["workers_dev"] is False and "main" not in config  # assets only, no code
    assert "deploy/site/dist/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    path = ROOT / ".github" / "workflows" / "site.yml"
    text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    on = workflow[True]  # YAML 1.1 reads the `on:` key as True
    assert "workflow_dispatch" in on and on["push"]["branches"] == ["main"]
    for watched in ("docs/**", "bench/results/**", "deploy/vm/www/**", "deploy/vm/render_site.py",
                    "mkdocs.yml", "README.md", ".github/workflows/site.yml"):  # fmt: skip
        assert watched in on["push"]["paths"], watched
    build = "\n".join(step.get("run", "") for step in workflow["jobs"]["build"]["steps"])
    assert "uv run python scripts/gen_docs.py" in build
    assert "mkdocs build --strict" in build
    assert "render_site.py --out deploy/site/dist --docs site" in build and "--cloudflare" in build
    deploy = workflow["jobs"]["deploy"]
    assert deploy["if"] == "github.ref == 'refs/heads/main'"  # other branches never publish
    wrangler = next(s for s in deploy["steps"] if "wrangler-action" in s.get("uses", ""))
    assert wrangler["uses"] == "cloudflare/wrangler-action@v3"
    assert wrangler["with"]["command"] == "deploy --config deploy/site/wrangler.jsonc"
    assert wrangler["with"]["apiToken"] == "${{ secrets.CLOUDFLARE_API_TOKEN }}"
    assert wrangler["with"]["accountId"] == "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", workflow["env"]["WRANGLER_VERSION"])
    assert "ratchetci-site" in text


# --- Cloudflare Tunnel connector (RATCHET_EDGE=tunnel) ------------------------------------------


def test_tunnel_script_never_exposes_the_token() -> None:
    script = (VM / "edge-tunnel.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "set -x" not in code
    assert "--token " not in code and "TUNNEL_TOKEN=" not in code  # never on a command line/env
    # The token file is never read into a variable or printed: only tested, sized and moved.
    assert not re.search(r"\$\((?:cat|<)\s*\"?\$\{?TOKEN_FILE", code)
    assert not re.search(r"\b(?:cat|less|head|tail)\b[^|\n]*\$\{?TOKEN_FILE", code)
    assert not re.search(r"\bread\b[^\n]*<\s*\"?\$\{?TOKEN_FILE", code)
    for line in code.splitlines():
        for printed in re.findall(r"\b(?:log|echo|printf|die)\b(.*)", line):
            assert not re.search(r"\$\{?(?:token|TOKEN)\b", printed), line
    # set-token writes stdin straight to a 0600 temp file in the same directory, then renames.
    set_token = script[script.index("cmd_set_token() {") : script.index("install_package() {")]
    assert 'chmod 0600 "$tmp"' in set_token
    assert set_token.index('chmod 0600 "$tmp"') < set_token.index('>"$tmp"')
    assert 'mv -f "$tmp" "$TOKEN_FILE"' in set_token
    for sub in ("install)", "set-token)", "status)", "uninstall)"):
        assert sub in script, sub
    # Cloudflare's apt repo with its own keyring, the key's fingerprint pinned.
    assert "signed-by=${KEYRING}] https://pkg.cloudflare.com/cloudflared any main" in script
    assert re.search(r"CLOUDFLARED_KEY_FPR:-[0-9A-F]{40}\}", script)
    assert (
        'RATCHET_TUNNEL_TOKEN_FILE="${RATCHET_TUNNEL_TOKEN_FILE:-/etc/ratchet/cloudflared-token}"'
        in (VM / "lib.sh").read_text(encoding="utf-8")
    )


def test_tunnel_unit_reads_the_token_as_a_credential() -> None:
    unit = _unit("ratchet-tunnel.service")
    service = unit["Service"]
    assert service["LoadCredential"] == "tunnel-token:/etc/ratchet/cloudflared-token"
    assert service["ExecStart"].startswith("/usr/bin/cloudflared --no-autoupdate tunnel")
    assert service["ExecStart"].endswith("run --token-file %d/tunnel-token")
    assert "--metrics 127.0.0.1:" in service["ExecStart"]  # metrics on loopback only
    assert service["DynamicUser"] == "yes" and service["NoNewPrivileges"] == "yes"
    assert "Environment" not in service and "EnvironmentFile" not in service
    assert unit["Unit"]["ConditionPathExists"] == "/etc/ratchet/cloudflared-token"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_failover_dns_in_tunnel_mode_needs_no_dns_change(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "RATCHET_ENV_FILE": str(tmp_path / "absent"),
        "RATCHET_OPS_DIR": str(tmp_path / "ops"),
    }
    result = subprocess.run(
        ["bash", str(VM / "failover.sh"), "--edge", "tunnel", "dns", "203.0.113.9"],
        env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "no DNS change" in result.stderr and "edge-tunnel.sh install" in result.stderr
    assert "systemctl disable --now ratchet-tunnel" in result.stderr  # one connector at a time
    assert "A record" not in result.stderr
    help_text = (VM / "failover.sh").read_text(encoding="utf-8")
    assert "tunnel mode" in help_text and "point DNS (apex + demo)" in help_text


# --- Nebius IAM token refresh ---------------------------------------------------------------------


def test_token_timer_runs_every_four_hours_and_at_boot() -> None:
    timer = _unit("ratchet-nebius-token.timer")
    assert timer["Timer"]["OnBootSec"]
    assert timer["Timer"]["OnCalendar"] == "*-*-* 00/4:00:00 UTC"
    assert timer["Install"]["WantedBy"] == "timers.target"
    service = _unit("ratchet-nebius-token.service")
    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["ExecStart"].startswith("/opt/ratchet/deploy/vm/nebius-token.sh")
    assert "EnvironmentFile" not in service["Service"]  # compares against the file, not a copy
    assert service["Unit"]["ConditionPathExists"] == "/etc/ratchet/nebius-sa.json"


def test_bootstrap_and_deploy_enable_the_token_timer() -> None:
    bootstrap = (VM / "bootstrap.sh").read_text(encoding="utf-8").replace("\\\n", " ")
    enable = re.search(r"systemctl enable ratchet-backup\.timer[^\n]*", bootstrap)
    assert enable and "ratchet-nebius-token.timer" in enable.group(0)
    start = re.search(r"systemctl start ratchet-backup\.timer[^\n]*", bootstrap)
    assert start and "ratchet-nebius-token.timer" in start.group(0)
    assert 'nebius-token.sh" install' in bootstrap
    deploy = (VM / "deploy.sh").read_text(encoding="utf-8")
    assert "systemctl enable --quiet --now ratchet-nebius-token.timer" in deploy


def test_token_script_rewrites_one_line_and_never_prints_the_token() -> None:
    script = (VM / "nebius-token.sh").read_text(encoding="utf-8")
    assert 'env_set NEBIUS_API_KEY "$token"' in script
    assert 'NEBIUS_INSTALL_FOLDER="$NEBIUS_DIR/bin"' in script
    assert re.search(
        r'^NEBIUS_CLI_VERSION="\$\{NEBIUS_CLI_VERSION:-\d+\.\d+\.\d+\}"$', script, re.M
    )
    assert "iam get-access-token" in script and '--profile "$NEBIUS_PROFILE"' in script
    assert "systemctl try-restart" in script
    for line in script.splitlines():
        for printed in re.findall(r"\b(?:log|echo|printf|die)\b(.*)", line):
            assert "$token" not in printed and "${token}" not in printed, line
    assert "set -x" not in script


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_token_refresh_is_a_silent_noop_without_credentials(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "RATCHET_NEBIUS_SA_FILE": str(tmp_path / "absent.json"),
        "RATCHET_ENV_FILE": str(tmp_path / "env"),
        "RATCHET_OPS_DIR": str(tmp_path / "ops"),
    }
    result = subprocess.run(
        ["bash", str(VM / "nebius-token.sh"), "refresh"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


@pytest.mark.skipif(sys.platform != "linux", reason="env_set uses GNU chmod/chown --reference")
def test_env_set_replaces_in_place_and_keeps_the_mode(tmp_path: Path) -> None:
    env_file = tmp_path / "env"
    env_file.write_text("A=1\nNEBIUS_API_KEY=old\nB=2\n", encoding="utf-8")
    env_file.chmod(0o640)
    subprocess.run(
        ["bash", "-c", f'source "{VM / "lib.sh"}"; env_set NEBIUS_API_KEY "n3w.t0ken"'],
        env={"PATH": os.environ["PATH"], "RATCHET_ENV_FILE": str(env_file)},
        check=True,
    )
    assert env_file.read_text(encoding="utf-8") == "A=1\nNEBIUS_API_KEY=n3w.t0ken\nB=2\n"
    assert env_file.stat().st_mode & 0o777 == 0o640
