"""USD per 1M tokens, per tier (re-verified in docs/VERIFIED.md).

Cost math: usd = prompt_tokens * in + completion_tokens * out.
"""

from __future__ import annotations

from dataclasses import dataclass

from ratchet.models import Tier

__all__ = [
    "Price",
    "PRICES",
    "EMBED_PRICE_PER_M",
    "cost_usd",
    "counterfactual_ultra_usd",
    "embed_cost_usd",
]


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float


PRICES: dict[Tier, Price] = {
    "nano": Price(0.06, 0.24),
    "super": Price(0.30, 0.90),
    "ultra": Price(1.00, 3.00),
}


def cost_usd(tier: Tier, prompt_tokens: int, completion_tokens: int) -> float:
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("token counts cannot be negative")
    price = PRICES[tier]
    return (prompt_tokens * price.input_per_m + completion_tokens * price.output_per_m) / 1_000_000


def counterfactual_ultra_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """What the same tokens would have cost judged entirely on ultra."""
    return cost_usd("ultra", prompt_tokens, completion_tokens)


# Qwen/Qwen3-Embedding-8B on Token Factory (docs/research/nebius-tavily.md).
EMBED_PRICE_PER_M = 0.01


def embed_cost_usd(prompt_tokens: int) -> float:
    return prompt_tokens * EMBED_PRICE_PER_M / 1_000_000
