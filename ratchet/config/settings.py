"""The single Settings object.

Nothing outside this module reads ``os.environ``. Every field maps 1:1 to an env var of
the same name (case-insensitive) documented in ``.env.example``. Settings are
validated at process start; a bad value fails fast with the field name.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ratchet.models import Tier

__all__ = [
    "Settings",
    "get_settings",
    "configure",
    "reset_settings",
    "DEV_ACTION_TOKEN_SECRET",
    "public_mode_refusal",
]

DEV_ACTION_TOKEN_SECRET = "dev-only-change-me"  # the shipped default; refused on a public host

# A release wheel carries the built SPA here (scripts/build_wheel.py); a checkout does not.
PACKAGED_DASHBOARD_DIST = Path(__file__).resolve().parent.parent / "_dashboard_dist"


def default_dashboard_dist() -> Path:
    """The SPA shipped inside the installed package, else ``./dashboard/dist`` (checkout)."""
    if (PACKAGED_DASHBOARD_DIST / "index.html").is_file():
        return PACKAGED_DASHBOARD_DIST
    return Path("./dashboard/dist")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Token Factory (proxy) -------------------------------------------------------
    nebius_token_factory_key: SecretStr | None = Field(
        None, description="Token Factory API key; read by the proxy only, never a sandbox."
    )
    nebius_tf_base_url: str = Field(
        "https://api.tokenfactory.nebius.com/v1/",
        description="OpenAI-compatible Token Factory base URL (see docs/VERIFIED.md).",
    )
    ratchet_model_nano: str | None = Field(None, description="Override the nano model id.")
    ratchet_model_super: str | None = Field(None, description="Override the super model id.")
    ratchet_model_ultra: str | None = Field(None, description="Override the ultra model id.")
    ratchet_model_embed: str | None = Field(None, description="Override the embedding model id.")
    ratchet_upstream: Literal["live", "sim"] = Field(
        "sim",
        description="live = Token Factory; sim = the in-repo offline simulator (ADR 0018). "
        "Verdicts produced by the simulator carry a visible 'Simulated models' badge.",
    )
    ratchet_agent_tier: Tier = Field("super", description="Tier the agent runs on.")
    ratchet_agent_thinking: Literal["off", "on", "default"] = Field(
        "off",
        description="Reasoning for agent calls when the harness sends no preference. Nemotron 3 "
        "thinks by default; off keeps replays cheap, fast and closer to deterministic.",
    )

    # --- Local service URLs -------------------------------------------------------------
    ratchet_proxy_url: str = Field("http://127.0.0.1:8081/v1", description="Capture proxy.")
    ratchet_mock_tools_url: str = Field("http://127.0.0.1:8082", description="Mock tools.")
    ratchet_dashboard_host: str = Field("127.0.0.1", description="Dashboard bind host.")
    ratchet_dashboard_port: int = Field(8080, description="Dashboard + API port.")
    ratchet_proxy_port: int = Field(8081, description="Proxy port.")
    ratchet_mock_tools_port: int = Field(8082, description="Mock tool server port.")

    # --- Storage and skills -------------------------------------------------------------
    ratchet_skills_repo: Path = Field(Path("./var/skills"), description="Skills git repo.")
    ratchet_data_dir: Path = Field(Path("./var/data"), description="Store root.")
    ratchet_pii_key_file: Path | None = Field(
        None, description="Fernet key for the PII token map; defaults to <data>/../pii.key."
    )

    # --- Replay ----------------------------------------------------------------------------
    ratchet_job_runner: Literal["nebius", "local"] = Field("local", description="Job runner.")
    ratchet_local_runner_mode: Literal["process", "docker"] = Field(
        "process", description="How LocalJobRunner launches a worker (ADR 0017)."
    )
    ratchet_sandbox: Literal["openshell", "docker", "process"] = Field(
        "process", description="Replay sandbox. process is dev-only; refused in public mode."
    )
    ratchet_harness: Literal["hermes", "minimal"] = Field("minimal", description="Harness adapter.")
    ratchet_hermes_version: str | None = Field(
        None,
        description="Hermes version baked at install time; when unset the adapter "
        "runs `hermes --version` once per process.",
    )
    ratchet_worker_image: str = Field("ratchet-worker:dev", description="Worker image.")
    ratchet_tf_proxy_upstream: str | None = Field(
        None, description="Worker sidecar upstream; defaults to nebius_tf_base_url."
    )
    ratchet_cassette_refresh: bool = Field(False, description="Overwrite cassettes in record.")
    ratchet_batch_size: int = Field(10, description="Goldens per replay batch.")
    ratchet_simulate_batch_size: int = Field(
        4, description="Goldens per batch for Simulate runs: more parallel workers, faster."
    )
    ratchet_max_traces_per_edit: int = Field(40, description="Selection cap.")
    ratchet_replay_timeout_s: int = Field(600, description="Batch wall clock.")
    ratchet_replay_retry_once: bool = Field(
        True, description="Re-dispatch replays that crashed (harness/sandbox/worker) once."
    )

    # --- Nebius Serverless Jobs -----------------------------------------------------------
    nebius_api_key: SecretStr | None = Field(None, description="Nebius IAM token for Jobs.")
    nebius_project_id: str | None = Field(None, description="Nebius project (parent) id.")
    nebius_region: str = Field("eu-north1", description="Nebius region.")
    nebius_jobs_api_url: str | None = Field(None, description="Override Jobs API endpoint.")
    nebius_s3_endpoint: str | None = Field(None, description="Object storage endpoint.")
    nebius_s3_bucket: str | None = Field(None, description="Bucket for job inputs/outputs.")
    nebius_s3_access_key_id: SecretStr | None = Field(None, description="Object storage key.")
    nebius_s3_secret_access_key: SecretStr | None = Field(None, description="Object storage.")
    nebius_job_preset: str | None = Field(None, description="Jobs compute preset/platform.")

    # --- Tavily ------------------------------------------------------------------------------
    tavily_api_key: SecretStr | None = Field(None, description="Tavily key; judge only.")
    ratchet_tavily_mode: Literal["live", "fixtures"] = Field(
        "fixtures", description="fixtures = recorded responses (bench, offline)."
    )
    ratchet_tavily_fixtures_dir: Path = Field(
        Path("./tests/fixtures/tavily"), description="Recorded Tavily responses."
    )

    # --- Judge -----------------------------------------------------------------------------
    ratchet_feature_drift: bool = Field(True, description="Tavily drift disambiguation.")
    ratchet_feature_control_replays: bool = Field(
        True,
        description="ADR 0023: replay contested pairs once more with the unchanged skill.",
    )
    ratchet_feature_responses_api: bool = Field(False, description="/v1/responses passthrough.")
    ratchet_thinking_style: Literal["chat_template_kwargs", "reasoning_effort", "none"] = Field(
        "chat_template_kwargs", description="How thinking is toggled (docs/VERIFIED.md)."
    )
    ratchet_ultra_reasoning_budget: int = Field(4000, description="Ultra reasoning budget.")
    ratchet_structured_mode: Literal["auto", "json_schema", "json_object", "prompt"] = Field(
        "auto",
        description="Judge response_format mode; auto tries json_schema, then json_object, "
        "then prompt-only JSON, remembering the degraded mode per tier (verified: json_schema "
        "works live on all tiers; the fallback stays as defence in depth).",
    )
    ratchet_selection_embedder: Literal["local", "tokenfactory"] = Field(
        "local", description="Embedding for trigger-neighborhood selection."
    )
    ratchet_neighborhood_threshold: float | None = Field(
        None, description="Cosine threshold; default depends on the embedder."
    )
    ratchet_cache_ttl_days: int = Field(7, description="Model response cache TTL.")

    # --- Budget ------------------------------------------------------------------------
    ratchet_daily_usd_cap: float = Field(10.0, description="All purposes, per UTC day.")
    ratchet_bench_usd_cap: float = Field(
        12.0,
        description="bench purpose hard cap, enforced all-time (cumulative bench spend in the "
        "bench store), not per day.",
    )
    ratchet_purpose_usd_caps: dict[str, float] = Field(
        default_factory=dict, description='JSON, e.g. {"agent": 5, "adjudicate": 3}.'
    )

    # --- Dashboard / public host -----------------------------------------------------------
    ratchet_public_url: str = Field("http://127.0.0.1:8080", description="Public base URL.")
    ratchet_public_mode: bool = Field(False, description="Disables bless/record.")
    ratchet_simulate_enabled: bool = Field(
        False,
        description="Serve the Simulate HTTP routes (true on the demo host only). "
        "`ratchet simulate` on the operator's machine runs regardless.",
    )
    ratchet_simulate_mode: Literal["live", "replayed"] = Field(
        "live", description="replayed = stored verdicts, 'Recorded run' badge."
    )
    ratchet_simulate_max_sessions: int = Field(5, description="Concurrent Simulate sessions.")
    ratchet_simulate_reset_s: int = Field(600, description="Scenario auto-revert.")
    ratchet_simulate_max_queue: int = Field(
        20, description="Queued Simulate runs before 429 simulate_busy."
    )
    ratchet_sse_max_per_client: int = Field(3, description="Concurrent SSE streams per IP.")
    ratchet_sse_max_streams: int = Field(100, description="Concurrent SSE streams in total.")
    ratchet_api_max_body_bytes: int = Field(
        64 * 1024, description="Largest request body the API accepts (413 above)."
    )
    ratchet_action_token_secret: SecretStr = Field(
        SecretStr(DEV_ACTION_TOKEN_SECRET),
        description="HMAC secret for the daily action token.",
    )
    ratchet_scenarios_dir: Path = Field(Path("./scenarios"), description="Seeded scenarios.")
    ratchet_dashboard_dist: Path = Field(
        default_factory=default_dashboard_dist,
        description="Built SPA.",
    )
    ratchet_repo_url: str = Field("https://github.com/lloydjf/ratchet", description="Repo.")
    ratchet_video_url: str = Field("", description="Demo video URL for the landing footer.")
    ratchet_bench_report_url: str | None = Field(
        None, description="Link to bench/results/<date>/report.md on GitHub (README, Costs page)."
    )
    ratchet_forwarded_allow_ips: str = Field(
        "127.0.0.1", description="Reverse proxies trusted for X-Forwarded-For (rate limits)."
    )

    # --- Notifications and ops ---------------------------------------------------------------
    ratchet_notify_webhook: SecretStr | None = Field(
        None, description="Slack/Telegram webhook (the URL carries its own credential)."
    )
    ratchet_debug: bool = Field(False, description="Content logging to a local file only.")
    ratchet_log_level: str = Field("INFO", description="Log level.")
    ratchet_max_edits_in_flight: int = Field(2, description="Concurrent edits.")

    @field_validator("nebius_tf_base_url")
    @classmethod
    def _trailing_slash(cls, value: str) -> str:
        return value if value.endswith("/") else value + "/"

    @property
    def db_path(self) -> Path:
        return self.ratchet_data_dir / "ratchet.db"

    @property
    def pii_key_path(self) -> Path:
        if self.ratchet_pii_key_file is not None:
            return self.ratchet_pii_key_file
        return self.ratchet_data_dir.parent / "pii.key"

    @property
    def simulated(self) -> bool:
        return self.ratchet_upstream == "sim"

    def require_live_key(self) -> SecretStr:
        from ratchet.errors import ConfigError

        if self.nebius_token_factory_key is None:
            raise ConfigError(
                "NEBIUS_TOKEN_FACTORY_KEY is not set; set it or use RATCHET_UPSTREAM=sim",
                code="config_error",
                context={"field": "nebius_token_factory_key"},
            )
        return self.nebius_token_factory_key


def public_mode_refusal(settings: Settings) -> str | None:
    """Why this configuration must not serve a public host, or ``None``.

    On a public host the action token is the only guard on Simulate and the demo actions;
    with the shipped default secret anyone could mint it."""
    if not settings.ratchet_public_mode:
        return None
    secret = settings.ratchet_action_token_secret.get_secret_value()
    if secret == DEV_ACTION_TOKEN_SECRET or len(secret) < 16:
        return (
            "RATCHET_PUBLIC_MODE is on but RATCHET_ACTION_TOKEN_SECRET is the default or "
            "shorter than 16 characters; set a long random secret"
        )
    return None


_override: Settings | None = None


@lru_cache(maxsize=1)
def _load() -> Settings:
    return Settings()


def get_settings() -> Settings:
    return _override if _override is not None else _load()


def configure(settings: Settings) -> Settings:
    """Install explicit settings (tests, CLI flags). Returns them for chaining."""
    global _override
    _override = settings
    return settings


def reset_settings() -> None:
    global _override
    _override = None
    _load.cache_clear()
