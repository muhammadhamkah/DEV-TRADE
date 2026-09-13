import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from survival.polymarket import Market


def gamma_market(id="1", closed=False, prices=("0.60", "0.40")):
    return {
        "id": id,
        "question": f"Will thing {id} happen?",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(list(prices)),
        "clobTokenIds": json.dumps([f"tok-{id}-yes", f"tok-{id}-no"]),
        "endDate": "2026-12-31T00:00:00Z",
        "volume24hr": 1234.5,
        "liquidity": 9000,
        "closed": closed,
    }


class FakeMarket:
    """Stands in for Polymarket. Markets and books are plain dicts you can mutate in a test."""

    def __init__(self):
        self.raw = {"1": gamma_market("1"), "2": gamma_market("2", prices=("0.90", "0.10"))}
        self.books = {tok: self.book(bid, ask) for tok, (bid, ask) in {
            "tok-1-yes": (0.59, 0.61), "tok-1-no": (0.39, 0.41), "tok-2-yes": (0.89, 0.91), "tok-2-no": (0.09, 0.11),
        }.items()}

    @staticmethod
    def book(bid, ask, depth=1_000_000):
        return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 4), "bids": [(bid, depth)], "asks": [(ask, depth)]}

    def list_markets(self, limit=20, query=None):
        ms = [Market.from_gamma(r) for r in self.raw.values()]
        if query:
            ms = [m for m in ms if query.lower() in m.question.lower()]
        return ms[:limit]

    def get_market(self, market_id):
        return Market.from_gamma(self.raw[market_id])

    def quote(self, token_id):
        return self.books[token_id]

    def resolve(self, market_id, winner):
        self.raw[market_id]["closed"] = True
        self.raw[market_id]["outcomePrices"] = json.dumps(["1", "0"] if winner == "Yes" else ["0", "1"])


@pytest.fixture
def fake_market():
    return FakeMarket()
