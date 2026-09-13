"""All tunables come from the environment so the container and the agent share nothing but this."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    state_dir: str = os.environ.get("SURVIVAL_STATE_DIR", "state")
    # Economy
    starting_balance: float = _f("STARTING_BALANCE", "50")
    daily_rent: float = _f("DAILY_RENT", "0.50")          # cost of living, USD per day
    tick_seconds: int = _i("TICK_SECONDS", "3600")        # how often the agent wakes
    # Inference
    model: str = os.environ.get("SURVIVAL_MODEL", "claude-opus-5")
    default_effort: str = os.environ.get("DEFAULT_EFFORT", "medium")
    max_tool_calls_per_tick: int = _i("MAX_TOOL_CALLS_PER_TICK", "12")
    max_tokens: int = _i("MAX_TOKENS", "8000")
    enable_fallbacks: bool = os.environ.get("ENABLE_FALLBACKS", "1") == "1"
    # Trading guardrails (enforced by the harness, not the prompt)
    max_position_frac: float = _f("MAX_POSITION_FRAC", "0.25")  # max share of cash per order
    max_open_positions: int = _i("MAX_OPEN_POSITIONS", "6")
    slippage_bps: float = _f("SLIPPAGE_BPS", "50")
    fee_bps: float = _f("FEE_BPS", "0")
    # Polymarket
    gamma_url: str = os.environ.get("GAMMA_URL", "https://gamma-api.polymarket.com")
    clob_url: str = os.environ.get("CLOB_URL", "https://clob.polymarket.com")
    live_trading: bool = os.environ.get("LIVE_TRADING", "0") == "1"

    @property
    def rent_per_second(self) -> float:
        return self.daily_rent / 86400.0


EFFORT_LEVELS = ("low", "medium", "high")
