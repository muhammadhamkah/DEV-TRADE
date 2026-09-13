"""Hunger. The harness owns this file; the agent can only act on it through the eat tool.

Hunger runs from 0 (just ate) to 100 (dead). It rises with wall-clock time. A meal costs money
and knocks it down. Nothing is automatic: an agent that never eats starves, and one that is
broke cannot eat.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from .ledger import Ledger


class Starved(Exception):
    pass


@dataclass
class Body:
    path: str
    meal_price: float
    meal_restores: float   # hunger points one meal removes
    starve_days: float     # days from 0 to 100 with no food
    hunger: float = 0.0
    last_ts: float = 0.0

    @classmethod
    def load(cls, path: str, meal_price: float, meal_restores: float, starve_days: float) -> "Body":
        body = cls(path=path, meal_price=meal_price, meal_restores=meal_restores, starve_days=starve_days, last_ts=time.time())
        if os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
            body.hunger = float(data.get("hunger", 0.0))
            body.last_ts = float(data.get("last_ts", body.last_ts))
        return body

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump({"hunger": round(self.hunger, 4), "last_ts": self.last_ts}, fh)

    @property
    def rate_per_second(self) -> float:
        return 100.0 / (self.starve_days * 86400.0)

    def advance(self, now: float | None = None) -> float:
        """Let time pass. Returns hunger gained. Raises Starved at 100."""
        now = now or time.time()
        gained = max(0.0, now - self.last_ts) * self.rate_per_second
        self.hunger = min(100.0, self.hunger + gained)
        self.last_ts = now
        self.save()
        if self.hunger >= 100.0:
            raise Starved("starved: hunger reached 100")
        return gained

    def eat(self, meals: int, ledger: Ledger) -> dict:
        meals = max(1, min(int(meals), 5))
        cost = round(meals * self.meal_price, 6)
        if cost > ledger.balance:
            affordable = int(ledger.balance // self.meal_price)
            if affordable <= 0:
                raise ValueError(f"cannot afford a meal: a meal costs {self.meal_price:.2f}, you have {ledger.balance:.4f}")
            meals, cost = affordable, round(affordable * self.meal_price, 6)
        ledger.charge("food", cost, {"meals": meals, "hunger_before": round(self.hunger, 2)})
        self.hunger = max(0.0, self.hunger - meals * self.meal_restores)
        self.save()
        return {"meals": meals, "cost": cost, "hunger": round(self.hunger, 1), "cash_after": ledger.balance}

    @property
    def hours_until_starving(self) -> float:
        return (100.0 - self.hunger) / self.rate_per_second / 3600.0

    def describe(self) -> dict:
        h = self.hunger
        word = "full" if h < 20 else "fine" if h < 45 else "hungry" if h < 70 else "very hungry" if h < 90 else "STARVING"
        return {"hunger": round(h, 1), "state": word, "hours_until_death": round(self.hours_until_starving, 1), "meal_price": self.meal_price}
