"""Append-only money ledger. The agent never gets a handle to this; only the harness writes it."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Ledger:
    path: str
    balance: float = 0.0
    entries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def open(cls, path: str, starting_balance: float) -> "Ledger":
        ledger = cls(path=path)
        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    if line.strip():
                        ledger.entries.append(json.loads(line))
            if ledger.entries:
                ledger.balance = ledger.entries[-1]["balance_after"]
                return ledger
        ledger._append("deposit", starting_balance, {"note": "initial stake"})
        return ledger

    def _append(self, kind: str, amount: float, meta: dict[str, Any] | None = None) -> float:
        self.balance = round(self.balance + amount, 6)
        entry = {
            "ts": time.time(),
            "kind": kind,
            "amount": round(amount, 6),
            "balance_after": self.balance,
            "meta": meta or {},
        }
        self.entries.append(entry)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
        return self.balance

    def charge(self, kind: str, amount: float, meta: dict[str, Any] | None = None) -> float:
        """Take money out. Charges are never refused; going below zero is how the agent dies."""
        if amount < 0:
            raise ValueError("charge amount must be non-negative")
        return self._append(kind, -amount, meta)

    def credit(self, kind: str, amount: float, meta: dict[str, Any] | None = None) -> float:
        if amount < 0:
            raise ValueError("credit amount must be non-negative")
        return self._append(kind, amount, meta)

    @property
    def is_dead(self) -> bool:
        return self.balance <= 0

    def total(self, kind: str) -> float:
        return round(sum(e["amount"] for e in self.entries if e["kind"] == kind), 6)

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        return self.entries[-n:]
