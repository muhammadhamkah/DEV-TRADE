"""Backend wire-format conversion, with a fake HTTP session. No model needed."""
import json

from survival.backends import AnthropicBackend, Completion, OllamaBackend, OpenAICompatBackend, make_backend
from survival.config import Settings
from survival.tools import TOOLS

TRANSCRIPT = [
    {"role": "user", "content": "briefing"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "buying"},
        {"type": "tool_use", "id": "c1", "name": "buy", "input": {"market_id": "1", "outcome": "Yes", "usd": 5, "reason": "r"}},
    ]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{\"rejected\": \"cap\"}", "is_error": True}]},
]


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "body": json, "headers": headers})
        return self.replies.pop(0)


def test_ollama_converts_transcript_and_prices_usage():
    session = FakeSession([FakeResp({
        "message": {"role": "assistant", "content": "", "thinking": "let me", "tool_calls": [{"function": {"name": "sleep", "arguments": {"hours": 6}}}]},
        "prompt_eval_count": 2000, "eval_count": 100, "done_reason": "stop",
    })])
    b = OllamaBackend(model="qwen3:8b", url="http://ollama:11434/", price_input=5, price_output=25, session=session)
    out = b.complete(system="SYS", tools=TOOLS, messages=TRANSCRIPT, effort="high", max_tokens=500)
    body = session.calls[0]["body"]
    assert body["think"] is True and body["options"]["num_predict"] == 500
    msgs = body["messages"]
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "buy" and msgs[2]["thinking"] == "hmm"
    assert msgs[3] == {"role": "tool", "content": 'ERROR: {"rejected": "cap"}', "tool_name": "buy"}
    assert body["tools"][0]["type"] == "function" and "strict" not in body["tools"][0]["function"]
    assert out.stop_reason == "tool_use" and out.tool_uses[0]["name"] == "sleep" and out.tool_uses[0]["input"] == {"hours": 6}
    assert out.content[0] == {"type": "thinking", "thinking": "let me"}
    assert abs(out.cost - (2000 * 5 + 100 * 25) / 1e6) < 1e-12


def test_ollama_falls_back_when_model_cannot_think():
    session = FakeSession([
        FakeResp({"error": "registry.ollama.ai/library/llama3.1:8b does not support thinking"}, status=400),
        FakeResp({"message": {"role": "assistant", "content": "ok"}, "prompt_eval_count": 10, "eval_count": 2}),
        FakeResp({"message": {"role": "assistant", "content": "ok"}, "prompt_eval_count": 10, "eval_count": 2}),
    ])
    b = OllamaBackend(model="llama3.1:8b", session=session)
    out = b.complete(system="s", tools=TOOLS, messages=[{"role": "user", "content": "hi"}], effort="medium", max_tokens=10)
    assert out.text == "ok" and out.stop_reason == "end_turn"
    b.complete(system="s", tools=TOOLS, messages=[{"role": "user", "content": "hi"}], effort="medium", max_tokens=10)
    assert "think" not in session.calls[1]["body"] and "think" not in session.calls[2]["body"]


def test_ollama_skips_thinking_on_low_effort():
    session = FakeSession([FakeResp({"message": {"role": "assistant", "content": "ok"}, "prompt_eval_count": 1, "eval_count": 1})])
    OllamaBackend(model="m", session=session).complete(system="s", tools=[], messages=[{"role": "user", "content": "x"}], effort="low", max_tokens=10)
    assert "think" not in session.calls[0]["body"]


