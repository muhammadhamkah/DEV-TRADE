import json

from survival import main as m
from tests.test_agent import ScriptedClient, block_text, block_tool, homework, make_agent, response


def test_watch_wakes_on_position_move_and_watchlist(tmp_path, fake_market):
    client = ScriptedClient(homework() + [
        response([block_tool("buy", {"market_id": "1", "outcome": "Yes", "usd": 5, "reason": "r"})], "tool_use"),
        response([block_tool("watch_market", {"market_id": "2", "outcome": "No", "wake_if_below": None, "wake_if_above": 0.5, "remove": False}, id="w1")], "tool_use"),
        response([block_text("set")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.wake()
    assert agent.state.watchlist[0]["token_id"] == "tok-2-no"
    reasons, bids = m.watch(agent, {})
    assert reasons == [] and bids["tok-1-yes"] == 0.59
    fake_market.books["tok-1-yes"] = fake_market.book(0.50, 0.52)     # position dropped 9 cents
    fake_market.books["tok-2-no"] = fake_market.book(0.55, 0.57)      # watch crossed above 0.5
    reasons, _ = m.watch(agent, bids)
    assert any("moved from 0.590 to 0.500" in r for r in reasons)
    assert any("watch triggered" in r and "above your 0.5" in r for r in reasons)


def test_watch_wakes_when_very_hungry_once(tmp_path, fake_market):
    agent = make_agent(tmp_path, fake_market, ScriptedClient([]))
    agent.body.hunger = 75
    reasons, bids = m.watch(agent, {})
    assert any("very hungry" in r for r in reasons)
    reasons, _ = m.watch(agent, bids)
    assert reasons == []  # not nagged every minute


def test_wake_reason_reaches_the_briefing(tmp_path, fake_market):
    client = ScriptedClient([response([block_text("ok")], "end_turn")])
    agent = make_agent(tmp_path, fake_market, client)
    summary = m.one_tick(agent, "watch triggered: thing")
    assert summary["reason"] == "watch triggered: thing"
    assert "WHY YOU ARE AWAKE: watch triggered: thing" in client.requests[0]["messages"][0]["content"]


def test_run_loop_starts_and_stops_on_kill_file(tmp_path, fake_market, monkeypatch):
    """Drive the real run() loop once so its imports and wiring are exercised."""
    import os
    from survival.config import Settings
    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(m.time, "sleep", fake_sleep)
    monkeypatch.setattr(m, "Polymarket", lambda *a, **k: fake_market)
    monkeypatch.setattr(m, "make_backend", lambda s: ScriptedClient([response([block_text("hi")], "end_turn")] * 5), raising=False)
    from survival import agent as agent_mod
    monkeypatch.setattr(agent_mod, "make_backend", lambda s: ScriptedClient([response([block_text("hi")], "end_turn")] * 5))
    settings = Settings(state_dir=str(tmp_path), backend="ollama", model="m", watch_seconds=1, tick_seconds=0)
    try:
        m.run(settings)
    except KeyboardInterrupt:
        pass
    assert os.path.exists(os.path.join(str(tmp_path), "wakeups.jsonl"))
