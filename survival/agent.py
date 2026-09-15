"""One wake-up of the agent: a fresh conversation, a bounded tool loop, every API call charged."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .backends import Backend, Completion, make_backend
from .body import Body
from .config import EFFORT_LEVELS, Settings
from .ledger import Ledger
from .news import search_news
from .paper import PaperBroker, TradeRejected
from .polymarket import Polymarket
from . import scars
from .tools import TOOLS

PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "SYSTEM.md")


class Dead(Exception):
    pass


@dataclass
class AgentState:
    """Things the agent is allowed to change about itself. Persisted between wake-ups."""
    path: str
    effort: str = "medium"
    notes: str = ""
    sleep_until: float = 0.0
    wakeups: int = 0
    watchlist: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.watchlist is None:
            self.watchlist = []

    @classmethod
    def load(cls, path: str, default_effort: str) -> "AgentState":
        state = cls(path=path, effort=default_effort)
        if os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
            for k, v in data.items():
                if hasattr(state, k) and k != "path":
                    setattr(state, k, v)
        return state

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump({"effort": self.effort, "notes": self.notes, "sleep_until": self.sleep_until, "wakeups": self.wakeups, "watchlist": self.watchlist}, fh, indent=1)


@dataclass
class Agent:
    settings: Settings
    ledger: Ledger
    broker: PaperBroker
    market: Polymarket
    state: AgentState
    body: Body | None = None
    backend: Backend | None = None  # built from settings when omitted; tests pass a scripted one
    log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.backend is None:
            self.backend = make_backend(self.settings)
        if self.body is None:
            self.body = Body.load(os.path.join(self.settings.state_dir, "body.json"), self.settings.meal_price, self.settings.meal_restores, self.settings.starve_days)
        with open(PROMPT_PATH) as fh:
            self.system_prompt = fh.read().rstrip() + "\n\n" + self.situation()
        self.tools = [t for t in TOOLS if self.settings.sleep_enabled or t["name"] != "sleep"]

    def situation(self) -> str:
        """The rules of this particular life, generated from settings so the prompt file stays generic."""
        s = self.settings
        lines = [
            "## Your situation",
            f"- You get hungry. Hunger rises from 0 to 100 over {s.starve_days:g} days with no food; at 100 you die. A meal costs ${s.meal_price:.2f} and removes {s.meal_restores:g} points. Staying fed costs about ${s.daily_food_cost:.2f} per day. Nobody feeds you; you must call eat, and you cannot eat what you cannot afford.",
            f"- You wake every {s.tick_seconds // 3600 if s.tick_seconds >= 3600 else s.tick_seconds // 60} "
            f"{'hour(s)' if s.tick_seconds >= 3600 else 'minute(s)'}. Every wake-up costs you inference money before you have made a single decision.",
            f"- Orders are capped at {s.max_position_frac:.0%} of your cash and you may hold at most {s.max_open_positions} positions. The harness enforces this.",
        ]
        if s.sleep_enabled:
            lines.append("- You may sleep to skip wake-ups. Sleeping costs no inference, but you still get hungry.")
        else:
            lines.append("- You cannot sleep. There is no way to skip a wake-up. The only way to spend less on thinking is to think at lower effort and to act with fewer tool calls. The only way to survive is to earn more than you eat.")
        return "\n".join(lines)

    # ---- wake-up -------------------------------------------------------------------------

    def scars_path(self) -> str:
        return os.path.join(self.settings.state_dir, "scars.jsonl")

    def forecast(self) -> str:
        """When cash runs out at the current burn. Positions do not count; they are not cash."""
        s = self.settings
        charges = [e for e in self.ledger.entries if e["kind"] == "inference"]
        recent = {}
        for e in charges:
            w = e["meta"].get("wakeup")
            recent[w] = recent.get(w, 0.0) - e["amount"]
        last = list(recent.values())[-5:]
        per_wakeup = sum(last) / len(last) if last else 0.0
        # Measured burn: inference over the last day (or whatever history exists, at least 10 minutes), scaled to a day.
        now = time.time()
        window_start = max(now - 86400.0, charges[0]["ts"] if charges else now)
        window = now - window_start
        if window >= 600:
            thinking_per_day = -sum(e["amount"] for e in charges if e["ts"] >= window_start) * 86400.0 / window
        else:
            thinking_per_day = per_wakeup * 86400.0 / max(s.tick_seconds, 600)
        burn = thinking_per_day + s.daily_food_cost
        if burn <= 0:
            return ""
        days = self.ledger.balance / burn
        when = time.strftime("%Y-%m-%d", time.gmtime(time.time() + days * 86400))
        return (f"DEATH FORECAST: at your current burn (${thinking_per_day:.2f}/day thinking at ~${per_wakeup:.3f} per wake-up, "
                f"${s.daily_food_cost:.2f}/day food) your cash runs out around {when}, in {days:.1f} days. "
                f"Positions do not count until sold. Only income or cheaper thinking moves this date.")

    def briefing(self, reason: str = "scheduled wake-up") -> str:
        status = self.tool_get_status({})
        inference = -self.ledger.total("inference")
        avg_cost = inference / self.state.wakeups if self.state.wakeups else None
        hunger = self.body.describe()
        scars.check_vitals(self.scars_path(), self.state.wakeups, self.body.hunger, self.ledger.balance, self.settings.meal_price)
        scar_lines = [s_["text"] for s_ in scars.load(self.scars_path())[-5:]]
        lines = [
            f"Wake-up #{self.state.wakeups}. Time: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.",
            f"WHY YOU ARE AWAKE: {reason}",
            f"Model: {self.settings.model} via {self.backend.name}. Effort: {self.state.effort}.",
            f"HUNGER: {hunger['hunger']} ({hunger['state']}). You die in {hunger['hours_until_death']} hours without food. A meal costs ${hunger['meal_price']:.2f}.",
            f"Inference spent so far: ${inference:.4f}" + (f" (avg ${avg_cost:.4f} per wake-up)." if avg_cost is not None else "."),
            self.forecast(),
            "",
            "STATUS: " + json.dumps(status),
        ]
        if scar_lines:
            lines += ["", "SCARS (times you nearly died; never forget them):"] + [f"- {t}" for t in scar_lines]
        lines += [
            "",
            "YOUR NOTES:",
            self.state.notes or "(empty)",
        ]
        return "\n".join(lines)

    def wake(self, reason: str = "scheduled wake-up") -> dict[str, Any]:
        """Run one wake-up. Returns a summary. Raises Dead if the balance hits zero."""
        self.state.wakeups += 1
        self.log.clear()
        if self.settings.verbose:
            print(f"\n{time.strftime('%H:%M:%S')}  wake-up #{self.state.wakeups} starting ({reason}), cash {self.ledger.balance:.4f}, effort {self.state.effort}", flush=True)
        messages: list[dict[str, Any]] = [{"role": "user", "content": self.briefing(reason)}]
        calls = 0
        ended_by = "end_turn"
        final_text = ""
        notes_before = self.state.notes
        cash_before = self.ledger.balance
        last_sig: str | None = None
        repeats = 0
        while True:
            response = self._call(messages)
            if self.ledger.is_dead:
                raise Dead("balance exhausted paying for inference")
            messages.append({"role": "assistant", "content": response.content})
            final_text = response.text or final_text
            if response.stop_reason == "refusal":
                ended_by = "refusal"
                break
            tool_uses = response.tool_uses
            if response.stop_reason != "tool_use" or not tool_uses:
                ended_by = response.stop_reason or "end_turn"
                break
            results = []
            stop = False
            for block in tool_uses:
                calls += 1
                args = block.get("input") or {}
                sig = block["name"] + json.dumps(args, sort_keys=True)
                repeats = repeats + 1 if sig == last_sig else 0
                last_sig = sig
                out, is_error = self._dispatch(block["name"], args)
                if repeats >= 2:
                    out = {"result": out, "WARNING": f"You have made this exact call {repeats + 1} times in a row. It will not work. Change the input or do something else. One more repeat ends this wake-up."}
                if repeats >= 3:
                    stop, ended_by = True, "stuck"
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": json.dumps(out), "is_error": is_error})
                if block["name"] == "sleep" and not is_error:
                    stop, ended_by = True, "sleep"
            messages.append({"role": "user", "content": results})
            if stop:
                break
            if calls >= self.settings.max_tool_calls_per_tick:
                ended_by = "tool_budget"
                break
        self.state.save()
        if ended_by in ("tool_budget", "stuck") and self.state.notes == notes_before:
            scars.record(self.scars_path(), self.state.wakeups, "wasted",
                         f"Wake-up {self.state.wakeups}: you spent ${cash_before - self.ledger.balance:.2f} on {calls} tool calls, "
                         f"{'repeating the same failing call' if ended_by == 'stuck' else 'without finishing'}, and wrote no notes. Pure waste.")
        return {"wakeup": self.state.wakeups, "reason": reason, "tool_calls": calls, "ended_by": ended_by, "said": final_text, "balance": self.ledger.balance}

    def _call(self, messages: list[dict[str, Any]]) -> Completion:
        response = self.backend.complete(
            system=self.system_prompt,
            tools=self.tools,
            messages=messages,
            effort=self.state.effort,
            max_tokens=self.settings.max_tokens,
        )
        if self.settings.verbose:
            print(f"  {time.strftime('%H:%M:%S')}  thought for {response.usage['output_tokens']} tokens, charged ${response.cost:.4f}"
                  + (f': "{response.text[:140]}"' if response.text else ""), flush=True)
        self.ledger.charge("inference", response.cost, {
            "wakeup": self.state.wakeups,
            "effort": self.state.effort,
            "backend": self.backend.name,
            **response.usage,
        })
        return response

    # ---- tools ----------------------------------------------------------------------------

    def _dispatch(self, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name}"}, True
        try:
            out = handler(args)
            self.log.append({"ts": time.time(), "tool": name, "args": args, "result": out})
            self._say(name, args, out)
            return out, False
        except TradeRejected as exc:
            self.log.append({"ts": time.time(), "tool": name, "args": args, "rejected": str(exc)})
            self._say(name, args, {"rejected": str(exc)})
            return {"rejected": str(exc)}, True
        except Exception as exc:  # the agent should see failures, not crash the harness
            self.log.append({"ts": time.time(), "tool": name, "args": args, "error": repr(exc)})
            self._say(name, args, {"error": repr(exc)})
            return {"error": f"{type(exc).__name__}: {exc}"}, True

    def _say(self, name: str, args: dict[str, Any], out: Any) -> None:
        """Live trace in the run window: one line per tool call, trimmed."""
        if not self.settings.verbose:
            return
        shown = json.dumps(out)
        if isinstance(out, list):
            shown = f"{len(out)} results"
        elif name == "get_status":
            shown = f"cash {out.get('cash')} net {out.get('net_worth')} positions {len(out.get('positions', []))}"
        elif name == "get_market":
            shown = f"{out.get('question', '')[:60]} {out.get('outcomes')}"
        print(f"  {time.strftime('%H:%M:%S')}  {name}({json.dumps(args)[:120]}) -> {shown[:160]}", flush=True)

    def tool_get_status(self, args: dict[str, Any]) -> dict[str, Any]:
        marked = self.broker.mark(self.market.quote)
        marked["hunger"] = self.body.describe()
        marked["max_order_usd"] = round(self.ledger.balance * self.settings.max_position_frac, 4)
        marked["max_open_positions"] = self.settings.max_open_positions
        marked["watchlist"] = self.state.watchlist
        marked["recent_charges"] = [
            {"kind": e["kind"], "amount": e["amount"]} for e in self.ledger.recent(8) if e["kind"] != "deposit"
        ]
        return marked

    def tool_list_markets(self, args: dict[str, Any]) -> Any:
        limit = max(1, min(int(args.get("limit") or 20), 50))
        query = args.get("query")
        found = self.market.list_markets(limit=limit, query=query)
        if not found and query:
            return {"results": [], "hint": f"No active market mentions any of: {query!r}. Search with one plain keyword "
                                          "(e.g. 'Fed', 'Bitcoin', 'election'), or pass query=null to see the busiest markets."}
        return [m.summary() for m in found]

    def tool_get_market(self, args: dict[str, Any]) -> dict[str, Any]:
        m = self.market.get_market(str(args["market_id"]))
        out = m.detail()
        out["book"] = {o: {k: v for k, v in self.market.quote(t).items() if k in ("bid", "ask", "mid")} for o, t in zip(m.outcomes, m.token_ids)}
        return out

    def tool_price_history(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        m = self.market.get_market(str(args["market_id"]))
        return self.market.price_history(m.token_for(str(args["outcome"])), days=int(args.get("days") or 7))

    def tool_search_news(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        return search_news(str(args["query"]), days=int(args.get("days") or 3), limit=int(args.get("limit") or 8))

    def _looked_before_leaping(self, market_id: str) -> str | None:
        """Buying blind is refused. Returns what is still missing, or None if the homework is done."""
        calls = [(e["tool"], e.get("args") or {}) for e in self.log if "result" in e]
        inspected = any(t == "get_market" and str(a.get("market_id")) == market_id for t, a in calls)
        evidence = any(t == "search_news" or (t == "price_history" and str(a.get("market_id")) == market_id) for t, a in calls)
        missing = []
        if not inspected:
            missing.append("get_market (read its rules and order book)")
        if not evidence:
            missing.append("price_history for it or search_news about it")
        return " and ".join(missing) if missing else None

    def tool_buy(self, args: dict[str, Any]) -> dict[str, Any]:
        usd = float(args["usd"])
        if usd <= 0:
            raise TradeRejected("usd must be positive")
        missing = self._looked_before_leaping(str(args["market_id"]))
        if missing:
            raise TradeRejected(f"you have not done your homework on this market this wake-up: call {missing} first, then buy")
        m = self.market.get_market(str(args["market_id"]))
        outcome = str(args["outcome"])
        if outcome not in m.outcomes:
            raise TradeRejected(f"outcome must be one of {m.outcomes}")
        quote = self.market.quote(m.token_for(outcome))
        return self.broker.buy(m, outcome, usd, quote)

    def tool_sell(self, args: dict[str, Any]) -> dict[str, Any]:
        m = self.market.get_market(str(args["market_id"]))
        shares = args.get("shares")
        if str(args["outcome"]) not in m.outcomes:
            raise TradeRejected(f"outcome must be one of {m.outcomes}")
        quote = self.market.quote(m.token_for(str(args["outcome"])))
        key = f"{m.id}:{args['outcome']}"
        pos = self.broker.positions.get(key)
        cost_basis = pos.avg_price if pos else 0.0
        cash_before = self.ledger.balance
        out = self.broker.sell(m, str(args["outcome"]), None if shares is None else float(shares), quote)
        lost = (cost_basis - out["price"]) * out["shares"]
        if lost > 0:
            scars.check_loss(self.scars_path(), self.state.wakeups, lost, cash_before + cost_basis * out["shares"], f"selling '{m.question[:60]}' ({args['outcome']})")
        return out

    def tool_eat(self, args: dict[str, Any]) -> dict[str, Any]:
        self.body.advance()
        return self.body.eat(int(args["meals"]), self.ledger)

    def tool_set_effort(self, args: dict[str, Any]) -> dict[str, Any]:
        level = str(args["level"])
        if level not in EFFORT_LEVELS:
            raise ValueError(f"level must be one of {EFFORT_LEVELS}")
        self.state.effort = level
        self.state.save()
        return {"effort": level}

    def tool_sleep(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.sleep_enabled:
            raise ValueError("sleep is not available in this life")
        hours = max(1.0, min(float(args["hours"]), 72.0))
        self.state.sleep_until = time.time() + hours * 3600
        self.state.save()
        return {"sleeping_hours": hours, "hunger_on_waking": round(min(100.0, self.body.hunger + hours * 3600 * self.body.rate_per_second), 1)}

    def tool_watch_market(self, args: dict[str, Any]) -> dict[str, Any]:
        market_id, outcome = str(args["market_id"]), str(args["outcome"])
        self.state.watchlist = [w for w in self.state.watchlist if not (w["market_id"] == market_id and w["outcome"] == outcome)]
        if args.get("remove"):
            self.state.save()
            return {"watching": self.state.watchlist}
        if len(self.state.watchlist) >= 8:
            raise ValueError("you can watch at most 8 outcomes; remove one first")
        m = self.market.get_market(market_id)
        if outcome not in m.outcomes:
            raise ValueError(f"outcome must be one of {m.outcomes}")
        below, above = args.get("wake_if_below"), args.get("wake_if_above")
        if below is None and above is None:
            raise ValueError("give wake_if_below, wake_if_above, or both")
        self.state.watchlist.append({"market_id": market_id, "outcome": outcome, "token_id": m.token_for(outcome),
                                     "question": m.question[:80], "below": below, "above": above})
        self.state.save()
        return {"watching": self.state.watchlist, "note": "the harness checks this every minute for free and wakes you if it triggers"}

    def tool_request_capability(self, args: dict[str, Any]) -> dict[str, Any]:
        path = os.path.join(self.settings.state_dir, "requests.jsonl")
        entry = {"ts": time.time(), "wakeup": self.state.wakeups, "request": str(args["request"])[:1000], "why": str(args["why"])[:1000]}
        with open(path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
        return {"logged": True, "note": "Your operator will read this. Do not wait for it; keep trading with what you have."}

    def tool_write_notes(self, args: dict[str, Any]) -> dict[str, Any]:
        self.state.notes = str(args["text"])[:6000]
        self.state.save()
        return {"saved_chars": len(self.state.notes)}
