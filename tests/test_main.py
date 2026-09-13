import json
import os
import time

from survival import main as m
from survival.config import Settings
from tests.test_agent import ScriptedClient, make_agent, response, block_text


def test_starvation_writes_obituary_without_touching_cash(tmp_path, fake_market):
    client = ScriptedClient([response([block_text("x")], "end_turn")])
    agent = make_agent(tmp_path, fake_market, client)
    agent.body.hunger = 99.9
    agent.body.last_ts = time.time() - 7200
    assert m.one_tick(agent) is None
    obit = json.load(open(os.path.join(str(tmp_path), "OBITUARY.json")))
    assert obit["cause"].startswith("starved") and agent.ledger.balance == 50
    assert client.requests == []


def test_one_tick_skips_wakeup_while_asleep_only_if_sleep_enabled(tmp_path, fake_market):
    client = ScriptedClient([response([block_text("hi")], "end_turn")])
    agent = make_agent(tmp_path, fake_market, client, sleep_enabled=True)
    agent.state.sleep_until = time.time() + 3600
    assert m.one_tick(agent) is None
    assert client.requests == []
    # with sleep disabled, a leftover timer from an earlier life is ignored
    agent2 = make_agent(tmp_path, fake_market, client)
    agent2.state.sleep_until = time.time() + 3600
    assert m.one_tick(agent2) is not None
    assert len(client.requests) == 1


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
