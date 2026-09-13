from survival.polymarket import Market, Polymarket
from tests.conftest import gamma_market


def test_market_parses_json_string_fields():
    m = Market.from_gamma(gamma_market("7"))
    assert m.outcomes == ["Yes", "No"] and m.prices == [0.6, 0.4]
    assert m.token_for("No") == "tok-7-no"
    assert m.resolved_outcome is None


def test_resolved_market_detected():
    m = Market.from_gamma(gamma_market("7", closed=True, prices=("0", "1")))
    assert m.closed and m.resolved_outcome == "No"


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self.routes[url])


def test_client_builds_quote_from_book():
    session = FakeSession({
        "https://gamma/markets": [gamma_market("1"), {"id": "bad", "outcomes": "[]", "clobTokenIds": "[]"}],
        "https://clob/book": {"bids": [{"price": "0.58", "size": "10"}, {"price": "0.59", "size": "5"}], "asks": [{"price": "0.62", "size": "7"}, {"price": "0.61", "size": "3"}]},
    })
    pm = Polymarket("https://gamma", "https://clob", session=session)
    markets = pm.list_markets(limit=5)
    assert [m.id for m in markets] == ["1"]  # markets without tokens are dropped
    assert session.calls[0][1]["closed"] == "false"
    q = pm.quote("tok")
    assert (q["bid"], q["ask"], q["mid"]) == (0.59, 0.61, 0.6)
    assert q["asks"] == [(0.61, 3.0), (0.62, 7.0)] and q["bids"] == [(0.59, 5.0), (0.58, 10.0)]


def test_price_history_downsamples():
    hist = [{"t": 1_700_000_000 + i * 3600, "p": i / 100} for i in range(100)]
    session = FakeSession({"https://clob/prices-history": {"history": hist}})
    pm = Polymarket("https://gamma", "https://clob", session=session)
    out = pm.price_history("tok", days=7, points=10)
    assert session.calls[0][1]["interval"] == "1w"
    assert len(out) == 11 and out[-1]["p"] == 0.99 and out[0]["p"] == 0.0
