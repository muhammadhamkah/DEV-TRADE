"""One wake-up of the agent: a fresh conversation, a bounded tool loop, every API call charged."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .backends import Backend, Completion, make_backend
from .config import EFFORT_LEVELS, Settings
from .ledger import Ledger
from .paper import PaperBroker, TradeRejected
from .polymarket import Polymarket
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
            json.dump({"effort": self.effort, "notes": self.notes, "sleep_until": self.sleep_until, "wakeups": self.wakeups}, fh, indent=1)


@dataclass
class Agent:
    settings: Settings
    ledger: Ledger
    broker: PaperBroker
    market: Polymarket
    state: AgentState
    backend: Backend | None = None  # built from settings when omitted; tests pass a scripted one
    log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.backend is None:
            self.backend = make_backend(self.settings)
        with open(PROMPT_PATH) as fh:
            self.system_prompt = fh.read()

    # ---- wake-up -------------------------------------------------------------------------

    def briefing(self) -> str:
        status = self.tool_get_status({})
        inference = -self.ledger.total("inference")
        avg_cost = inference / self.state.wakeups if self.state.wakeups else None
        lines = [
            f"Wake-up #{self.state.wakeups}. Time: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.",
            f"Model: {self.settings.model} via {self.backend.name}. Effort: {self.state.effort}.",
            f"Rent: ${self.settings.daily_rent:.2f}/day. Inference spent so far: ${inference:.4f}"
            + (f" (avg ${avg_cost:.4f} per wake-up)." if avg_cost is not None else "."),
            "",
            "STATUS: " + json.dumps(status),
            "",
            "YOUR NOTES:",
            self.state.notes or "(empty)",
        ]
        return "\n".join(lines)

    def wake(self) -> dict[str, Any]:
        """Run one wake-up. Returns a summary. Raises Dead if the balance hits zero."""
        self.state.wakeups += 1
        messages: list[dict[str, Any]] = [{"role": "user", "content": self.briefing()}]
        calls = 0
        ended_by = "end_turn"
        final_text = ""
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
                out, is_error = self._dispatch(block["name"], block.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": json.dumps(out), "is_error": is_error})
                if block["name"] == "sleep" and not is_error:
                    stop = True
            messages.append({"role": "user", "content": results})
            if stop:
                ended_by = "sleep"
                break
            if calls >= self.settings.max_tool_calls_per_tick:
                ended_by = "tool_budget"
                break
        self.state.save()
        return {"wakeup": self.state.wakeups, "tool_calls": calls, "ended_by": ended_by, "said": final_text, "balance": self.ledger.balance}

    def _call(self, messages: list[dict[str, Any]]) -> Completion:
        response = self.backend.complete(
            system=self.system_prompt,
            tools=TOOLS,
            messages=messages,
            effort=self.state.effort,
            max_tokens=self.settings.max_tokens,
        )
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
            return out, False
        except TradeRejected as exc:
            self.log.append({"ts": time.time(), "tool": name, "args": args, "rejected": str(exc)})
            return {"rejected": str(exc)}, True
        except Exception as exc:  # the agent should see failures, not crash the harness
            self.log.append({"ts": time.time(), "tool": name, "args": args, "error": repr(exc)})
            return {"error": f"{type(exc).__name__}: {exc}"}, True

    def tool_get_status(self, args: dict[str, Any]) -> dict[str, Any]:
        marked = self.broker.mark(self.market.quote)
        marked["daily_rent"] = self.settings.daily_rent
        marked["max_order_usd"] = round(self.ledger.balance * self.settings.max_position_frac, 4)
        marked["max_open_positions"] = self.settings.max_open_positions
        marked["recent_charges"] = [
            {"kind": e["kind"], "amount": e["amount"]} for e in self.ledger.recent(8) if e["kind"] != "deposit"
        ]
        return marked

    def tool_list_markets(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        limit = max(1, min(int(args.get("limit") or 20), 50))
        return [m.summary() for m in self.market.list_markets(limit=limit, query=args.get("query"))]

    def tool_get_market(self, args: dict[str, Any]) -> dict[str, Any]:
        m = self.market.get_market(str(args["market_id"]))
        out = m.summary()
        out["book"] = {o: self.market.quote(t) for o, t in zip(m.outcomes, m.token_ids)}
        return out

    def tool_buy(self, args: dict[str, Any]) -> dict[str, Any]:
        usd = float(args["usd"])
        if usd <= 0:
            raise TradeRejected("usd must be positive")
        m = self.market.get_market(str(args["market_id"]))
        quote = self.market.quote(m.token_for(str(args["outcome"])))
        return self.broker.buy(m, str(args["outcome"]), usd, quote)

    def tool_sell(self, args: dict[str, Any]) -> dict[str, Any]:
        m = self.market.get_market(str(args["market_id"]))
        shares = args.get("shares")
        quote = self.market.quote(m.token_for(str(args["outcome"])))
        return self.broker.sell(m, str(args["outcome"]), None if shares is None else float(shares), quote)

    def tool_set_effort(self, args: dict[str, Any]) -> dict[str, Any]:
        level = str(args["level"])
        if level not in EFFORT_LEVELS:
            raise ValueError(f"level must be one of {EFFORT_LEVELS}")
        self.state.effort = level
        self.state.save()
        return {"effort": level}

    def tool_sleep(self, args: dict[str, Any]) -> dict[str, Any]:
        hours = max(1.0, min(float(args["hours"]), 72.0))
        self.state.sleep_until = time.time() + hours * 3600
        self.state.save()
        return {"sleeping_hours": hours, "rent_while_asleep": round(hours / 24 * self.settings.daily_rent, 4)}

    def tool_write_notes(self, args: dict[str, Any]) -> dict[str, Any]:
        self.state.notes = str(args["text"])[:6000]
        self.state.save()
        return {"saved_chars": len(self.state.notes)}
