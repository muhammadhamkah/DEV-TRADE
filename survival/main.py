"""Daemon and CLI.

    python -m survival run      # live forever (or until dead)
    python -m survival tick     # one wake-up, then exit
    python -m survival status   # print the books once
    python -m survival watch    # print the books every 30s
    python -m survival dashboard  # local web page at http://localhost:8787
    python -m survival reset    # wipe state (asks for confirmation)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

from .agent import Agent, AgentState, Dead
from .body import Body, Starved
from .config import Settings
from .ledger import Ledger
from .paper import PaperBroker
from .polymarket import Polymarket


def build(settings: Settings, backend=None) -> Agent:
    if settings.live_trading:
        raise SystemExit("LIVE_TRADING=1 is not implemented. This harness is paper-only for now.")
    sd = settings.state_dir
    ledger = Ledger.open(os.path.join(sd, "ledger.jsonl"), settings.starting_balance)
    broker = PaperBroker(
        ledger=ledger,
        path=os.path.join(sd, "positions.json"),
        max_position_frac=settings.max_position_frac,
        max_open_positions=settings.max_open_positions,
        slippage_bps=settings.slippage_bps,
        fee_bps=settings.fee_bps,
    )
    market = Polymarket(settings.gamma_url, settings.clob_url)
    state = AgentState.load(os.path.join(sd, "agent.json"), settings.default_effort)
    body = Body.load(os.path.join(sd, "body.json"), settings.meal_price, settings.meal_restores, settings.starve_days)
    return Agent(settings=settings, ledger=ledger, broker=broker, market=market, state=state, body=body, backend=backend)


def die(agent: Agent, cause: str) -> None:
    agent.ledger.charge("death", 0.0, {"cause": cause})
    obit = {
        "died_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "cause": cause,
        "wakeups": agent.state.wakeups,
        "inference_spent": -agent.ledger.total("inference"),
        "food_bought": -agent.ledger.total("food"),
        "hunger": round(agent.body.hunger, 1),
        "trading_pnl": agent.ledger.total("trade_sell") + agent.ledger.total("settlement") + agent.ledger.total("trade_buy"),
        "last_notes": agent.state.notes,
    }
    with open(os.path.join(agent.settings.state_dir, "OBITUARY.json"), "w") as fh:
        json.dump(obit, fh, indent=1)
    print("DEAD:", json.dumps(obit, indent=1))


def one_tick(agent: Agent) -> dict | None:
    """Hunger, settlement, then a wake-up unless asleep. Returns the wake-up summary or None."""
    try:
        agent.body.advance()
    except Starved as exc:
        die(agent, str(exc))
        return None
    for ev in agent.broker.settle(agent.market.get_market):
        print("SETTLED:", json.dumps(ev))
    if agent.settings.sleep_enabled and time.time() < agent.state.sleep_until:
        return None
    try:
        summary = agent.wake()
    except Dead as exc:
        die(agent, str(exc))
        return None
    print("WAKEUP:", json.dumps(summary))
    with open(os.path.join(agent.settings.state_dir, "wakeups.jsonl"), "a") as fh:
        fh.write(json.dumps({"summary": summary, "tools": agent.log}) + "\n")
    agent.log.clear()
    return summary


def run(settings: Settings) -> None:
    agent = build(settings)
    kill = os.path.join(settings.state_dir, "KILL")
    while True:
        if os.path.exists(kill):
            print("KILL file present; frozen. Remove it to resume.")
        elif os.path.exists(os.path.join(settings.state_dir, "OBITUARY.json")):
            print("Agent is dead. Run `python -m survival reset` to start over.")
            return
        else:
            one_tick(agent)
            if agent.ledger.is_dead or os.path.exists(os.path.join(settings.state_dir, "OBITUARY.json")):
                return
        time.sleep(settings.tick_seconds)


def status(settings: Settings) -> None:
    from .dashboard import snapshot
    snap = snapshot(settings)
    snap.pop("recent_ledger")
    snap.pop("recent_wakeups")
    print(json.dumps(snap, indent=1))


def reset(settings: Settings) -> None:
    if os.path.isdir(settings.state_dir):
        if input(f"Delete {settings.state_dir}? [y/N] ").lower() != "y":
            return
        shutil.rmtree(settings.state_dir)
    print("state cleared")


def main(argv: list[str]) -> None:
    settings = Settings()
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        run(settings)
    elif cmd == "tick":
        one_tick(build(settings))
    elif cmd == "status":
        status(settings)
    elif cmd == "watch":
        from .dashboard import watch
        watch(settings, int(argv[1]) if len(argv) > 1 else 30)
    elif cmd == "dashboard":
        from .dashboard import serve
        serve(settings, int(argv[1]) if len(argv) > 1 else 8787)
    elif cmd == "reset":
        reset(settings)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
