"""Thin LLM layer.

The agent talks to one small interface, `LLMClient.complete`, and messages are kept
in a single canonical format (Anthropic-style content blocks). Adapters translate
that format for each provider. This is all the abstraction the project needs to
benchmark models across providers, so no framework is used.

Canonical messages:
    {"role": "user", "content": "text"}
    {"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from emporio.agent.prompt import SystemPrompt
from emporio.core.tools import Tool


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def as_message(self) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        for call in self.tool_calls:
            blocks.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            )
        return {"role": "assistant", "content": blocks}


class LLMClient(Protocol):
    model: str

    def complete(
        self, system: SystemPrompt, messages: list[dict[str, Any]], tools: Sequence[Tool]
    ) -> LLMResponse: ...


# --- Anthropic ----------------------------------------------------------------


class AnthropicClient:
    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 1024):
        import anthropic

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def complete(self, system, messages, tools) -> LLMResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            # The static part carries the whole policy manual: cache it across turns.
            system=[
                {"type": "text", "text": system.static, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": system.dynamic},
            ],
            messages=messages,
            tools=[
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        usage = response.usage
        return LLMResponse(
            text=text.strip(),
            tool_calls=calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )


# --- OpenAI-compatible (OpenAI, Groq, Gemini's OpenAI endpoint, local servers) ---


def to_openai_messages(
    system: SystemPrompt | str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": str(system)}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "".join(b["text"] for b in content if b["type"] == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in content
                if b["type"] == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in content:
                if b["type"] == "tool_result":
                    out.append(
                        {"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]}
                    )
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


class OpenAICompatClient:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL") or None,
        )

    def complete(self, system, messages, tools) -> LLMResponse:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=to_openai_messages(system, messages),
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ],
        )
        choice = response.choices[0].message
        calls = [
            ToolCall(
                id=c.id,
                name=c.function.name,
                arguments=json.loads(c.function.arguments or "{}"),
            )
            for c in (choice.tool_calls or [])
        ]
        usage = response.usage
        return LLMResponse(
            text=(choice.content or "").strip(),
            tool_calls=calls,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


# --- Scripted (tests and offline demos) ------------------------------------------


Step = LLMResponse | Callable[[list[dict[str, Any]]], LLMResponse]


class ScriptedLLM:
    """Replays a fixed list of responses. A step can be a callable that sees the messages."""

    model = "scripted"

    def __init__(self, steps: list[Step]):
        self.steps = list(steps)
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, system, messages, tools) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        if not self.steps:
            raise RuntimeError("ScriptedLLM ran out of steps")
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


def make_client(provider: str | None = None, model: str | None = None) -> LLMClient:
    provider = (provider or os.getenv("LLM_PROVIDER") or "anthropic").lower()
    model = model or os.getenv("LLM_MODEL") or "claude-haiku-5-5"
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it."
            )
        return AnthropicClient(model)
    if provider == "openai":
        return OpenAICompatClient(model)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use 'anthropic' or 'openai'.")
