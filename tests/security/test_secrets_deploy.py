"""Static checks on the deployment surface and its secrets-rotation procedure.

The keys live only in ``/etc/ratchet/env`` (root, 0600) and the service's environment; the
proxy and mock-tool ports are never published; the public host hides ``/metrics`` and keeps
query strings out of its access log. These run without Docker, systemd or Caddy installed.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VM = ROOT / "deploy" / "vm"


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(VM / "systemd" / name)
    return parser


def test_serve_unit_reads_the_root_only_env_file_and_is_hardened() -> None:
    service = _unit("ratchet-serve.service")["Service"]
    assert service["EnvironmentFile"] == "/etc/ratchet/env"
    assert service["User"] == "ratchet"
    assert service["Restart"] == "always"
    expected = {
        "NoNewPrivileges": "true",
        "ProtectSystem": "strict",
        "ReadWritePaths": "/srv/ratchet",
        "PrivateTmp": "true",
        "ProtectHome": "true",
        "CapabilityBoundingSet": "",
    }
    for key, value in expected.items():
        assert service.get(key) == value, key
    alias = _unit("ratchet-serve.service")["Install"]["Alias"].split()
    assert {"ratchet-proxy.service", "ratchet-orchestrator.service"} <= set(alias)


def test_timers_match_the_deploy_schedule() -> None:
    assert _unit("ratchet-backup.timer")["Timer"]["OnCalendar"] == "*-*-* 03:00:00 UTC"
    assert "Mon" in _unit("ratchet-token-rotate.timer")["Timer"]["OnCalendar"]


def test_env_file_is_created_root_only_and_never_printed() -> None:
    bootstrap = (VM / "bootstrap.sh").read_text(encoding="utf-8")
    assert 'install -m 0600 -o root -g root "$template" "$RATCHET_ENV_FILE"' in bootstrap
    assert 'chmod 0600 "$RATCHET_ENV_FILE"' in bootstrap
    lib = (VM / "lib.sh").read_text(encoding="utf-8")
    assert 'chmod 600 "$tmp"' in lib  # env_set writes atomically with 0600 before the move
    for script in VM.glob("*.sh"):
        text = script.read_text(encoding="utf-8")
        assert "cat $RATCHET_ENV_FILE" not in text and 'cat "$RATCHET_ENV_FILE"' not in text
        assert "set -x" not in text, f"{script.name}: xtrace would echo secrets"


def test_env_template_holds_no_values_for_secrets() -> None:
    secrets = {
        "NEBIUS_TOKEN_FACTORY_KEY",
        "TAVILY_API_KEY",
        "NEBIUS_API_KEY",
        "NEBIUS_S3_ACCESS_KEY_ID",
        "NEBIUS_S3_SECRET_ACCESS_KEY",
        "RATCHET_ACTION_TOKEN_SECRET",
    }
    for line in (VM / "env.template").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key in secrets:
            assert value == "", f"{key} must be empty in the template"


@pytest.mark.parametrize("compose", ["deploy/docker-compose.yml"])
def test_compose_publishes_only_the_dashboard_on_loopback(compose: str) -> None:
    spec = yaml.safe_load((ROOT / compose).read_text(encoding="utf-8"))
    control = spec["services"]["control"]
    assert control["ports"] == ["${RATCHET_BIND_ADDR:-127.0.0.1}:${RATCHET_HOST_PORT:-8080}:8080"]
    assert sorted(control["expose"]) == ["8081", "8082"]
    assert control["read_only"] is True and control["cap_drop"] == ["ALL"]
    env = control["environment"]
    assert not {"NEBIUS_TOKEN_FACTORY_KEY", "TAVILY_API_KEY"} & set(env)
    assert spec["networks"]["sandbox"] == {"name": "ratchet-sandbox", "external": True}


def test_caddy_hides_metrics_and_strips_query_strings_from_access_logs() -> None:
    caddyfile = (VM / "Caddyfile").read_text(encoding="utf-8")
    assert 'handle /metrics {\n\t\trespond "Not found" 404' in caddyfile
    assert 'request>uri regexp \\?.*$ ""' in caddyfile
    assert "request>headers delete" in caddyfile
    assert "flush_interval -1" in caddyfile


def test_backups_never_include_the_env_file_or_the_pii_key() -> None:
    backup = (VM / "backup.sh").read_text(encoding="utf-8")
    assert "/etc/ratchet" not in backup.split("set -Eeuo pipefail", 1)[1]
    assert "--exclude='*.key'" in backup
