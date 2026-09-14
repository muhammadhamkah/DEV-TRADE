import json

from survival import scars
from tests.test_agent import ScriptedClient, block_text, block_tool, make_agent, response


def test_vitals_scar_once_per_wakeup(tmp_path):
    p = str(tmp_path / "scars.jsonl")
    scars.check_vitals(p, 4, hunger=90, cash=10, meal_price=0.5)
    scars.check_vitals(p, 4, hunger=95, cash=10, meal_price=0.5)
    scars.check_vitals(p, 5, hunger=10, cash=0.7, meal_price=0.5)
    got = scars.load(p)
    assert [s["kind"] for s in got] == ["starving", "broke"]


def test_big_loss_scar_threshold(tmp_path):
    p = str(tmp_path / "scars.jsonl")
    scars.check_loss(p, 1, amount_lost=1.0, cash_before=50, what="x")   # 2%: no scar
    scars.check_loss(p, 2, amount_lost=12.0, cash_before=50, what="y")  # 24%: scar
    assert len(scars.load(p)) == 1 and "24%" in scars.load(p)[0]["text"]


def test_briefing_carries_forecast_and_scars(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_text("a")], "end_turn"),
        response([block_text("b")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    agent.body.hunger = 88
    agent.wake()
    first = client.requests[0]["messages"][0]["content"]
    assert "SCARS" in first and "hunger reached 88" in first
    assert "DEATH FORECAST" in first and "$0.00/day thinking" in first  # food alone gives a date on wake-up one
    agent2 = make_agent(tmp_path, fake_market, client)
    agent2.wake()
    second = client.requests[1]["messages"][0]["content"]
    assert "DEATH FORECAST" in second and "$0.00/day thinking" not in second  # now priced from real inference
    assert "hunger reached 88" in second  # scars persist


def test_selling_at_a_big_loss_leaves_a_scar(tmp_path, fake_market):
    client = ScriptedClient([
        response([block_tool("buy", {"market_id": "2", "outcome": "Yes", "usd": 12, "reason": "r"})], "tool_use"),
        response([block_tool("sell", {"market_id": "2", "outcome": "Yes", "shares": None, "reason": "r"}, id="t2")], "tool_use"),
        response([block_text("done")], "end_turn"),
    ])
    agent = make_agent(tmp_path, fake_market, client)
    fake_market.books["tok-2-yes"] = fake_market.book(0.10, 0.91)  # bid collapses after we buy at 0.91
    agent.wake()
    got = scars.load(str(tmp_path / "scars.jsonl"))
    assert len(got) == 1 and got[0]["kind"] == "big_loss"
