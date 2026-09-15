"""Paper broker. Fills against the real order book with configurable slippage and fees.

Guardrails live here, in code the agent cannot reach: position sizing, position count, and
the rule that you can only spend cash you actually have.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .ledger import Ledger
from .polymarket import Market


class TradeRejected(Exception):
    pass


@dataclass
class Position:
    market_id: str
    question: str
    outcome: str
    token_id: str
    shares: float
    avg_price: float
    opened_at: float = field(default_factory=time.time)

    @property
    def cost(self) -> float:
        return self.shares * self.avg_price


@dataclass
class PaperBroker:
    ledger: Ledger
    path: str
    max_position_frac: float
    max_open_positions: int
    slippage_bps: float
    fee_bps: float
    positions: dict[str, Position] = field(default_factory=dict)  # key: market_id:outcome

    def __post_init__(self) -> None:
        if os.path.exists(self.path):
            with open(self.path) as fh:
                raw = json.load(fh)
            self.positions = {k: Position(**v) for k, v in raw.items()}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump({k: asdict(v) for k, v in self.positions.items()}, fh, indent=1)

    @staticmethod
    def _key(market_id: str, outcome: str) -> str:
        return f"{market_id}:{outcome}"

    def buy(self, market: Market, outcome: str, usd: float, quote: dict[str, float]) -> dict[str, Any]:
        if market.closed:
            raise TradeRejected("market is closed")
        if outcome not in market.outcomes:
            raise TradeRejected(f"outcome must be one of {market.outcomes}")
        if usd <= 0:
            raise TradeRejected("usd must be positive")
        cash = self.ledger.balance
        cap = round(min(cash, cash * self.max_position_frac), 4)
        clipped_from = None
        if usd > cap:
            if cap < 0.5:
                raise TradeRejected(f"you cannot afford a trade: cap is {cap:.4f} ({self.max_position_frac:.0%} of {cash:.4f} cash)")
            clipped_from, usd = usd, cap
        key = self._key(market.id, outcome)
        if key not in self.positions and len(self.positions) >= self.max_open_positions:
            raise TradeRejected(f"already holding the maximum of {self.max_open_positions} positions")
        ask = quote["ask"]
        if ask <= 0 or ask >= 1:
            raise TradeRejected("no usable ask price in the order book")
        # Walk the ask ladder: each level only has so many shares. Anything past the book is unfilled.
        levels = quote.get("asks") or [(ask, float("inf"))]
        budget = usd * (1 - self.fee_bps / 10_000)
        shares = 0.0
        spent = 0.0
        for price, size in levels:
            price = min(0.999, price * (1 + self.slippage_bps / 10_000))
            take = min(size, (budget - spent) / price)
            if take <= 0:
                break
            shares += take
            spent += take * price
            if budget - spent < 1e-9:
                break
        if shares <= 0:
            raise TradeRejected("order book has no depth on the ask side")
        fill = spent / shares
        fee = spent * self.fee_bps / 10_000
        usd = round(spent + fee, 6)
        self.ledger.charge("trade_buy", usd, {"market": market.id, "outcome": outcome, "shares": shares, "price": fill, "fee": fee})
        pos = self.positions.get(key)
        if pos:
            total = pos.shares + shares
            pos.avg_price = (pos.cost + shares * fill) / total
            pos.shares = total
        else:
            self.positions[key] = Position(market.id, market.question, outcome, market.token_for(outcome), shares, fill)
        self._save()
        note = "partial fill: the order book ran out of shares at reasonable prices" if usd < budget / (1 - self.fee_bps / 10_000) - 1e-6 else "full fill"
        if clipped_from is not None:
            note = f"order clipped from {clipped_from:.4f} to the cap of {cap:.4f} ({self.max_position_frac:.0%} of cash); " + note
        return {"filled": True, "spent": usd, "shares": round(shares, 4), "avg_price": round(fill, 4), "fee": round(fee, 6),
                "note": note, "cash_after": self.ledger.balance}

    def sell(self, market: Market, outcome: str, shares: float | None, quote: dict[str, float]) -> dict[str, Any]:
        key = self._key(market.id, outcome)
        pos = self.positions.get(key)
        if not pos:
            raise TradeRejected("no such position")
        qty = pos.shares if shares is None else min(shares, pos.shares)
        if qty <= 0:
            raise TradeRejected("shares must be positive")
        bid = quote["bid"]
        if bid <= 0:
            raise TradeRejected("no bids in the order book; cannot sell right now")
        levels = quote.get("bids") or [(bid, float("inf"))]
        remaining = qty
        gross = 0.0
        for price, size in levels:
            price = max(0.001, price * (1 - self.slippage_bps / 10_000))
            take = min(size, remaining)
            if take <= 0:
                break
            gross += take * price
            remaining -= take
            if remaining <= 1e-9:
                break
        qty = qty - remaining
        if qty <= 0:
            raise TradeRejected("order book has no depth on the bid side")
        fill = gross / qty
        fee = gross * self.fee_bps / 10_000
        proceeds = gross - fee
        self.ledger.credit("trade_sell", proceeds, {"market": market.id, "outcome": outcome, "shares": qty, "price": fill, "fee": fee})
        pos.shares -= qty
        if pos.shares < 0.01:  # dust: not worth tracking or thinking about
            del self.positions[key]
        self._save()
        return {"filled": True, "shares": round(qty, 4), "price": round(fill, 4), "proceeds": round(proceeds, 6), "cash_after": self.ledger.balance}

    def settle(self, fetch_market: Callable[[str], Market]) -> list[dict[str, Any]]:
        """Pay out positions whose markets have resolved. Winners get $1 per share, losers $0."""
        events = []
        for key, pos in list(self.positions.items()):
            try:
                market = fetch_market(pos.market_id)
            except Exception as exc:  # network hiccup: leave the position, try next tick
                events.append({"market": pos.market_id, "error": str(exc)})
                continue
            if not market.closed or market.resolved_outcome is None:
                continue
            won = market.resolved_outcome == pos.outcome
            payout = pos.shares if won else 0.0
            if payout > 0:
                self.ledger.credit("settlement", payout, {"market": pos.market_id, "outcome": pos.outcome, "won": won})
            else:
                self.ledger.credit("settlement", 0.0, {"market": pos.market_id, "outcome": pos.outcome, "won": won})
            events.append({"market": pos.market_id, "question": pos.question, "outcome": pos.outcome, "won": won, "payout": round(payout, 4)})
            del self.positions[key]
        if events:
            self._save()
        return events

    def mark(self, quote_for: Callable[[str], dict[str, float]]) -> dict[str, Any]:
        """Mark positions at the current bid. Returns cash, position value, net worth."""
        value = 0.0
        rows = []
        for pos in self.positions.values():
            try:
                bid = quote_for(pos.token_id)["bid"]
            except Exception:
                bid = pos.avg_price
            worth = pos.shares * bid
            value += worth
            rows.append({
                "market_id": pos.market_id,
                "question": pos.question,
                "outcome": pos.outcome,
                "shares": round(pos.shares, 4),
                "avg_price": round(pos.avg_price, 4),
                "bid": round(bid, 4),
                "value": round(worth, 4),
                "unrealized_pnl": round(worth - pos.cost, 4),
            })
        return {"cash": round(self.ledger.balance, 4), "positions_value": round(value, 4), "net_worth": round(self.ledger.balance + value, 4), "positions": rows}
