"""``$HERMES_HOME`` generation for the HermesAdapter (Hermes Agent v0.22.x).

Every key below was confirmed against a real Hermes v0.22.2 one-shot run
(``docs/research/hermes-live-s3.md``, fixes C1–C7):

- **Provider (C2).** A *named* provider ``ratchet`` that owns its ``base_url`` (the proxy's
  path form ``<proxy>/r/<run_id>``), the dummy key and the ``X-Ratchet-*`` headers. The bare
  ``custom`` provider ignores ``providers.custom.{api_key,extra_headers}``.
- **Tools (C1, C3).** The MCP toolset is the bare server key ``ratchet_mock`` (``-t`` also
  filters which MCP servers are spawned); its tools reach the model as
  ``mcp__ratchet_mock__<tool>``. ``tools.tool_search.enabled: off`` exposes them directly
  instead of behind ``tool_search``/``tool_call``. Toolsets are an allowlist
  (``platform_toolsets.cli``) with every other known toolset in ``agent.disabled_toolsets``.
- **No side calls (C3).** ``auxiliary.title_generation.enabled: false``: otherwise every run
  makes an extra LLM call without the attribution headers.
- **Skills (C5).** Only the candidate skills: ``HERMES_BUNDLED_SKILLS`` points at an empty
  directory (created next to the home) and ``.no-bundled-skills`` is written as a second
  layer; the marker alone still seeds the bundled ``hermes-agent`` skill.
- **Stable prompt (C6, C7).** ``_config_version: 50`` (no migration warning on boot),
  ``model.context_length`` (no context probe), ``agent.coding_context: off`` (no git
  "Workspace" block naming the host repo) and ``agent.api_max_retries: 1`` (fast failure).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from ratchet.config.models import DEFAULT_MODELS
from ratchet.llm.client import HEADER
from ratchet.replay.harness.base import DUMMY_KEY, HarnessConfig

__all__ = [
    "CONFIG_VERSION",
    "DISABLED_TOOLSETS",
    "ENABLED_TOOLSETS",
    "MAX_TURNS",
    "MCP_SERVER",
    "MCP_TOOL_PREFIX",
    "NO_BUNDLED_SKILLS",
    "PROVIDER",
    "build_config",
    "write_home",
]

MCP_SERVER = "ratchet_mock"
MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER}__"  # + the mock server's tool name (github_label)
PROVIDER = "ratchet"
CONFIG_VERSION = 50  # hermes_cli/config_defaults.py at v0.22.2
NO_BUNDLED_SKILLS = "no-bundled-skills"  # empty dir beside the home (HERMES_BUNDLED_SKILLS)
MAX_TURNS = 25
# The only toolsets a replayed Hermes may use (allowlist): the mock MCP server, file
# (workspace, reconciled by the diff) and skills (skill_view).
ENABLED_TOOLSETS: tuple[str, ...] = (MCP_SERVER, "file", "skills")
# Every other toolset Hermes v0.22.x is known to ship (tools-reference), disabled as well.
_KNOWN_TOOLSETS: tuple[str, ...] = (
    "file",
    "skills",
    "terminal",
    "code_execution",
    "web",
    "browser",
    "x_search",
    "cronjob",
    "delegation",
    "messaging",
    "discord",
    "discord_admin",
    "image_gen",
    "video_gen",
    "tts",
    "vision",
    "video",
    "memory",
    "session_search",
    "computer_use",
    "desktop_ui",
    "connections",
    "kanban",
    "todo",
    "clarify",
    "catalog",
    "feishu",
    "yuanbao",
)
DISABLED_TOOLSETS: tuple[str, ...] = tuple(t for t in _KNOWN_TOOLSETS if t not in ENABLED_TOOLSETS)


def build_config(cfg: HarnessConfig, *, max_turns: int = MAX_TURNS) -> dict[str, object]:
    """The ``$HERMES_HOME/config.yaml`` content for one run (pure)."""
    run_headers = {
        HEADER.RUN_ID: cfg.run_id,
        HEADER.PURPOSE: "agent",
        HEADER.TIER: cfg.agent_tier,
    }
    return {
        "_config_version": CONFIG_VERSION,
        "model": {
            "default": cfg.agent_tier,  # the proxy rewrites model to the tier's id
            "provider": PROVIDER,
            "context_length": DEFAULT_MODELS[cfg.agent_tier].context_tokens,
        },
        "providers": {
            PROVIDER: {
                "base_url": f"{cfg.proxy_url.rstrip('/')}/r/{cfg.run_id}",
                "api_key": DUMMY_KEY,
                "extra_headers": run_headers,
            }
        },
        "agent": {
            "max_turns": max_turns,
            "disabled_toolsets": list(DISABLED_TOOLSETS),
            "coding_context": "off",
            "api_max_retries": 1,
        },
        "platform_toolsets": {"cli": list(ENABLED_TOOLSETS)},
        "tools": {"tool_search": {"enabled": "off"}},
        "auxiliary": {"title_generation": {"enabled": False}},
        "skills": {"creation_nudge_interval": 0},
        "timezone": "UTC",
        "mcp_servers": {
            MCP_SERVER: {
                "url": f"{cfg.mock_tools_url.rstrip('/')}/mcp",
                "headers": {HEADER.RUN_ID: cfg.run_id},
                "timeout": 120,
                "tools": {"include": ["*"], "prompts": False, "resources": False},
            }
        },
    }


def write_home(home: Path, cfg: HarnessConfig, skills_src: Path) -> Path:
    """Create an isolated ``HERMES_HOME`` (config, empty ``.env``, skills copy, no bundled
    skills) plus the empty ``<home>/../no-bundled-skills`` directory."""
    home.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(build_config(cfg), sort_keys=False)
    (home / "config.yaml").write_text(text, encoding="utf-8")
    (home / ".env").write_text("", encoding="utf-8")
    (home / ".no-bundled-skills").write_text("", encoding="utf-8")
    empty = home.parent / NO_BUNDLED_SKILLS
    if empty.exists():
        shutil.rmtree(empty)
    empty.mkdir()
    skills = home / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    if skills_src.is_dir():
        shutil.copytree(skills_src, skills, ignore=shutil.ignore_patterns(".git"))
    else:
        skills.mkdir()
    return home
