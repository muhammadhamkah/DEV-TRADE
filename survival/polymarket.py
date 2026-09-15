"""Read-only Polymarket client: Gamma for market metadata, CLOB for live prices.

Both are public and need no key. Field shapes follow the public Gamma /markets schema, where
list-valued fields (outcomes, outcomePrices, clobTokenIds) arrive as JSON-encoded strings.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests


def _jlist(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []


@dataclass
class Market:
    id: str
    question: str
    outcomes: list[str]
    prices: list[float]          # last known price per outcome, 0..1
    token_ids: list[str]         # CLOB token id per outcome
    end_date: str | None
    volume_24h: float
    liquidity: float
    closed: bool
    resolved_outcome: str | None  # set once the market has paid out
    rules: str = ""               # resolution criteria, from the market description

    @classmethod
    def from_gamma(cls, raw: dict[str, Any]) -> "Market":
        outcomes = [str(o) for o in _jlist(raw.get("outcomes"))]
        prices = [float(p) for p in _jlist(raw.get("outcomePrices"))]
        closed = bool(raw.get("closed"))
        resolved = None
        if closed and prices and max(prices) >= 0.99:
            resolved = outcomes[prices.index(max(prices))]
        return cls(
            id=str(raw.get("id")),
            question=raw.get("question") or raw.get("title") or "",
            outcomes=outcomes,
            prices=prices,
            token_ids=[str(t) for t in _jlist(raw.get("clobTokenIds"))],
            end_date=raw.get("endDate"),
            volume_24h=float(raw.get("volume24hr") or 0),
            liquidity=float(raw.get("liquidity") or 0),
            closed=closed,
            resolved_outcome=resolved,
            rules=(raw.get("description") or "")[:700],
        )

    def token_for(self, outcome: str) -> str:
        idx = self.outcomes.index(outcome)
        return self.token_ids[idx]

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "question": self.question,
            "outcomes": {o: round(p, 3) for o, p in zip(self.outcomes, self.prices)},
            "ends": (self.end_date or "")[:10],
            "vol24h": int(self.volume_24h),
            "liq": int(self.liquidity),
        }
        if self.closed:
            out["closed"] = True
            out["resolved_outcome"] = self.resolved_outcome
        return out

    def detail(self) -> dict[str, Any]:
        out = self.summary()
        out["rules"] = self.rules
        return out


class Polymarket:
    def __init__(self, gamma_url: str, clob_url: str, session: requests.Session | None = None, timeout: float = 15.0):
        self.gamma_url = gamma_url.rstrip("/")
        self.clob_url = clob_url.rstrip("/")
        self.http = session or requests.Session()
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.http.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def list_markets(self, limit: int = 20, query: str | None = None) -> list[Market]:
        """Busiest active markets. With a query, searches the busiest 100 by keyword: markets matching
        every word first, then markets matching any word."""
        limit = max(1, min(limit, 100))
        params: dict[str, Any] = {
            "active": "true",
            "closed": "false",
            "limit": 100 if query else limit,
            "order": "volume24hr",
            "ascending": "false",
        }
        raw = self._get(f"{self.gamma_url}/markets", params)
        markets = [m for m in (Market.from_gamma(x) for x in raw) if m.token_ids and m.outcomes]
        if query:
            words = [w for w in query.lower().replace(",", " ").split() if len(w) > 2]
            if words:
                text = {m.id: (m.question + " " + m.rules).lower() for m in markets}
                all_words = [m for m in markets if all(w in text[m.id] for w in words)]
                any_words = [m for m in markets if m not in all_words and any(w in text[m.id] for w in words)]
                markets = all_words + any_words
        return markets[:limit]

    def get_market(self, market_id: str) -> Market:
        return Market.from_gamma(self._get(f"{self.gamma_url}/markets/{market_id}"))

    def price_history(self, token_id: str, days: int = 7, points: int = 24) -> list[dict[str, Any]]:
        """Price of a token over the last `days`, downsampled to about `points` readings."""
        days = max(1, min(int(days), 90))
        interval = "1d" if days <= 1 else "1w" if days <= 7 else "1m" if days <= 30 else "max"
        fidelity = max(10, int(days * 24 * 60 / max(points, 2)))
        raw = self._get(f"{self.clob_url}/prices-history", {"market": token_id, "interval": interval, "fidelity": fidelity})
        hist = raw.get("history") or []
        if len(hist) > points:
            step = len(hist) / points
            hist = [hist[int(i * step)] for i in range(points)] + [hist[-1]]
        import time as _t
        return [{"t": _t.strftime("%m-%d %H:%M", _t.gmtime(h["t"])), "p": round(float(h["p"]), 3)} for h in hist]

    def quote(self, token_id: str) -> dict[str, Any]:
        """Order book for a token: best bid/ask plus the full ladder as (price, size) levels."""
        book = self._get(f"{self.clob_url}/book", {"token_id": token_id})
        bids = sorted(((float(b["price"]), float(b.get("size") or 0)) for b in book.get("bids", [])), key=lambda x: -x[0])
        asks = sorted(((float(a["price"]), float(a.get("size") or 0)) for a in book.get("asks", [])), key=lambda x: x[0])
        bid = bids[0][0] if bids else 0.0
        ask = asks[0][0] if asks else 1.0
        return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 4), "bids": bids, "asks": asks}
