"""Free opportunity scanner. Runs in the harness, no model. Sweeps the busiest markets and reports
sharp moves since the last sweep and books where Yes plus No sell for less than a dollar."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .polymarket import Polymarket


@dataclass
class Scanner:
    market: Polymarket
    every_seconds: int = 300
    move_threshold: float = 0.08   # absolute price change between sweeps that counts as a lead
    arb_threshold: float = 0.03    # 1 - (Yes + No) that counts as a lead
    last_prices: dict[str, list[float]] = field(default_factory=dict)
    arb_reported: dict[str, float] = field(default_factory=dict)  # market id -> when we last woke the agent for it
    arb_cooldown: float = 6 * 3600
    last_run: float = 0.0
    sweeps: int = 0

    def due(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.last_run >= self.every_seconds

    def sweep(self, now: float | None = None) -> list[str]:
        """Returns lead strings for the agent's wake-up reason. Empty when nothing stands out."""
        now = now or time.time()
        self.last_run = now
        try:
            markets = self.market.list_markets(limit=100)
        except Exception:
            return []
        leads: list[dict[str, Any]] = []
        for m in markets:
            if not m.prices or m.closed:
                continue
            prev = self.last_prices.get(m.id)
            if prev and len(prev) == len(m.prices):
                idx = max(range(len(m.prices)), key=lambda i: abs(m.prices[i] - prev[i]))
                delta = m.prices[idx] - prev[idx]
                settled_like = m.prices[idx] <= 0.03 or m.prices[idx] >= 0.97  # a game ending, not a mispricing
                if abs(delta) >= self.move_threshold and not settled_like:
                    leads.append({"score": abs(delta), "text": f"mover: '{m.question[:60]}' {m.outcomes[idx]} {prev[idx]:.2f}->{m.prices[idx]:.2f} (id {m.id})"})
            if len(m.prices) == 2:
                gap = 1.0 - sum(m.prices)
                if gap >= self.arb_threshold:
                    last = self.arb_reported.get(m.id)
                    if last is None or now - last >= self.arb_cooldown:
                        self.arb_reported[m.id] = now
                        leads.append({"score": gap, "text": f"possible arbitrage: '{m.question[:60]}' Yes+No = {sum(m.prices):.2f} (id {m.id}); check the asks and depth"})
                else:
                    self.arb_reported.pop(m.id, None)  # gap closed; report again if it reopens
            self.last_prices[m.id] = list(m.prices)
        self.sweeps += 1
        leads.sort(key=lambda x: -x["score"])
        return [x["text"] for x in leads[:3]]
