import json
import os
import time

from survival import main as m
from survival.config import Settings
from tests.test_agent import ScriptedClient, make_agent, response, block_text


def test_rent_accrues_by_wall_clock(tmp_path, fake_market):
    agent = make_agent(tmp_path, fake_market, ScriptedClient([]), daily_rent=0.48)
    m.charge_rent(agent, now=1000.0)             # first call only stamps the clock
    assert agent.ledger.balance == 50
    due = m.charge_rent(agent, now=1000.0 + 3600)  # one hour later
    assert abs(due - 0.02) < 1e-9
    assert abs(agent.ledger.balance - 49.98) < 1e-9


def test_one_tick_skips_wakeup_while_asleep(tmp_path, fake_market):
    client = ScriptedClient([response([block_text("hi")], "end_turn")])
    agent = make_agent(tmp_path, fake_market, client)
    agent.state.sleep_until = time.time() + 3600
    assert m.one_tick(agent) is None
    assert client.requests == []


def test_death_writes_obituary(tmp_path, fake_market):
    client = ScriptedClient([response([block_text("x")], "end_turn", in_tok=10_000_000)])
    agent = make_agent(tmp_path, fake_market, client, cash=1)
    assert m.one_tick(agent) is None
    obit = json.load(open(os.path.join(str(tmp_path), "OBITUARY.json")))
    assert obit["cause"].startswith("balance exhausted")
    assert agent.ledger.entries[-1]["kind"] == "death"


def test_live_trading_refuses_to_start(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        m.build(Settings(state_dir=str(tmp_path), live_trading=True))
