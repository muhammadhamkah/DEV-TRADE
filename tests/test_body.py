import pytest

from survival.body import Body, Starved
from survival.ledger import Ledger


def make(tmp_path):
    return Body.load(str(tmp_path / "body.json"), meal_price=0.5, meal_restores=50, starve_days=2)


def test_hunger_rises_with_time_and_persists(tmp_path):
    b = make(tmp_path)
    b.last_ts = 1000.0
    b.advance(now=1000.0 + 86400)  # one day of a two-day starvation clock
    assert abs(b.hunger - 50) < 1e-9
    assert abs(b.hours_until_starving - 24) < 1e-9
    b2 = make(tmp_path)
    assert abs(b2.hunger - 50) < 1e-9


def test_starves_at_100(tmp_path):
    b = make(tmp_path)
    b.last_ts = 0.0
    with pytest.raises(Starved):
        b.advance(now=3 * 86400)


def test_eating_costs_money_and_lowers_hunger(tmp_path):
    b = make(tmp_path)
    b.hunger = 80
    ledger = Ledger.open(str(tmp_path / "l.jsonl"), 10)
    out = b.eat(2, ledger)
    assert out == {"meals": 2, "cost": 1.0, "hunger": 0.0, "cash_after": 9.0}
    assert ledger.entries[-1]["kind"] == "food"


def test_cannot_eat_more_than_you_can_afford(tmp_path):
    b = make(tmp_path)
    b.hunger = 90
    ledger = Ledger.open(str(tmp_path / "l.jsonl"), 0.6)
    out = b.eat(3, ledger)  # wanted 3, could afford 1
    assert out["meals"] == 1 and abs(ledger.balance - 0.1) < 1e-9
    with pytest.raises(ValueError, match="cannot afford"):
        b.eat(1, ledger)
