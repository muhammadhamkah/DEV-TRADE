"""Read-only views of the agent's state: a terminal watcher and a tiny local web page.

Neither touches the model. They read the state files the harness writes, so they are safe to
run alongside `python -m survival run`.
"""
from __future__ import annotations

import html
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .agent import AgentState
from .body import Body
from .config import Settings
from .ledger import Ledger
from . import scars as scars_mod
from .paper import PaperBroker
from .polymarket import Polymarket


def snapshot(settings: Settings, mark: bool = True) -> dict[str, Any]:
    """Everything worth knowing, read fresh from disk. Marks positions at the live bid if it can."""
    sd = settings.state_dir
    ledger = Ledger.open(os.path.join(sd, "ledger.jsonl"), settings.starting_balance)
    broker = PaperBroker(ledger=ledger, path=os.path.join(sd, "positions.json"), max_position_frac=settings.max_position_frac,
                         max_open_positions=settings.max_open_positions, slippage_bps=settings.slippage_bps, fee_bps=settings.fee_bps)
    state = AgentState.load(os.path.join(sd, "agent.json"), settings.default_effort)
    body = Body.load(os.path.join(sd, "body.json"), settings.meal_price, settings.meal_restores, settings.starve_days)
    body.hunger = min(100.0, body.hunger + max(0.0, time.time() - body.last_ts) * body.rate_per_second)  # project, don't save
    market = Polymarket(settings.gamma_url, settings.clob_url, timeout=5)
    quote = market.quote if mark else (lambda token_id: (_ for _ in ()).throw(RuntimeError("no marking")))
    marked = broker.mark(quote)
    obituary = None
    obit_path = os.path.join(sd, "OBITUARY.json")
    if os.path.exists(obit_path):
        with open(obit_path) as fh:
            obituary = json.load(fh)
    wakeups: list[dict[str, Any]] = []
    wk_path = os.path.join(sd, "wakeups.jsonl")
    if os.path.exists(wk_path):
        with open(wk_path) as fh:
            wakeups = [json.loads(line) for line in fh if line.strip()]
    inference = -ledger.total("inference")
    requests_: list[dict[str, Any]] = []
    rq_path = os.path.join(sd, "requests.jsonl")
    if os.path.exists(rq_path):
        with open(rq_path) as fh:
            requests_ = [json.loads(line) for line in fh if line.strip()]
    return {
        "requests": requests_[::-1],
        "scars": scars_mod.load(os.path.join(sd, "scars.jsonl"))[::-1],
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "alive": not ledger.is_dead and obituary is None,
        "cash": round(ledger.balance, 4),
        "positions_value": marked["positions_value"],
        "net_worth": marked["net_worth"],
        "wakeups": state.wakeups,
        "effort": state.effort,
        "asleep_for_s": max(0, round(state.sleep_until - time.time())),
        "inference_spent": round(inference, 4),
        "avg_wakeup_cost": round(inference / state.wakeups, 4) if state.wakeups else None,
        "food_bought": round(-ledger.total("food"), 4),
        "hunger": body.describe(),
        "trading_pnl": round(ledger.total("trade_sell") + ledger.total("settlement") + ledger.total("trade_buy") + marked["positions_value"], 4),
        "model": f"{settings.model} via {settings.backend}",
        "positions": marked["positions"],
        "notes": state.notes,
        "recent_ledger": ledger.recent(15)[::-1],
        "recent_wakeups": [w["summary"] for w in wakeups[-8:]][::-1],
        "obituary": obituary,
    }


# ---- terminal ------------------------------------------------------------------------------


def watch(settings: Settings, every: int = 30) -> None:
    while True:
        snap = snapshot(settings)
        os.system("cls" if os.name == "nt" else "clear")
        status = "ALIVE" if snap["alive"] else "DEAD"
        print(f"{status}  cash ${snap['cash']:.2f}  positions ${snap['positions_value']:.2f}  net ${snap['net_worth']:.2f}   {snap['now']}")
        print(f"wake-ups {snap['wakeups']}  effort {snap['effort']}  asleep {snap['asleep_for_s'] // 3600}h{(snap['asleep_for_s'] % 3600) // 60:02d}m  "
              f"hunger {snap['hunger']['hunger']:.0f} ({snap['hunger']['state']})  thinking ${snap['inference_spent']:.3f}  food ${snap['food_bought']:.2f}  trading {snap['trading_pnl']:+.2f}")
        print()
        if snap["positions"]:
            print("POSITIONS")
            for p in snap["positions"]:
                print(f"  {p['outcome']:>4} x{p['shares']:<9.2f} @{p['avg_price']:.3f}  bid {p['bid']:.3f}  pnl {p['unrealized_pnl']:+.2f}  {p['question'][:70]}")
            print()
        print("NOTES")
        print("  " + (snap["notes"] or "(none)").replace("\n", "\n  "))
        print()
        print("RECENT")
        for e in snap["recent_ledger"][:8]:
            ts = time.strftime("%H:%M", time.localtime(e["ts"]))
            print(f"  {ts}  {e['kind']:<11} {e['amount']:+9.4f}  -> {e['balance_after']:.4f}")
        if snap["obituary"]:
            print("\nOBITUARY:", json.dumps(snap["obituary"], indent=1))
        print(f"\nrefreshing every {every}s; ctrl-c to stop")
        time.sleep(every)


