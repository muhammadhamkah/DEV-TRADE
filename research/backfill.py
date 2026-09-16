"""Pull resolved Polymarket markets and their price paths into research/data/resolved.jsonl.

    python -m research.backfill              # keep going until Gamma runs out or you stop it
    python -m research.backfill --max 2000   # cap the number of markets this run

Resumable: markets already on disk are skipped. Polite: a short pause between calls.
Each line: {id, question, outcomes, winner, end_date, volume, liquidity, history: [[ts, price], ...]}
where history is the winning-side-agnostic path of outcome 0 (the first listed outcome, usually Yes).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUT = os.path.join(DATA_DIR, "resolved.jsonl")


def _jlist(v: Any) -> list:
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


def existing_ids(path: str = OUT) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path) as fh:
        return {json.loads(line)["id"] for line in fh if line.strip()}


def fetch_closed_page(session: requests.Session, offset: int, limit: int = 100) -> list[dict]:
    resp = session.get(f"{GAMMA}/markets", params={"closed": "true", "limit": limit, "offset": offset, "order": "endDate", "ascending": "false"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_history(session: requests.Session, token_id: str) -> list[list[float]]:
    resp = session.get(f"{CLOB}/prices-history", params={"market": token_id, "interval": "max", "fidelity": 60}, timeout=30)
    resp.raise_for_status()
    return [[int(h["t"]), round(float(h["p"]), 4)] for h in resp.json().get("history", [])]


def normalize(raw: dict, history: list[list[float]]) -> dict | None:
    outcomes = [str(o) for o in _jlist(raw.get("outcomes"))]
    prices = [float(p) for p in _jlist(raw.get("outcomePrices"))]
    tokens = [str(t) for t in _jlist(raw.get("clobTokenIds"))]
    if len(outcomes) != 2 or len(prices) != 2 or not tokens:
        return None
    if max(prices) < 0.99:  # closed but not clearly resolved (or resolved 50/50); skip
        return None
    winner = outcomes[prices.index(max(prices))]
    return {
        "id": str(raw.get("id")),
        "question": raw.get("question") or "",
        "outcomes": outcomes,
        "winner": winner,
        "end_date": (raw.get("endDate") or "")[:19],
        "volume": float(raw.get("volumeNum") or raw.get("volume") or 0),
        "liquidity": float(raw.get("liquidityNum") or raw.get("liquidity") or 0),
        "history": history,
    }


def run(max_markets: int | None = None, pause: float = 0.25, session: requests.Session | None = None, out: str = OUT) -> int:
    session = session or requests.Session()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    seen = existing_ids(out)
    added = 0
    offset = 0
    while True:
        page = fetch_closed_page(session, offset)
        if not page:
            break
        with open(out, "a") as fh:
            for raw in page:
                mid = str(raw.get("id"))
                if mid in seen:
                    continue
                tokens = [str(t) for t in _jlist(raw.get("clobTokenIds"))]
                if not tokens:
                    continue
                try:
                    hist = fetch_history(session, tokens[0])
                except requests.RequestException:
                    continue
                row = normalize(raw, hist)
                if row is None or len(hist) < 2:
                    continue
                fh.write(json.dumps(row) + "\n")
                seen.add(mid)
                added += 1
                if max_markets and added >= max_markets:
                    return added
                time.sleep(pause)
        offset += len(page)
        print(f"  {added} markets saved, scanned {offset}", file=sys.stderr, flush=True)
    return added


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None)
    args = ap.parse_args()
    n = run(max_markets=args.max)
    print(f"done: {n} new markets in {OUT}")
