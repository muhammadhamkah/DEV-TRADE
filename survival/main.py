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
from . import scars
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


def one_tick(agent: Agent, reason: str = "scheduled wake-up") -> dict | None:
    """Hunger, settlement, then a wake-up unless asleep. Returns the wake-up summary or None."""
    try:
        agent.body.advance()
    except Starved as exc:
        die(agent, str(exc))
        return None
    settled = settle(agent)
    if settled:
        reason = f"{reason}; settled: " + "; ".join(f"{e['outcome']} {'WON' if e['won'] else 'LOST'} on '{e.get('question', '')[:50]}'" for e in settled if "won" in e)
    if agent.settings.sleep_enabled and time.time() < agent.state.sleep_until:
        return None
    try:
        summary = agent.wake(reason)
    except Dead as exc:
        die(agent, str(exc))
        return None
    except Exception as exc:  # brain or network unreachable: skip this tick, try again next time
        print(f"WAKEUP FAILED ({type(exc).__name__}): {str(exc)[:200]}")
        print("The agent could not think this tick. Is the model backend running? Retrying next tick.")
        agent.state.save()
        return None
    print("WAKEUP:", json.dumps(summary))
    with open(os.path.join(agent.settings.state_dir, "wakeups.jsonl"), "a") as fh:
        fh.write(json.dumps({"summary": summary, "tools": agent.log}) + "\n")
    agent.log.clear()
    return summary


def settle(agent: Agent) -> list[dict]:
    cash_before = agent.ledger.balance
    events = agent.broker.settle(agent.market.get_market)
    for ev in events:
        print("SETTLED:", json.dumps(ev))
        if ev.get("won") is False:
            # a lost settlement pays 0; what it cost is in the ledger's trade_buy entries for that market
            spent = -sum(e["amount"] for e in agent.ledger.entries if e["kind"] == "trade_buy" and e["meta"].get("market") == ev["market"] and e["meta"].get("outcome") == ev["outcome"])
            scars.check_loss(agent.scars_path(), agent.state.wakeups, spent, cash_before + spent, f"'{ev.get('question', ev['market'])[:60]}' ({ev['outcome']})")
    return events


def watch(agent: Agent, last_bids: dict[str, float]) -> tuple[list[str], dict[str, float]]:
    """One free look at the world. Returns reasons to wake the agent (maybe none) and the new bids."""
    reasons: list[str] = []
    bids: dict[str, float] = {}
    s = agent.settings
    for pos in agent.broker.positions.values():
        try:
            bid = agent.market.quote(pos.token_id)["bid"]
        except Exception:
            continue
        bids[pos.token_id] = bid
        prev = last_bids.get(pos.token_id)
        if prev is not None and abs(bid - prev) >= s.wake_on_move:
            reasons.append(f"your position '{pos.question[:50]}' ({pos.outcome}) moved from {prev:.3f} to {bid:.3f}")
    for w in agent.state.watchlist:
        try:
            q = agent.market.quote(w["token_id"])
        except Exception:
            continue
        mid = q["mid"]
        bids[w["token_id"]] = mid
        if w.get("below") is not None and mid <= w["below"]:
            reasons.append(f"watch triggered: '{w['question'][:50]}' ({w['outcome']}) is {mid:.3f}, below your {w['below']}")
        elif w.get("above") is not None and mid >= w["above"]:
            reasons.append(f"watch triggered: '{w['question'][:50]}' ({w['outcome']}) is {mid:.3f}, above your {w['above']}")
    if agent.body.hunger >= 70 and last_bids.get("__hunger_warned__", 0) < 70:
        reasons.append(f"you are very hungry (hunger {agent.body.hunger:.0f})")
    bids["__hunger_warned__"] = agent.body.hunger
    return reasons, bids


def run(settings: Settings) -> None:
    """Watch the world every WATCH_SECONDS for free; wake the agent on schedule or when something happens."""
    agent = build(settings)
    kill = os.path.join(settings.state_dir, "KILL")
    obit = os.path.join(settings.state_dir, "OBITUARY.json")
    last_wake = 0.0
    last_bids: dict[str, float] = {}
    while True:
        if os.path.exists(kill):
            print("KILL file present; frozen. Remove it to resume.")
            time.sleep(settings.watch_seconds)
            continue
        if os.path.exists(obit):
            print("Agent is dead. Run `python -m survival reset` to start over.")
            return
        try:
            agent.body.advance()
        except Starved as exc:
            die(agent, str(exc))
            return
        settled = settle(agent)
        reasons, last_bids = watch(agent, last_bids)
        if settled:
            reasons.append("a market you held just settled")
        due = time.time() - last_wake >= settings.tick_seconds
        if due:
            reasons.insert(0, "scheduled wake-up")
        if reasons:
            one_tick(agent, "; ".join(reasons))
            last_wake = time.time()
            if agent.ledger.is_dead or os.path.exists(obit):
                return
        else:
            marks = ", ".join(f"{p.outcome} {last_bids.get(p.token_id, p.avg_price):.3f}" for p in agent.broker.positions.values())
            nxt = max(0, int(settings.tick_seconds - (time.time() - last_wake)))
            print(f"{time.strftime('%H:%M:%S')}  watching  cash {agent.ledger.balance:.2f}  hunger {agent.body.hunger:.0f}  "
                  f"positions [{marks or 'none'}]  watches {len(agent.state.watchlist)}  next scheduled wake in {nxt // 60}m", flush=True)
        time.sleep(settings.watch_seconds)


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