# ---- web -----------------------------------------------------------------------------------

PAGE = """<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<title>survival</title>
<style>
 :root{--bg:#000;--panel:#050a05;--line:#0f3d0f;--fg:#33ff33;--dim:#1f9e1f;--faint:#0f6b0f;--red:#ff4040;--amber:#ffb000}
 body{font:14px/1.45 "SF Mono",Menlo,Consolas,"Liberation Mono",monospace;max-width:960px;margin:2rem auto;padding:0 1rem;color:var(--fg);background:var(--bg)}
 h1{font-size:1.3rem;margin:0 0 .25rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
 h1::before{content:"> "} .sub{color:var(--dim);margin-bottom:1.5rem}
 .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem;margin-bottom:1.5rem}
 .tile{background:var(--panel);border:1px solid var(--line);padding:.75rem}
 .tile b{display:block;font-size:1.5rem;font-weight:700} .tile span{color:var(--dim);font-size:.8rem}
 table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);margin-bottom:1.5rem}
 th,td{text-align:left;padding:.45rem .6rem;border-top:1px solid var(--line);font-size:.85rem;vertical-align:top}
 th{border-top:0;color:var(--dim);font-weight:400;text-transform:uppercase;font-size:.75rem;letter-spacing:.05em}
 td.n{text-align:right;font-variant-numeric:tabular-nums} .pos{color:var(--fg)} .neg{color:var(--red)}
 pre{background:var(--panel);border:1px solid var(--line);padding:.75rem;white-space:pre-wrap;color:var(--fg);font:inherit}
 .dead{background:var(--red);color:#000;padding:.5rem .75rem;margin-bottom:1rem;font-weight:700}
 h2{font-size:.8rem;color:var(--dim);margin:1.5rem 0 .5rem;font-weight:400;text-transform:uppercase;letter-spacing:.08em}
 h2::before{content:"## "} p{color:var(--dim)} ul{padding-left:1.2rem} li{margin:.2rem 0}
 .cursor::after{content:"_";animation:blink 1s steps(1) infinite} @keyframes blink{50%{opacity:0}}
</style>
<h1>{title}<span class="cursor"></span></h1><div class="sub">{model} &middot; updated {now} &middot; refreshes every 30s</div>
{dead}
<div class="tiles">
 <div class="tile"><b>${cash}</b><span>cash</span></div>
 <div class="tile"><b>${net}</b><span>net worth</span></div>
 <div class="tile"><b>{wakeups}</b><span>wake-ups &middot; effort {effort}</span></div>
 <div class="tile"><b>{asleep}</b><span>asleep</span></div>
 <div class="tile"><b>${thinking}</b><span>spent thinking{avg}</span></div>
 <div class="tile"><b class="{hungercls}">{hunger}</b><span>hunger &middot; {hungerstate}</span></div>
 <div class="tile"><b>${rent}</b><span>spent on food</span></div>
 <div class="tile"><b class="{pnlcls}">{pnl}</b><span>trading p&amp;l</span></div>
</div>
<h2>Notes (the agent's only memory)</h2><pre>{notes}</pre>
<h2>Positions</h2>{positions}
<h2>Scars (close calls it is reminded of every wake-up)</h2>{scars}
<h2>Capabilities it has asked for</h2>{requests}
<h2>Recent wake-ups</h2>{wakeups_table}
<h2>Ledger</h2>{ledger}
"""


