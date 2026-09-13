"""Token prices in USD per million tokens: (input, output, cache_write, cache_read)."""
from __future__ import annotations

PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5": (2.0, 10.0, 2.50, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
    "claude-fable-5-1": (10.0, 50.0, 12.50, 0.25),
    "claude-fable-5": (10.0, 50.0, 12.50, 1.00),
}


def usage_cost(model: str, usage) -> float:
    """Cost of one API response. `usage` is the SDK Usage object or a dict with the same keys."""
    if model not in PRICES:
        raise KeyError(f"no price table for model {model!r}; add it to survival/pricing.py")
    inp, out, cw, cr = PRICES[model]
    get = usage.get if isinstance(usage, dict) else lambda k, d=0: getattr(usage, k, d) or 0
    return (
        get("input_tokens", 0) * inp
        + get("output_tokens", 0) * out
        + get("cache_creation_input_tokens", 0) * cw
        + get("cache_read_input_tokens", 0) * cr
    ) / 1_000_000
