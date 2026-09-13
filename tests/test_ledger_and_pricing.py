from survival.ledger import Ledger
from survival.pricing import usage_cost


def test_ledger_persists_and_reloads(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    l1 = Ledger.open(p, 50)
    l1.charge("inference", 0.25, {"x": 1})
    l1.credit("trade_sell", 3)
    assert l1.balance == 52.75
    l2 = Ledger.open(p, 999)  # starting balance ignored when a ledger exists
    assert l2.balance == 52.75
    assert len(l2.entries) == 3
    assert l2.total("inference") == -0.25


def test_death_when_balance_hits_zero(tmp_path):
    l = Ledger.open(str(tmp_path / "l.jsonl"), 1)
    assert not l.is_dead
    l.charge("rent", 1)
    assert l.is_dead


def test_usage_cost_opus_5():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 1_000_000}
    assert abs(usage_cost("claude-opus-5", usage) - (5 + 2.5 + 0.5)) < 1e-9


def test_unknown_model_refuses_to_guess():
    import pytest
    with pytest.raises(KeyError):
        usage_cost("claude-made-up", {"input_tokens": 1})