def test_openai_compat_converts_and_parses():
    session = FakeSession([FakeResp({
        "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "call_9", "type": "function", "function": {"name": "get_status", "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 300, "completion_tokens": 20},
    })])
    b = OpenAICompatBackend(model="llama-3.3-70b-versatile", base_url="https://api.groq.com/openai/v1/", api_key="k", session=session)
    out = b.complete(system="SYS", tools=TOOLS, messages=TRANSCRIPT, effort="medium", max_tokens=100)
    call = session.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer k"}
    msgs = call["body"]["messages"]
    assert msgs[2]["tool_calls"][0] == {"id": "c1", "type": "function", "function": {"name": "buy", "arguments": json.dumps({"market_id": "1", "outcome": "Yes", "usd": 5, "reason": "r"})}}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": 'ERROR: {"rejected": "cap"}'}
    assert out.stop_reason == "tool_use" and out.tool_uses[0] == {"type": "tool_use", "id": "call_9", "name": "get_status", "input": {}}
    assert out.usage["input_tokens"] == 300


def test_make_backend_selects_by_setting(monkeypatch):
    assert isinstance(make_backend(Settings(backend="ollama", model="qwen3:8b")), OllamaBackend)
    assert isinstance(make_backend(Settings(backend="openai", model="x", openai_base_url="https://h/v1")), OpenAICompatBackend)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(make_backend(Settings(backend="anthropic", model="claude-opus-5")), AnthropicBackend)


