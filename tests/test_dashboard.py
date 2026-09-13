from survival import dashboard
from survival.config import Settings
from tests.test_agent import ScriptedClient, block_text, block_tool, make_agent, response


def test_snapshot_and_render_after_a_wakeup(tmp_path, fake_market, monkeypatch):
    client = ScriptedClient([
        response([block_tool("buy", {"market_id": "1", "outcome": "Yes", "usd": 5, "reason": "edge"})], "tool_use"),
        response([block_tool("write_notes", {"text": "hold <b>1:Yes</b>"}, id="t2")], "tool_use"),
        response([block_text("done")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.wake()
    # the snapshot marks positions through Polymarket; point it at the fake
    monkeypatch.setattr(dashboard, "Polymarket", lambda *a, **k: fake_market)
    snap = dashboard.snapshot(Settings(state_dir=str(tmp_path), backend="ollama", model="m"))
    assert snap["alive"] and snap["wakeups"] == 1 and snap["cash"] == 45 - 0.03
    assert snap["positions"][0]["outcome"] == "Yes" and snap["avg_wakeup_cost"] == 0.03
    page = dashboard.render(snap)
    assert "The agent is alive" in page and "&lt;b&gt;1:Yes&lt;/b&gt;" in page  # notes are escaped
    assert snap["hunger"]["state"] == "full" and "hunger" in page
    assert "Will thing 1 happen?" in page
