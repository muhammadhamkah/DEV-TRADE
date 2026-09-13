"""Drive the agent loop with a scripted model. No network, no API key."""
import json
from types import SimpleNamespace

import pytest

from survival.agent import Agent, AgentState, Dead
from survival.config import Settings
from survival.ledger import Ledger
from survival.paper import PaperBroker


def block_text(t):
    return SimpleNamespace(type="text", text=t)


def block_tool(name, inp, id="tu1"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=id)


def response(content, stop_reason, in_tok=1000, out_tok=200):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )


class ScriptedClient:
    """Returns responses in order; records every request so tests can assert on them."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def make_agent(tmp_path, fake_market, client, cash=50, **overrides):
    settings = Settings(state_dir=str(tmp_path), starting_balance=cash, model="claude-opus-5", **overrides)
    ledger = Ledger.open(str(tmp_path / "ledger.jsonl"), cash)
    broker = PaperBroker(ledger=ledger, path=str(tmp_path / "pos.json"), max_position_frac=0.25, max_open_positions=6, slippage_bps=50, fee_bps=0)
    state = AgentState.load(str(tmp_path / "agent.json"), "medium")
    return Agent(settings=settings, ledger=ledger, broker=broker, market=fake_market, state=state, client=client)


def test_wakeup_charges_inference_and_executes_tools(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("buy", {"market_id": "1", "outcome": "Yes", "usd": 5, "reason": "edge"})], "tool_use"),
        response([block_tool("write_notes", {"text": "bought 1:Yes"}, id="tu2")], "tool_use"),
        response([block_text("done")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    summary = agent.wake()
    assert summary["tool_calls"] == 2 and summary["ended_by"] == "end_turn" and summary["said"] == "done"
    # three calls at 1000 in / 200 out on opus-5: 3 * (0.005 + 0.005) = 0.03, plus the $5 buy
    assert abs(agent.ledger.balance - (50 - 5 - 0.03)) < 1e-9
    assert list(agent.broker.positions) == ["1:Yes"]
    assert agent.state.notes == "bought 1:Yes"
    # the model was asked with the chosen effort and the tool list
    assert client.requests[0]["output_config"] == {"effort": "medium"}
    assert {t["name"] for t in client.requests[0]["tools"]} >= {"buy", "sell", "sleep"}
    # rejected trades come back to the model as errors, not crashes
    tool_result = client.requests[1]["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["is_error"] is False


def test_rejected_trade_is_reported_not_raised(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("buy", {"market_id": "1", "outcome": "Yes", "usd": 40, "reason": "yolo"})], "tool_use"),
        response([block_text("ok")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.wake()
    result = json.loads(client.requests[1]["messages"][2]["content"][0]["content"])
    assert "position cap" in result["rejected"]
    assert client.requests[1]["messages"][2]["content"][0]["is_error"] is True
    assert agent.broker.positions == {}


def test_sleep_ends_wakeup_and_sets_timer(tmp_path, fake_market):
    client = ScriptedClient([response([block_tool("sleep", {"hours": 6})], "tool_use")])
    agent = make_agent(tmp_path, fake_market, client)
    summary = agent.wake()
    assert summary["ended_by"] == "sleep"
    assert agent.state.sleep_until > 0
    assert client.responses == []  # no further model calls after sleeping


def test_thinking_costs_money_and_can_kill(tmp_path, fake_market):
    client = ScriptedClient([response([block_text("thinking hard")], "end_turn", in_tok=1_000_000, out_tok=1_000_000)])
    agent = make_agent(tmp_path, fake_market, client, cash=10)
    with pytest.raises(Dead):
        agent.wake()
    assert agent.ledger.is_dead


def test_tool_budget_stops_runaway_loops(tmp_path, fake_market):
    client = ScriptedClient([response([block_tool("get_status", {}, id=f"t{i}")], "tool_use") for i in range(5)])
    agent = make_agent(tmp_path, fake_market, client, max_tool_calls_per_tick=3)
    summary = agent.wake()
    assert summary["ended_by"] == "tool_budget" and summary["tool_calls"] == 3


def test_effort_choice_persists_to_next_wakeup(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("set_effort", {"level": "low"})], "tool_use"),
        response([block_text("ok")], "end_turn"),
        response([block_text("ok")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.wake()
    agent2 = make_agent(tmp_path, fake_market, client)
    agent2.wake()
    assert client.requests[-1]["output_config"] == {"effort": "low"}
    assert "Wake-up #2" in client.requests[-1]["messages"][0]["content"]
