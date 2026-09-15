"""All tunables come from the environment so the container and the agent share nothing but this."""
from __future__ import annotations

import os
from dataclasses import dataclass


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from .env into the environment without overriding existing vars.
    Docker Compose does this itself; this is for running `python -m survival` directly."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


load_dotenv()


def _f(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    state_dir: str = os.environ.get("SURVIVAL_STATE_DIR", "state")
    # Economy
    starting_balance: float = _f("STARTING_BALANCE", "50")
    meal_price: float = _f("MEAL_PRICE", "0.50")          # what one meal costs
    meal_restores: float = _f("MEAL_RESTORES", "50")      # hunger points one meal removes (0..100 scale)
    starve_days: float = _f("STARVE_DAYS", "2")           # days from just-fed to dead with no food
    tick_seconds: int = _i("TICK_SECONDS", "1800")        # scheduled wake-up interval; 0 = wake again as soon as the last wake-up ends
    watch_seconds: int = _i("WATCH_SECONDS", "60")        # how often the harness checks the world (free)
    wake_on_move: float = _f("WAKE_ON_MOVE", "0.05")      # wake early if a held outcome's bid moves this much
    scan_seconds: int = _i("SCAN_SECONDS", "300")         # how often the free opportunity scanner sweeps the market
    scan_move: float = _f("SCAN_MOVE", "0.08")            # price jump between sweeps that counts as a lead
    scan_arb: float = _f("SCAN_ARB", "0.03")              # 1 - (Yes+No) that counts as a lead
    sleep_enabled: bool = os.environ.get("SLEEP_ENABLED", "0") == "1"  # off: it must feed itself every wake-up
    # Inference
    backend: str = os.environ.get("BACKEND", "anthropic")            # anthropic | ollama | openai
    model: str = os.environ.get("SURVIVAL_MODEL", "claude-opus-5")
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "")     # model for the ollama backend when it is the fallback
    ollama_num_ctx: int = _i("OLLAMA_NUM_CTX", "16384")
    ollama_think: bool = os.environ.get("OLLAMA_THINK", "1") == "1"
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "")     # any OpenAI-compatible endpoint
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    # What a local model's tokens cost the agent on paper (USD per million). Defaults match Opus 5.
    synthetic_price_input: float = _f("SYNTHETIC_PRICE_INPUT", "5")
    synthetic_price_output: float = _f("SYNTHETIC_PRICE_OUTPUT", "25")
    default_effort: str = os.environ.get("DEFAULT_EFFORT", "medium")
    max_tool_calls_per_tick: int = _i("MAX_TOOL_CALLS_PER_TICK", "20")
    max_tokens: int = _i("MAX_TOKENS", "3000")
    context_budget_tokens: int = _i("CONTEXT_BUDGET_TOKENS", "5000")  # prune old tool results past this (free tiers cap ~8k/min)
    fallback_backend: str = os.environ.get("FALLBACK_BACKEND", "")     # e.g. "ollama": used when the primary is rate-limited or down
    enable_fallbacks: bool = os.environ.get("ENABLE_FALLBACKS", "1") == "1"
    verbose: bool = os.environ.get("VERBOSE", "1") == "1"   # print every tool call in the run window
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
    def daily_food_cost(self) -> float:
        """What staying fed costs per day, for the briefing."""
        return 100.0 / self.starve_days / self.meal_restores * self.meal_price


EFFORT_LEVELS = ("low", "medium", "high")
