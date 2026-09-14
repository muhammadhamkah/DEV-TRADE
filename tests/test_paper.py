import pytest

from survival.ledger import Ledger
from survival.paper import PaperBroker, TradeRejected


def broker(tmp_path, cash=50, **kw):
    ledger = Ledger.open(str(tmp_path / "l.jsonl"), cash)
    opts = dict(max_position_frac=0.25, max_open_positions=2, slippage_bps=50, fee_bps=0)
    opts.update(kw)
    return PaperBroker(ledger=ledger, path=str(tmp_path / "pos.json"), **opts)


def test_buy_fills_at_ask_plus_slippage(tmp_path, fake_market):
    b = broker(tmp_path)
    m = fake_market.get_market("1")
    out = b.buy(m, "Yes", 10, fake_market.quote("tok-1-yes"))
    assert out["avg_price"] == round(0.61 * 1.005, 4) and out["note"] == "full fill"
    assert b.ledger.balance == 40
    assert abs(out["shares"] - 10 / (0.61 * 1.005)) < 1e-3


def test_orders_over_the_cap_are_clipped_not_rejected(tmp_path, fake_market):
    b = broker(tmp_path)
    m = fake_market.get_market("1")
    out = b.buy(m, "Yes", 40, fake_market.quote("tok-1-yes"))
    assert out["spent"] == 12.5 and out["note"].startswith("order clipped from 40.0000 to the cap of 12.5000")
    assert b.ledger.balance == 37.5


def test_cannot_spend_more_than_cash(tmp_path, fake_market):
    b = broker(tmp_path, cash=5, max_position_frac=1.0)
    out = b.buy(fake_market.get_market("1"), "Yes", 6, fake_market.quote("tok-1-yes"))
    assert out["spent"] == 5 and b.ledger.balance == 0
    b2 = broker(tmp_path / "b2", cash=1, max_position_frac=0.25)
    with pytest.raises(TradeRejected, match="cannot afford"):
        b2.buy(fake_market.get_market("1"), "Yes", 6, fake_market.quote("tok-1-yes"))


def test_max_open_positions(tmp_path, fake_market):
    b = broker(tmp_path, max_open_positions=1)
    b.buy(fake_market.get_market("1"), "Yes", 5, fake_market.quote("tok-1-yes"))
    with pytest.raises(TradeRejected, match="maximum"):
        b.buy(fake_market.get_market("2"), "Yes", 5, fake_market.quote("tok-2-yes"))
    # adding to an existing position is still allowed
    b.buy(fake_market.get_market("1"), "Yes", 5, fake_market.quote("tok-1-yes"))


def test_sell_and_settle(tmp_path, fake_market):
    b = broker(tmp_path)
    m = fake_market.get_market("1")
    b.buy(m, "Yes", 10, fake_market.quote("tok-1-yes"))
    b.buy(fake_market.get_market("2"), "No", 4, fake_market.quote("tok-2-no"))
    out = b.sell(m, "Yes", 5, fake_market.quote("tok-1-yes"))
    assert out["price"] == round(0.59 * 0.995, 4)
    cash_before = b.ledger.balance
    fake_market.resolve("1", "Yes")
    fake_market.resolve("2", "Yes")  # our "No" loses
    events = b.settle(fake_market.get_market)
    assert {e["outcome"]: e["won"] for e in events} == {"Yes": True, "No": False}
    remaining_shares = 10 / (0.61 * 1.005) - 5
    assert abs(b.ledger.balance - (cash_before + remaining_shares)) < 1e-6
    assert b.positions == {}


def test_positions_persist(tmp_path, fake_market):
    b = broker(tmp_path)
    b.buy(fake_market.get_market("1"), "Yes", 10, fake_market.quote("tok-1-yes"))
    b2 = broker(tmp_path)
    assert list(b2.positions) == ["1:Yes"]
    marked = b2.mark(fake_market.quote)
    assert marked["net_worth"] < 50  # bought at ask, marked at bid: spread is a real cost


def test_buy_walks_the_book_and_stops_when_depth_runs_out(tmp_path, fake_market):
    b = broker(tmp_path, slippage_bps=0)
    m = fake_market.get_market("1")
    # 100 shares at 0.004, then 100 at 0.05, then nothing: a $10 order cannot fill
    quote = {"bid": 0.003, "ask": 0.004, "mid": 0.0035, "bids": [(0.003, 100)], "asks": [(0.004, 100), (0.05, 100)]}
    out = b.buy(m, "Yes", 10, quote)
    assert out["shares"] == 200 and abs(out["spent"] - (0.4 + 5.0)) < 1e-9
    assert out["note"].startswith("partial fill")
    assert abs(b.ledger.balance - (50 - 5.4)) < 1e-9
    # selling more than the bid side can absorb also stops at the book's edge
    out = b.sell(m, "Yes", None, {"bid": 0.003, "ask": 0.004, "mid": 0.0035, "bids": [(0.003, 50)], "asks": []})
    assert out["shares"] == 50 and b.positions["1:Yes"].shares == 150
