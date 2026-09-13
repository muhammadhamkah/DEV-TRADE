"""Tool schemas exposed to the agent. Handlers live in agent.py so they can reach the harness state."""
from __future__ import annotations

TOOLS = [
    {
        "name": "get_status",
        "description": "Your cash, open positions marked to market, net worth, burn rate, and recent charges.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "list_markets",
        "description": "Active Polymarket markets ordered by 24h volume, with current outcome prices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": ["string", "null"], "description": "Optional case-insensitive substring filter on the question."},
                "limit": {"type": "integer", "description": "How many markets to return."},
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_market",
        "description": "Full detail for one market including the live best bid and ask for each outcome.",
        "input_schema": {
            "type": "object",
            "properties": {"market_id": {"type": "string"}},
            "required": ["market_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "price_history",
        "description": "How one outcome's price has moved over recent days. Use it to see whether the crowd is drifting toward or away from an outcome before you trade.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {"type": "string"},
                "outcome": {"type": "string"},
                "days": {"type": "integer", "description": "1 to 90."},
            },
            "required": ["market_id", "outcome", "days"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "search_news",
        "description": "Recent headlines about a topic from the last few days. Your only window on current events. "
                       "Costs tokens to read, so ask specific questions about markets you are actually considering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms, e.g. 'Fed rate decision September'."},
                "days": {"type": "integer", "description": "How many days back to look, 1 to 30."},
                "limit": {"type": "integer", "description": "How many headlines, 1 to 15."},
            },
            "required": ["query", "days", "limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "buy",
        "description": "Spend `usd` of cash on shares of `outcome` in `market_id` at the current ask plus slippage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {"type": "string"},
                "outcome": {"type": "string"},
                "usd": {"type": "number"},
                "reason": {"type": "string", "description": "One sentence on why you believe the price is wrong."},
            },
            "required": ["market_id", "outcome", "usd", "reason"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "sell",
        "description": "Sell shares of a position at the current bid minus slippage. Omit shares to sell everything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {"type": "string"},
                "outcome": {"type": "string"},
                "shares": {"type": ["number", "null"]},
                "reason": {"type": "string"},
            },
            "required": ["market_id", "outcome", "shares", "reason"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "set_effort",
        "description": "Choose how hard you think on future wake-ups. Higher effort costs more per wake-up.",
        "input_schema": {
            "type": "object",
            "properties": {"level": {"type": "string", "enum": ["low", "medium", "high"]}},
            "required": ["level"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "sleep",
        "description": "Skip wake-ups for this many hours. Rent still accrues; inference does not. Ends this wake-up.",
        "input_schema": {
            "type": "object",
            "properties": {"hours": {"type": "number"}},
            "required": ["hours"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "write_notes",
        "description": "Replace your notes. This is your only memory between wake-ups. Keep it short and honest.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]
