"""deploy/vm: the apex/demo/www Caddy layout, the static site, and the Nebius token refresh.

Static checks plus two small runs (render_site.py, nebius-token.sh without credentials);
none needs Caddy, systemd or the network.
"""

from __future__ import annotations

import base64
import configparser
import hashlib
import importlib.util
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


def test_caddyfile_serves_apex_www_and_demo() -> None:
    blocks = _blocks((VM / "Caddyfile").read_text(encoding="utf-8"))
    apex, www, demo = (
        blocks["{$RATCHET_SITE_ADDRESS}"],
        blocks["www.{$RATCHET_SITE_ADDRESS}"],
        blocks["{$RATCHET_DEMO_ADDRESS}"],
    )
    assert "root * /var/www/ratchet-site" in apex and "file_server" in apex
    assert "reverse_proxy" not in apex  # the apex never reaches the app
    assert "redir https://{$RATCHET_SITE_ADDRESS}{uri} permanent" in www  # 301
    assert "reverse_proxy {$RATCHET_UPSTREAM_ADDR:127.0.0.1:8080}" in demo
    assert "/var/www/ratchet-fallback" in demo and 'respond "Not found" 404' in demo
    for block in (apex, www, demo):
        assert "import security_headers" in block


def test_every_site_keeps_the_security_headers_and_a_strict_csp() -> None:
    caddyfile = (VM / "Caddyfile").read_text(encoding="utf-8")
    snippet = _blocks(caddyfile)["(security_headers)"]
    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
    ):
        assert header in snippet, header
    blocks = _blocks(caddyfile)
    for address in ("{$RATCHET_SITE_ADDRESS}", "{$RATCHET_DEMO_ADDRESS}"):
        csp = re.search(r'Content-Security-Policy "([^"]+)"', blocks[address])
        assert csp, address
        script_src = re.search(r"script-src ([^;]+)", csp.group(1))
        assert script_src and "unsafe-inline" not in script_src.group(1), address
        assert "frame-ancestors 'none'" in csp.group(1)
    apex = blocks["{$RATCHET_SITE_ADDRESS}"]
    assert "{vars.docs_script_src}" in apex  # docs theme scripts by hash only
    assert "import /etc/caddy/ratchet.d/site/*.caddy" in apex
    assert "import /etc/caddy/ratchet.d/redirects/*.caddy" in caddyfile
    assert caddyfile.count("import access_log") == 2


def test_bootstrap_writes_both_addresses_into_the_caddy_dropin() -> None:
    bootstrap = (VM / "bootstrap.sh").read_text(encoding="utf-8")
    assert "--site-url" in bootstrap and "--public-url" in bootstrap
    assert "Environment=RATCHET_SITE_ADDRESS=${apex}" in bootstrap
    assert "Environment=RATCHET_DEMO_ADDRESS=${demo}" in bootstrap
    assert "render_site" in bootstrap and "render_caddy_redirects" in bootstrap
    deploy = (VM / "deploy.sh").read_text(encoding="utf-8")
    assert "render_site ||" in deploy and "install_caddyfile" in deploy
    template = (VM / "env.template").read_text(encoding="utf-8").splitlines()
    for key in ("RATCHET_PUBLIC_URL=", "RATCHET_SITE_URL=", "RATCHET_ALT_ADDRESSES="):
        assert key in template, key


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
