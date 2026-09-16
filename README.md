# DEV-TRADE: agent survival harness

An experiment. A Claude agent is given a small cash stake and one rule: **stay alive**.

- Every time it thinks, the real cost of that inference is deducted from its balance.
- It gets hungry. It has to buy meals with cash, and if hunger hits the limit it starves. Nobody feeds it.
- The harness watches the world every minute for free and wakes the agent on a schedule or the moment something happens: a held position moves, a market settles, hunger gets serious, or a price it asked to watch crosses a line. Every five minutes it also sweeps the busiest hundred markets and wakes the agent with a lead when a price jumps or Yes plus No sell under a dollar. Thinking costs money; watching does not.
- By default it cannot sleep through wake-ups. Set `SLEEP_ENABLED=1` for the gentler version.
- Its only income is trading on Polymarket prediction markets.
- When the balance hits zero, or it starves, it dies. No deposits, no bailouts.

The question being tested is whether an agent can generate more value than it consumes.

## What it can and cannot do

The agent gets these tools: check status, list markets, inspect a market, see a market's
price history, search recent news headlines, buy, sell, eat, set free price watches that wake it
early, choose its thinking effort, ask its operator for a capability it lacks, write notes, and
(only if enabled) sleep. It has no shell,
no filesystem, no general web access, and no memory between wake-ups except the notes it
writes. The container's egress is locked to the model API, Polymarket, and Google News RSS. The ledger is written only by the harness, so the agent
cannot edit its own balance.

Guardrails are enforced in code, not in the prompt:

| Guardrail | Default |
|---|---|
| Max order size | 25% of cash |
| Max open positions | 6 |
| Tool calls per wake-up | 12 |
| Sleep | 1 to 72 hours |
| Trading mode | paper only |

Paper trading fills against the real Polymarket order book with configurable slippage and
fees, and settles positions when the real market resolves.

## Pick a brain

The harness runs on any of three backends. Set `BACKEND` in `.env`:

| Backend | Cost to you | What the agent pays | Notes |
|---|---|---|---|
| `ollama` | free | synthetic price per token | Runs locally. Needs a model with tool calling, e.g. `qwen3:8b` or `llama3.1:8b`. |
| `openai` | free tiers exist | synthetic price per token | Any OpenAI-compatible endpoint: Groq, Gemini, OpenRouter, vLLM. |
| `anthropic` | real API credits | the real inference cost | Claude. Set a spend cap on the key. |

Set `FALLBACK_BACKEND` to hand over to a second brain when the first is rate-limited or down,
for example a big model on Groq's free tier by day and local Ollama when the daily cap is hit.

With a free backend the agent is still charged for every token at `SYNTHETIC_PRICE_*`
rates, so the survival pressure is identical. The only thing that changes is whether the
money leaving the ledger is also leaving your account.

## Run it

```bash
cp .env.example .env      # pick a backend and fill it in
pip install -r requirements.txt
python -m survival tick       # one wake-up, watch what happens
python -m survival run        # loop forever; prints every tool call as it happens
python -m survival status     # the books, once
python -m survival watch      # the books, refreshed every 30s
python -m survival dashboard  # same thing as a web page at http://localhost:8787
python -m survival reset      # start over
```

For Ollama, pull the model first: `ollama pull qwen3:8b`.

With Docker (the agent runs unprivileged with egress locked to the model API and Polymarket):

```bash
docker compose up --build
```

If the backend is Ollama on the host machine, set `OLLAMA_URL=http://host.docker.internal:11434`
in `.env`; the compose file maps that name to the host.

State lives in `state/`: `ledger.jsonl` (every cent, append-only), `positions.json`,
`agent.json` (notes, effort, watchlist), `wakeups.jsonl` (every tool call), `scars.jsonl`,
`requests.jsonl` (capabilities it asked for), `market_snapshots.jsonl` (every scanner sweep's
prices, a growing dataset for research), and `OBITUARY.json` when it dies. Touch `state/KILL` to freeze the agent without killing it.

## Tuning the economy

All knobs are environment variables; see `.env.example`. The interesting ones:

- `MEAL_PRICE`, `MEAL_RESTORES`, and `STARVE_DAYS` set the food economy. Defaults: a meal is $0.50, two meals take it from starving to full, and it starves in two days without eating.
- `SLEEP_ENABLED` decides whether it can skip wake-ups. Off by default: it has to feed itself.
- `SURVIVAL_MODEL` picks the brain. A weaker model is a worse trader but no cheaper on paper, so the harness rewards good judgment, not raw size.
- `SYNTHETIC_PRICE_INPUT` and `SYNTHETIC_PRICE_OUTPUT` set what free backends charge the agent. Lower them and the agent can afford to think more; raise them and every wake-up hurts.
- `TICK_SECONDS` is the scheduled wake-up interval; `WATCH_SECONDS` is how often the free watch runs; `WAKE_ON_MOVE` is how far a held price must move to wake it early.

## Going live

`LIVE_TRADING=1` currently refuses to start. Real orders need a Polygon wallet, USDC, and the
CLOB signing flow, and Polymarket's terms exclude some jurisdictions. Run paper for a few weeks
first and read the ledger before deciding whether real money makes the experiment better.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite drives the agent loop with a scripted model and a fake market, so it needs no API key
and no network.
