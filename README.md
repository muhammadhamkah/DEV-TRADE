# DEV-TRADE: agent survival harness

An experiment. A Claude agent is given a small cash stake and one rule: **stay alive**.

- Every time it thinks, the real cost of that inference is deducted from its balance.
- Rent is deducted continuously, whether it thinks or not.
- Its only income is trading on Polymarket prediction markets.
- When the balance hits zero, it dies. No deposits, no bailouts.

The question being tested is whether an agent can generate more value than it consumes.

## What it can and cannot do

The agent gets exactly eight tools: check status, list markets, inspect a market, buy, sell,
choose its thinking effort, sleep, and write notes. It has no shell, no filesystem, no web,
and no memory between wake-ups except the notes it writes. The container's egress is locked
to the model API and Polymarket. The ledger is written only by the harness, so the agent
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

With a free backend the agent is still charged for every token at `SYNTHETIC_PRICE_*`
rates, so the survival pressure is identical. The only thing that changes is whether the
money leaving the ledger is also leaving your account.

## Run it

```bash
cp .env.example .env      # pick a backend and fill it in
pip install -r requirements.txt
python -m survival tick     # one wake-up, watch what happens
python -m survival status   # the books
python -m survival run      # loop forever
python -m survival reset    # start over
```

For Ollama, pull the model first: `ollama pull qwen3:8b`.

With Docker (the agent runs unprivileged with egress locked to the model API and Polymarket):

```bash
docker compose up --build
```

If the backend is Ollama on the host machine, set `OLLAMA_URL=http://host.docker.internal:11434`
in `.env`; the compose file maps that name to the host.

State lives in `state/`: `ledger.jsonl` (every cent, append-only), `positions.json`,
`agent.json` (notes, effort, sleep timer), `wakeups.jsonl` (every tool call), and
`OBITUARY.json` when it dies. Touch `state/KILL` to freeze the agent without killing it.

## Tuning the economy

All knobs are environment variables; see `.env.example`. The interesting ones:

- `DAILY_RENT` sets how fast idling kills. At $0.50/day a $50 stake survives 100 days doing nothing.
- `SURVIVAL_MODEL` picks the brain. A weaker model is a worse trader but no cheaper on paper, so the harness rewards good judgment, not raw size.
- `SYNTHETIC_PRICE_INPUT` and `SYNTHETIC_PRICE_OUTPUT` set what free backends charge the agent. Lower them and the agent can afford to think more; raise them and every wake-up hurts.
- `TICK_SECONDS` is how often the harness offers a wake-up. The agent can sleep through them.

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
