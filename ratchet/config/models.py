"""Tier -> model id. The ONLY place a model string may appear (a project convention).

Values are from the Token Factory catalog as recorded in docs/VERIFIED.md; each can be
overridden by RATCHET_MODEL_<TIER> without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass

from ratchet.config.settings import Settings, get_settings
from ratchet.models import Tier

__all__ = [
    "ModelSpec",
    "DEFAULT_MODELS",
    "SIM_MODEL_PREFIX",
    "model_for",
    "spec_for",
    "embed_model",
    "thinking_params",
]

SIM_MODEL_PREFIX = "ratchet-sim/"


@dataclass(frozen=True)
class ModelSpec:
    tier: Tier
    model_id: str
    context_tokens: int
    timeout_s: float  # 60 s nano, 120 s super, 300 s ultra
    supports_thinking: bool


DEFAULT_MODELS: dict[Tier, ModelSpec] = {
    # Ids from https://tokenfactory.nebius.com/api/public/models_info (docs/research).
    "nano": ModelSpec("nano", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", 262_000, 60.0, True),
    "super": ModelSpec("super", "nvidia/nemotron-3-super-120b-a12b", 256_000, 120.0, True),
    "ultra": ModelSpec("ultra", "nvidia/Nemotron-3-Ultra-550b-a55b", 1_000_000, 300.0, True),
}

DEFAULT_EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"


def _override(settings: Settings, tier: Tier) -> str | None:
    return {
        "nano": settings.ratchet_model_nano,
        "super": settings.ratchet_model_super,
        "ultra": settings.ratchet_model_ultra,
    }[tier]


def spec_for(tier: Tier, settings: Settings | None = None) -> ModelSpec:
    settings = settings or get_settings()
    base = DEFAULT_MODELS[tier]
    model_id = _override(settings, tier) or base.model_id
    if settings.simulated:
        model_id = f"{SIM_MODEL_PREFIX}{tier}"
    return ModelSpec(tier, model_id, base.context_tokens, base.timeout_s, base.supports_thinking)


def model_for(tier: Tier, settings: Settings | None = None) -> str:
    return spec_for(tier, settings).model_id


def embed_model(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return settings.ratchet_model_embed or DEFAULT_EMBED_MODEL


def thinking_params(
    tier: Tier, enabled: bool, budget: int | None, settings: Settings | None = None
) -> dict[str, object]:
    """Request-body fields that switch Nemotron reasoning on or off.

    Token Factory does not document a thinking toggle; NVIDIA's convention for Nemotron 3 is
    ``chat_template_kwargs.enable_thinking``. ``reasoning_effort`` is the documented Token
    Factory alternative. The style is a setting so a live test can switch it without code
    (docs/VERIFIED.md). The budget is enforced by the caller through ``max_tokens``.
    """
    settings = settings or get_settings()
    if not spec_for(tier, settings).supports_thinking:
        return {}
    style = settings.ratchet_thinking_style
    if style == "chat_template_kwargs":
        return {"chat_template_kwargs": {"enable_thinking": enabled}}
    if style == "reasoning_effort":
        if not enabled:
            return {"reasoning_effort": "none"}
        return {"reasoning_effort": "high" if (budget or 0) >= 4000 else "medium"}
    return {}
