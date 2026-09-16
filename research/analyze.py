"""Measure how honest Polymarket's prices are, from research/data/resolved.jsonl.

    python -m research.analyze            # prints a report and writes research/data/base_rates.md

Questions answered:
  1. Calibration: when the market said p, how often did it happen?  (by price bucket)
  2. Longshot bias: do cheap outcomes pay off as often as their price implies?
  3. Drift: does the price 24h / 6h before close point at the answer?
  4. Expected return of buying at each price bucket and holding to settlement, after a spread guess.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

from .backfill import OUT

REPORT = os.path.join(os.path.dirname(OUT), "base_rates.md")
BUCKETS = [(0.0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.85), (0.85, 0.95), (0.95, 1.01)]


def load(path: str = OUT) -> list[dict]:
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def price_at(history: list[list[float]], seconds_before_end: float) -> float | None:
    """Price of outcome 0 at roughly `seconds_before_end` before the last history point."""
    if len(history) < 2:
        return None
    end_ts = history[-1][0]
    target = end_ts - seconds_before_end
    best = None
    for ts, p in history:
        if ts <= target:
            best = p
        else:
            break
    return best


def bucket_of(p: float) -> tuple[float, float]:
    for lo, hi in BUCKETS:
        if lo <= p < hi:
            return (lo, hi)
    return BUCKETS[-1]


def calibration(rows: list[dict], seconds_before_end: float, spread: float = 0.02) -> list[dict[str, Any]]:
    """For outcome 0 priced p at T-before-end: how often outcome 0 won, and the return of buying it then."""
    stats: dict[tuple[float, float], dict[str, float]] = defaultdict(lambda: {"n": 0, "won": 0, "p_sum": 0.0})
    for r in rows:
        p = price_at(r["history"], seconds_before_end)
        if p is None or p <= 0 or p >= 1:
            continue
        won = r["winner"] == r["outcomes"][0]
        b = bucket_of(p)
        stats[b]["n"] += 1
        stats[b]["won"] += int(won)
        stats[b]["p_sum"] += p
    out = []
    for (lo, hi), s in sorted(stats.items()):
        if s["n"] == 0:
            continue
        hit = s["won"] / s["n"]
        avg_p = s["p_sum"] / s["n"]
        cost = min(0.999, avg_p + spread)          # you pay the ask, roughly
        ret = hit / cost - 1.0                    # expected return per dollar, held to settlement
        out.append({"bucket": f"{lo:.2f}-{hi:.2f}", "n": int(s["n"]), "implied": round(avg_p, 3), "actual": round(hit, 3),
                    "edge": round(hit - avg_p, 3), "return_after_spread": round(ret, 3)})
    return out


def report(rows: list[dict]) -> str:
    lines = [f"# Polymarket base rates\n", f"Resolved binary markets analyzed: {len(rows)}\n"]
    for label, secs in (("24 hours before close", 86400), ("6 hours before close", 6 * 3600), ("7 days before close", 7 * 86400)):
        lines.append(f"\n## Price {label}\n")
        lines.append("| price bucket | n | market said | actually happened | edge | return if bought & held (after ~2c spread) |")
        lines.append("|---|---|---|---|---|---|")
        for c in calibration(rows, secs):
            lines.append(f"| {c['bucket']} | {c['n']} | {c['implied']:.1%} | {c['actual']:.1%} | {c['edge']:+.1%} | {c['return_after_spread']:+.1%} |")
    lines.append("\n## How to read this\n")
    lines.append("- 'edge' near zero means the crowd was right; buying there is a coin flip minus the spread.")
    lines.append("- A negative edge on cheap buckets is the favorite-longshot bias: cheap outcomes happen less often than their price says.")
    lines.append("- A positive return in the top buckets close to resolution means holding favorites to settlement pays, slowly.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    rows = load()
    text = report(rows)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as fh:
        fh.write(text)
    print(text)
    print(f"written to {REPORT}")