def render(snap: dict[str, Any]) -> str:
    e = html.escape

    def money(x: float) -> str:
        return f"{x:,.2f}"

    pos_rows = "".join(
        f"<tr><td>{e(p['question'])}</td><td>{e(p['outcome'])}</td><td class=n>{p['shares']:.2f}</td><td class=n>{p['avg_price']:.3f}</td>"
        f"<td class=n>{p['bid']:.3f}</td><td class='n {'pos' if p['unrealized_pnl'] >= 0 else 'neg'}'>{p['unrealized_pnl']:+.2f}</td></tr>"
        for p in snap["positions"]
    )
    positions = (f"<table><tr><th>market</th><th>side</th><th>shares</th><th>paid</th><th>bid</th><th>p&amp;l</th></tr>{pos_rows}</table>"
                 if pos_rows else "<p>none</p>")
    wk_rows = "".join(
        f"<tr><td class=n>{w['wakeup']}</td><td class=n>{w['tool_calls']}</td><td>{e(w['ended_by'])}</td><td>{e(w['said'][:240])}</td></tr>"
        for w in snap["recent_wakeups"]
    )
    wakeups_table = (f"<table><tr><th>#</th><th>tools</th><th>ended by</th><th>what it said</th></tr>{wk_rows}</table>"
                     if wk_rows else "<p>none yet</p>")
    led_rows = "".join(
        f"<tr><td>{time.strftime('%m-%d %H:%M', time.localtime(x['ts']))}</td><td>{e(x['kind'])}</td>"
        f"<td class='n {'pos' if x['amount'] >= 0 else 'neg'}'>{x['amount']:+.4f}</td><td class=n>{x['balance_after']:.4f}</td>"
        f"<td>{e(json.dumps({k: v for k, v in x['meta'].items() if k in ('market', 'outcome', 'shares', 'price', 'won', 'effort', 'output_tokens', 'cause')}))}</td></tr>"
        for x in snap["recent_ledger"]
    )
    rq_rows = "".join(
        f"<tr><td class=n>{r['wakeup']}</td><td>{e(r['request'])}</td><td>{e(r['why'])}</td></tr>" for r in snap["requests"]
    )
    scars_html = ("<ul>" + "".join(f"<li>{e(x['text'])}</li>" for x in snap["scars"]) + "</ul>") if snap["scars"] else "<p>none yet</p>"
    requests_html = f"<table><tr><th>#</th><th>request</th><th>why</th></tr>{rq_rows}</table>" if rq_rows else "<p>none yet</p>"
    ledger = f"<table><tr><th>when</th><th>kind</th><th>amount</th><th>balance</th><th>detail</th></tr>{led_rows}</table>"
    asleep = snap["asleep_for_s"]
    return (PAGE
            .replace("{title}", "The agent is alive" if snap["alive"] else "The agent is dead")
            .replace("{model}", e(snap["model"])).replace("{now}", e(snap["now"]))
            .replace("{dead}", f"<div class=dead>Died: {e(snap['obituary']['cause'])} after {snap['obituary']['wakeups']} wake-ups</div>" if snap["obituary"] else "")
            .replace("{cash}", money(snap["cash"])).replace("{net}", money(snap["net_worth"]))
            .replace("{wakeups}", str(snap["wakeups"])).replace("{effort}", e(snap["effort"]))
            .replace("{asleep}", f"{asleep // 3600}h {(asleep % 3600) // 60:02d}m" if asleep else "no")
            .replace("{thinking}", f"{snap['inference_spent']:.3f}")
            .replace("{avg}", f" &middot; {snap['avg_wakeup_cost']:.3f}/wake-up" if snap["avg_wakeup_cost"] is not None else "")
            .replace("{rent}", f"{snap['food_bought']:.2f}")
            .replace("{hunger}", f"{snap['hunger']['hunger']:.0f}").replace("{hungerstate}", e(snap["hunger"]["state"]))
            .replace("{hungercls}", "neg" if snap["hunger"]["hunger"] >= 70 else "")
            .replace("{pnlcls}", "pos" if snap["trading_pnl"] >= 0 else "neg").replace("{pnl}", f"{snap['trading_pnl']:+.2f}")
            .replace("{notes}", e(snap["notes"]) or "(none yet)")
            .replace("{positions}", positions).replace("{requests}", requests_html).replace("{scars}", scars_html)
            .replace("{wakeups_table}", wakeups_table).replace("{ledger}", ledger))


def serve(settings: Settings, port: int = 8787) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            try:
                snap = snapshot(settings)
                if self.path.startswith("/status.json"):
                    body, ctype = json.dumps(snap, indent=1).encode(), "application/json"
                else:
                    body, ctype = render(snap).encode(), "text/html; charset=utf-8"
                self.send_response(200)
            except Exception as exc:  # show the error instead of a blank tab
                body, ctype = f"error: {exc}".encode(), "text/plain"
                self.send_response(500)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    print(f"dashboard at http://localhost:{port}  (ctrl-c to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
