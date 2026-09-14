"""Drive the agent loop with a scripted model. No network, no API key."""
import json

import pytest

from survival.agent import Agent, AgentState, Dead
from survival.backends import Completion
from survival.config import Settings
from survival.ledger import Ledger
from survival.paper import PaperBroker
from survival.pricing import usage_cost


def block_text(t):
    return {"type": "text", "text": t}


def block_tool(name, inp, id="tu1"):
    return {"type": "tool_use", "name": name, "input": inp, "id": id}


def response(content, stop_reason, in_tok=1000, out_tok=200):
    usage = {"input_tokens": in_tok, "output_tokens": out_tok, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    return Completion(content=content, stop_reason=stop_reason, usage=usage, cost=usage_cost("claude-opus-5", usage))


class ScriptedClient:
    """A backend that returns completions in order and records every request."""

    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def make_agent(tmp_path, fake_market, client, cash=50, **overrides):
    settings = Settings(state_dir=str(tmp_path), starting_balance=cash, model="claude-opus-5", **overrides)
    ledger = Ledger.open(str(tmp_path / "ledger.jsonl"), cash)
    broker = PaperBroker(ledger=ledger, path=str(tmp_path / "positions.json"), max_position_frac=0.25, max_open_positions=6, slippage_bps=50, fee_bps=0)
    state = AgentState.load(str(tmp_path / "agent.json"), "medium")
    from survival.body import Body
    body = Body.load(str(tmp_path / "body.json"), settings.meal_price, settings.meal_restores, settings.starve_days)
    return Agent(settings=settings, ledger=ledger, broker=broker, market=fake_market, state=state, body=body, backend=client)


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
    assert client.requests[0]["effort"] == "medium"
    assert {t["name"] for t in client.requests[0]["tools"]} >= {"buy", "sell", "request_capability"}
    # rejected trades come back to the model as errors, not crashes
    tool_result = client.requests[1]["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["is_error"] is False


def test_rejected_trade_is_reported_not_raised(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("buy", {"market_id": "1", "outcome": "Nope", "usd": 4, "reason": "yolo"})], "tool_use"),
        response([block_text("ok")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.wake()
    result = json.loads(client.requests[1]["messages"][2]["content"][0]["content"])
    assert "outcome must be one of" in result["rejected"]
    assert client.requests[1]["messages"][2]["content"][0]["is_error"] is True
    assert agent.broker.positions == {}


def test_repeating_the_same_failing_call_ends_the_wakeup_with_a_scar(tmp_path, fake_market):
    bad = block_tool("buy", {"market_id": "1", "outcome": "Nope", "usd": 4, "reason": "again"})
    client = ScriptedClient([response([bad], "tool_use") for _ in range(6)])
    agent = make_agent(tmp_path, fake_market, client)
    summary = agent.wake()
    assert summary["ended_by"] == "stuck" and summary["tool_calls"] == 4
    third = json.loads(client.requests[3]["messages"][6]["content"][0]["content"])
    assert "WARNING" in third and "3 times" in third["WARNING"]
    from survival import scars
    got = scars.load(str(tmp_path / "scars.jsonl"))
    assert got[-1]["kind"] == "wasted" and "repeating the same failing call" in got[-1]["text"]


def test_sleep_ends_wakeup_and_sets_timer_when_enabled(tmp_path, fake_market):
    client = ScriptedClient([response([block_tool("sleep", {"hours": 6})], "tool_use")])
    agent = make_agent(tmp_path, fake_market, client, sleep_enabled=True)
    summary = agent.wake()
    assert summary["ended_by"] == "sleep"
    assert agent.state.sleep_until > 0
    assert client.responses == []  # no further model calls after sleeping
    assert "sleep" in {t["name"] for t in client.requests[0]["tools"]}


def test_no_sleep_by_default(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("sleep", {"hours": 6})], "tool_use"),
        response([block_text("fine")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    summary = agent.wake()
    assert "sleep" not in {t["name"] for t in client.requests[0]["tools"]}
    assert "cannot sleep" in client.requests[0]["system"]
    assert summary["ended_by"] == "end_turn" and agent.state.sleep_until == 0
    assert client.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_request_capability_is_logged(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("request_capability", {"request": "limit orders", "why": "earn the spread"})], "tool_use"),
        response([block_text("ok")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.wake()
    lines = open(tmp_path / "requests.jsonl").read().splitlines()
    assert json.loads(lines[0])["request"] == "limit orders"


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
    assert client.requests[-1]["effort"] == "low"
    assert "Wake-up #2" in client.requests[-1]["messages"][0]["content"]


def test_news_tool_is_wired(tmp_path, fake_market, monkeypatch):
    from survival import agent as agent_mod
    monkeypatch.setattr(agent_mod, "search_news", lambda q, days, limit: [{"title": f"{q} {days} {limit}"}])
    client = ScriptedClient([
        response([block_tool("search_news", {"query": "fed", "days": 2, "limit": 3})], "tool_use"),
        response([block_text("ok")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.wake()
    result = json.loads(client.requests[1]["messages"][2]["content"][0]["content"])
    assert result == [{"title": "fed 2 3"}]
    assert {t["name"] for t in client.requests[0]["tools"]} >= {"search_news", "get_market"}


def test_eat_tool_feeds_the_agent(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("eat", {"meals": 1})], "tool_use"),
        response([block_text("fed")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.body.hunger = 60
    agent.wake()
    assert abs(agent.body.hunger - 10) < 0.01 and abs(agent.ledger.balance - (50 - 0.5 - 0.02)) < 1e-9
    assert "HUNGER: 60 (hungry)" in client.requests[0]["messages"][0]["content"]
