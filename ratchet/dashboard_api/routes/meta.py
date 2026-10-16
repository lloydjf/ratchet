"""``GET /meta`` (CONTRACTS §7) and ``GET /settings`` (operator host only)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import SecretStr

from ratchet import __version__
from ratchet.config.models import spec_for
from ratchet.config.settings import Settings
from ratchet.copy import ERROR_COPY
from ratchet.dashboard_api.deps import NewSessionDep, ServicesDep
from ratchet.dashboard_api.security import current_action_token
from ratchet.dashboard_api.views import CapView, Meta, SettingsView
from ratchet.errors import ForbiddenError
from ratchet.models import TIERS

__all__ = ["router", "bench_report_url"]

router = APIRouter(tags=["meta"])


def bench_report_url(settings: Settings) -> str | None:
    """``RATCHET_BENCH_REPORT_URL`` once Settings has it, else none."""
    value = getattr(settings, "ratchet_bench_report_url", None)
    return value if isinstance(value, str) and value else None


@router.get("/meta", response_model=Meta)
async def get_meta(services: ServicesDep, session: NewSessionDep) -> Meta:
    """Host configuration for the SPA. Issues the ``ratchet_session`` cookie on first call."""
    settings = services.settings
    return Meta(
        public_mode=settings.ratchet_public_mode,
        simulate_enabled=settings.ratchet_simulate_enabled,
        simulate_mode=settings.ratchet_simulate_mode,
        simulated_models=settings.simulated,
        action_token=current_action_token(settings) if settings.ratchet_public_mode else None,
        repo_url=settings.ratchet_repo_url or None,
        video_url=settings.ratchet_video_url or None,
        bench_report_url=bench_report_url(settings),
        version=__version__,
        harness=settings.ratchet_harness,
        runner=settings.ratchet_job_runner,
        sandbox=settings.ratchet_sandbox,
    )


@router.get("/settings", response_model=SettingsView)
async def get_settings_view(services: ServicesDep) -> SettingsView:
    """Read-only caps, runner and models. Hidden on the public host (403 ``not_public``)."""
    settings = services.settings
    if settings.ratchet_public_mode:
        raise ForbiddenError(ERROR_COPY["not_public"], code="not_public")
    caps = [CapView(purpose="all", usd_per_day=settings.ratchet_daily_usd_cap)]
    caps += [
        CapView(purpose=purpose, usd_per_day=usd)
        for purpose, usd in sorted(settings.ratchet_purpose_usd_caps.items())
    ]
    return SettingsView(
        caps=caps,
        runner=settings.ratchet_job_runner,
        harness=settings.ratchet_harness,
        sandbox=settings.ratchet_sandbox,
        webhook_configured=_configured(settings.ratchet_notify_webhook),
        models={tier: spec_for(tier, settings).model_id for tier in TIERS},
    )


def _configured(secret: SecretStr | None) -> bool:
    return secret is not None and bool(secret.get_secret_value())
