"""Model backends. The agent loop speaks one neutral transcript format (Anthropic-shaped dicts);
each backend converts to and from its own wire format and prices its own usage.

Neutral format:
  assistant content: [{"type": "text", "text": ...}, {"type": "tool_use", "id", "name", "input"}]
  user content:      str | [{"type": "tool_result", "tool_use_id", "content", "is_error"}]
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

from .pricing import PRICES, usage_cost


@dataclass
class Completion:
    content: list[dict[str, Any]]
    stop_reason: str            # end_turn | tool_use | max_tokens | refusal
    usage: dict[str, int]       # input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens
    cost: float                 # USD charged to the agent for this call

    @property
    def text(self) -> str:
        return " ".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class Backend(Protocol):
    name: str

    def complete(self, *, system: str, tools: list[dict], messages: list[dict], effort: str, max_tokens: int) -> Completion: ...


# ---------------------------------------------------------------------------------------------
# Anthropic


@dataclass
class AnthropicBackend:
    model: str
    enable_fallbacks: bool = True
    client: Any = None
    name: str = "anthropic"

    def __post_init__(self) -> None:
        if self.client is None:
            import anthropic
            self.client = anthropic.Anthropic()
        if self.model not in PRICES:
            raise KeyError(f"no price table for {self.model!r}; add it to survival/pricing.py")

    def complete(self, *, system, tools, messages, effort, max_tokens) -> Completion:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            output_config={"effort": effort},
        )
        if self.enable_fallbacks:
            resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = self.client.messages.create(**kwargs)
        u = resp.usage
        usage = {
            "input_tokens": u.input_tokens or 0,
            "output_tokens": u.output_tokens or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        content = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(vars(b)) for b in resp.content]
        return Completion(content=content, stop_reason=resp.stop_reason or "end_turn", usage=usage, cost=usage_cost(self.model, usage))


# ---------------------------------------------------------------------------------------------
# Ollama (local, free). Usage is priced synthetically so the agent still pays to think.


@dataclass
class OllamaBackend:
    model: str
    url: str = "http://localhost:11434"
    price_input: float = 5.0       # USD per million tokens, charged to the paper ledger
    price_output: float = 25.0
    num_ctx: int = 16384
    think: bool = True             # ask for thinking on effort medium/high if the model supports it
    session: Any = None
    timeout: float = 600.0
    name: str = "ollama"
    _supports_think: bool | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.url = self.url.rstrip("/")
        self.session = self.session or requests.Session()

    # -- conversion ---------------------------------------------------------------------------

    @staticmethod
    def _tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}} for t in tools]

    @staticmethod
    def _messages(system: str, messages: list[dict]) -> list[dict]:
        id_to_name: dict[str, str] = {}
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if m["role"] == "assistant":
                text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
                calls = []
                for b in content:
                    if b.get("type") == "tool_use":
                        id_to_name[b["id"]] = b["name"]
                        calls.append({"function": {"name": b["name"], "arguments": b.get("input") or {}}})
                msg: dict[str, Any] = {"role": "assistant", "content": text}
                thinking = " ".join(b.get("thinking", "") for b in content if b.get("type") == "thinking").strip()
                if thinking:
                    msg["thinking"] = thinking
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            elif isinstance(content, str):
                out.append({"role": "user", "content": content})
            else:
                for b in content:
                    if b.get("type") == "tool_result":
                        body = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                        if b.get("is_error"):
                            body = "ERROR: " + body
                        out.append({"role": "tool", "content": body, "tool_name": id_to_name.get(b["tool_use_id"], "")})
                    elif b.get("type") == "text":
                        out.append({"role": "user", "content": b["text"]})
        return out

    # -- call ---------------------------------------------------------------------------------

    def _post(self, body: dict) -> dict:
        resp = self.session.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"ollama {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def complete(self, *, system, tools, messages, effort, max_tokens) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, messages),
            "tools": self._tools(tools),
            "stream": False,
            "options": {"num_ctx": self.num_ctx, "num_predict": max_tokens},
        }
        want_think = self.think and effort != "low" and self._supports_think is not False
        if want_think:
            body["think"] = True
        try:
            data = self._post(body)
            if want_think:
                self._supports_think = True
        except RuntimeError as exc:
            if want_think and "think" in str(exc).lower():
                self._supports_think = False
                body.pop("think", None)
                data = self._post(body)
            else:
                raise
        msg = data.get("message", {})
        content: list[dict[str, Any]] = []
        if msg.get("thinking"):
            content.append({"type": "thinking", "thinking": msg["thinking"]})
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_raw": args}
            content.append({"type": "tool_use", "id": f"call_{uuid.uuid4().hex[:12]}", "name": fn.get("name", ""), "input": args})
        has_calls = any(b["type"] == "tool_use" for b in content)
        stop = "tool_use" if has_calls else ("max_tokens" if data.get("done_reason") == "length" else "end_turn")
        usage = {
            "input_tokens": int(data.get("prompt_eval_count") or 0),
            "output_tokens": int(data.get("eval_count") or 0),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        cost = (usage["input_tokens"] * self.price_input + usage["output_tokens"] * self.price_output) / 1_000_000
        return Completion(content=content, stop_reason=stop, usage=usage, cost=cost)


# ---------------------------------------------------------------------------------------------
# OpenAI-compatible chat completions: Groq, Gemini, OpenRouter, vLLM, LM Studio, and so on.
# Priced synthetically like Ollama, since free tiers charge nothing.


@dataclass
class OpenAICompatBackend:
    model: str
    base_url: str
    api_key: str = ""
    price_input: float = 5.0
    price_output: float = 25.0
    session: Any = None
    timeout: float = 600.0
    name: str = "openai-compat"

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.session = self.session or requests.Session()

    @staticmethod
    def _messages(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if m["role"] == "assistant":
                text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
                calls = [
                    {"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                    for b in content if b.get("type") == "tool_use"
                ]
                msg: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            elif isinstance(content, str):
                out.append({"role": "user", "content": content})
            else:
                for b in content:
                    if b.get("type") == "tool_result":
                        body = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                        if b.get("is_error"):
                            body = "ERROR: " + body
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": body})
                    elif b.get("type") == "text":
                        out.append({"role": "user", "content": b["text"]})
        return out

    def complete(self, *, system, tools, messages, effort, max_tokens) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, messages),
            "tools": OllamaBackend._tools(tools),
            "max_tokens": max_tokens,
        }
        if "gpt-oss" in self.model:  # reasoning models on Groq/OpenAI-compatible hosts take graded effort
            body["reasoning_effort"] = effort
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = self.session.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"{self.name} {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        content: list[dict[str, Any]] = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_raw": args}
            content.append({"type": "tool_use", "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}", "name": fn.get("name", ""), "input": args})
        finish = choice.get("finish_reason")
        has_calls = any(b["type"] == "tool_use" for b in content)
        stop = "tool_use" if has_calls else ("max_tokens" if finish == "length" else "end_turn")
        u = data.get("usage") or {}
        usage = {
            "input_tokens": int(u.get("prompt_tokens") or 0),
            "output_tokens": int(u.get("completion_tokens") or 0),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        cost = (usage["input_tokens"] * self.price_input + usage["output_tokens"] * self.price_output) / 1_000_000
        return Completion(content=content, stop_reason=stop, usage=usage, cost=cost)


# ---------------------------------------------------------------------------------------------


def make_backend(settings) -> Backend:
    if settings.backend == "anthropic":
        return AnthropicBackend(model=settings.model, enable_fallbacks=settings.enable_fallbacks)
    if settings.backend == "ollama":
        return OllamaBackend(
            model=settings.model,
            url=settings.ollama_url,
            price_input=settings.synthetic_price_input,
            price_output=settings.synthetic_price_output,
            num_ctx=settings.ollama_num_ctx,
            think=settings.ollama_think,
        )
    if settings.backend == "openai":
        if not settings.openai_base_url:
            raise SystemExit("BACKEND=openai needs OPENAI_BASE_URL (e.g. https://api.groq.com/openai/v1)")
        return OpenAICompatBackend(
            model=settings.model,
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            price_input=settings.synthetic_price_input,
            price_output=settings.synthetic_price_output,
        )
    raise SystemExit(f"unknown BACKEND={settings.backend!r}; use 'anthropic', 'ollama' or 'openai'")