def test_openai_compat_sends_reasoning_effort_for_gpt_oss():
    session = FakeSession([FakeResp({"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {}})] * 2)
    OpenAICompatBackend(model="openai/gpt-oss-120b", base_url="https://h/v1", session=session).complete(
        system="s", tools=[], messages=[{"role": "user", "content": "x"}], effort="high", max_tokens=10)
    assert session.calls[0]["body"]["reasoning_effort"] == "high"
    OpenAICompatBackend(model="llama-3.3-70b-versatile", base_url="https://h/v1", session=session).complete(
        system="s", tools=[], messages=[{"role": "user", "content": "x"}], effort="high", max_tokens=10)
    assert "reasoning_effort" not in session.calls[1]["body"]


def test_openai_compat_waits_out_a_rate_limit(monkeypatch):
    class Limited(FakeResp):
        def __init__(self):
            super().__init__({"error": {"message": "Rate limit reached. Please try again in 1.5s."}}, status=429)
            self.headers = {}
    session = FakeSession([Limited(), FakeResp({"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {}})])
    slept = []
    import survival.backends as b
    monkeypatch.setattr(b.time, "sleep", lambda s: slept.append(s))
    out = OpenAICompatBackend(model="m", base_url="https://h/v1", session=session).complete(
        system="s", tools=[], messages=[{"role": "user", "content": "x"}], effort="low", max_tokens=10)
    assert out.text == "ok" and slept == [2.5] and len(session.calls) == 2


def test_fallback_backend_switches_on_exhaustion_and_returns_later(monkeypatch):
    from survival.backends import FallbackBackend

    class Primary:
        name = "groq"
        calls = 0

        def complete(self, **kw):
            Primary.calls += 1
            raise RuntimeError("openai-compat 429: tokens per day")

    class Local:
        name = "ollama"

        def complete(self, **kw):
            return Completion(content=[{"type": "text", "text": "local"}], stop_reason="end_turn", usage={}, cost=0.01)

    fb = FallbackBackend(Primary(), Local(), retry_primary_after=100)
    import survival.backends as b
    t = {"now": 1000.0}
    monkeypatch.setattr(b.time, "time", lambda: t["now"])
    assert fb.complete(system="s", tools=[], messages=[], effort="low", max_tokens=1).text == "local"
    assert fb.name == "ollama" and Primary.calls == 1
    fb.complete(system="s", tools=[], messages=[], effort="low", max_tokens=1)
    assert Primary.calls == 1            # still on fallback, primary not retried yet
    t["now"] = 1200.0
    fb.complete(system="s", tools=[], messages=[], effort="low", max_tokens=1)
    assert Primary.calls == 2            # retried the primary after the cooldown, fell back again


def test_fallback_backend_reraises_real_errors():
    from survival.backends import FallbackBackend

    class Primary:
        name = "p"

        def complete(self, **kw):
            raise ValueError("bad schema")

    class Local:
        name = "l"

        def complete(self, **kw):
            raise AssertionError("should not be called")

    import pytest
    with pytest.raises(ValueError):
        FallbackBackend(Primary(), Local()).complete(system="s", tools=[], messages=[], effort="low", max_tokens=1)


def test_make_backend_builds_fallback_chain():
    from survival.backends import FallbackBackend
    from survival.config import Settings
    b = make_backend(Settings(backend="openai", model="m", openai_base_url="https://h/v1", fallback_backend="ollama", ollama_model="q"))
    assert isinstance(b, FallbackBackend) and b.fallback.model == "q"


def test_openai_compat_gives_up_immediately_on_a_daily_cap(monkeypatch):
    class Daily(FakeResp):
        def __init__(self):
            super().__init__({"error": {"message": "Rate limit reached ... on tokens per day (TPD): Limit 200000"}}, status=429)
            self.headers = {}
    session = FakeSession([Daily()])
    import survival.backends as b
    monkeypatch.setattr(b.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("should not wait")))
    import pytest
    with pytest.raises(RuntimeError, match="exhausted"):
        OpenAICompatBackend(model="m", base_url="https://h/v1", session=session).complete(
            system="s", tools=[], messages=[{"role": "user", "content": "x"}], effort="low", max_tokens=10)


def test_chain_backend_skips_exhausted_brains_and_retries_after_cooldown(monkeypatch):
    from survival.backends import ChainBackend

    class Brain:
        def __init__(self, name, fail):
            self.name, self.fail, self.calls = name, fail, 0

        def complete(self, **kw):
            self.calls += 1
            if self.fail:
                raise RuntimeError("429 tokens per day")
            return Completion(content=[{"type": "text", "text": self.name}], stop_reason="end_turn", usage={}, cost=0)

    a, b, c = Brain("a", True), Brain("b", True), Brain("c", False)
    chain = ChainBackend([a, b, c], cooldown=100)
    import survival.backends as mod
    t = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: t["now"])
    assert chain.complete(system="s", tools=[], messages=[], effort="low", max_tokens=1).text == "c"
    assert (a.calls, b.calls, c.calls) == (1, 1, 1) and chain.name == "c"
    chain.complete(system="s", tools=[], messages=[], effort="low", max_tokens=1)
    assert (a.calls, b.calls, c.calls) == (1, 1, 2)      # a and b skipped during cooldown
    t["now"] = 1200.0
    a.fail = False
    assert chain.complete(system="s", tools=[], messages=[], effort="low", max_tokens=1).text == "a"


def test_make_backend_builds_chain_from_numbered_env(monkeypatch):
    from survival.backends import ChainBackend
    from survival.config import Settings
    monkeypatch.setenv("BRAIN1_KIND", "openai")
    monkeypatch.setenv("BRAIN1_MODEL", "gpt-oss-120b")
    monkeypatch.setenv("BRAIN1_URL", "https://api.cerebras.ai/v1")
    monkeypatch.setenv("BRAIN1_KEY", "csk-x")
    monkeypatch.setenv("BRAIN2_KIND", "ollama")
    monkeypatch.setenv("BRAIN2_MODEL", "qwen3:8b")
    b = make_backend(Settings())
    assert isinstance(b, ChainBackend) and len(b.brains) == 2
    assert b.brains[0].name == "cerebras:gpt-oss-120b" and b.brains[1].name == "ollama:qwen3:8b"


def test_chain_skips_a_brain_that_wants_payment_or_has_no_such_model():
    from survival.backends import ChainBackend

    class Paid:
        name = "cerebras"

        def complete(self, **kw):
            raise RuntimeError('openai-compat 402: {"message":"Payment required to access this resource."}')

    class Free:
        name = "groq"

        def complete(self, **kw):
            return Completion(content=[{"type": "text", "text": "free"}], stop_reason="end_turn", usage={}, cost=0)

    assert ChainBackend([Paid(), Free()]).complete(system="s", tools=[], messages=[], effort="low", max_tokens=1).text == "free"
