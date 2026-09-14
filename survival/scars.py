"""Close calls, recorded by the harness and shown to the agent forever after.

A stateless agent cannot remember fear. It can be made to read about the times it nearly died.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any


def load(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def record(path: str, wakeup: int, kind: str, text: str) -> None:
    """Append a scar unless the same kind was already recorded on this wake-up."""
    for s in load(path):
        if s["wakeup"] == wakeup and s["kind"] == kind:
            return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps({"ts": time.time(), "wakeup": wakeup, "kind": kind, "text": text}) + "\n")


def check_vitals(path: str, wakeup: int, hunger: float, cash: float, meal_price: float) -> None:
    if hunger >= 85:
        record(path, wakeup, "starving", f"Wake-up {wakeup}: hunger reached {hunger:.0f}. You were hours from starving.")
    if cash < 2 * meal_price:
        record(path, wakeup, "broke", f"Wake-up {wakeup}: cash fell to ${cash:.2f}, not enough for two meals.")


def check_loss(path: str, wakeup: int, amount_lost: float, cash_before: float, what: str) -> None:
    if cash_before > 0 and amount_lost >= 0.2 * cash_before:
        record(path, wakeup, "big_loss", f"Wake-up {wakeup}: lost ${amount_lost:.2f} ({amount_lost / cash_before:.0%} of your cash) on {what}.")
