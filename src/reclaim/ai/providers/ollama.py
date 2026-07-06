"""Ollama provider — the fully-local, zero-network-egress fallback (docs/05 §Model, cost).

Talks to a local Ollama daemon's `/api/chat` endpoint (tool-calling supported by models like
llama3.1). Nothing ever leaves the machine — the privacy floor of the product. Uses only the
standard library (`urllib`), so there is no extra dependency to install.

The HTTP call is behind an injectable `transport` so the whole provider is unit-testable
without a running daemon; the schema-conversion and message-parsing helpers are pure.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable, Sequence

from reclaim.ai.providers.base import (
    AssistantTurn,
    Provider,
    ToolCall,
    ToolResult,
    ToolSpec,
)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"

# transport(url, payload) -> decoded JSON response. Injected in tests; the default POSTs JSON.
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def _http_transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:            # noqa: S310 - localhost daemon
        return json.loads(resp.read().decode("utf-8"))


def specs_to_ollama(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Render provider-neutral `ToolSpec`s into Ollama function-tool definitions."""
    return [
        {"type": "function",
         "function": {"name": t.name, "description": t.description,
                      "parameters": dict(t.input_schema)}}
        for t in tools
    ]


def parse_ollama_message(message: dict[str, Any]) -> AssistantTurn:
    """Turn an Ollama `message` object into an `AssistantTurn`.

    Ollama tool calls carry no id, so we synthesise stable positional ids (`call_0`, …).
    `arguments` is usually an object but may arrive as a JSON string — both are handled."""
    calls: list[ToolCall] = []
    for i, tc in enumerate(message.get("tool_calls") or []):
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            args = json.loads(args) if args.strip() else {}
        calls.append(ToolCall(id=f"call_{i}", name=fn.get("name", ""),
                              arguments=dict(args)))
    return AssistantTurn(
        text=message.get("content", "") or "",
        tool_calls=tuple(calls),
        stop_reason="tool_use" if calls else "end_turn",
        raw=message,
    )


def results_to_messages(results: Sequence[ToolResult]) -> list[dict[str, Any]]:
    """Render `ToolResult`s into Ollama `role: tool` messages (in order)."""
    return [{"role": "tool", "content": r.content} for r in results]


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport | None = None,
    ) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/api/chat"
        self._transport = transport or _http_transport
        self._tools: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []

    def start(self, system: str, tools: Sequence[ToolSpec]) -> None:
        self._tools = specs_to_ollama(tools)
        self._messages = [{"role": "system", "content": system}] if system else []

    def send_user(self, text: str) -> AssistantTurn:
        self._messages.append({"role": "user", "content": text})
        return self._run()

    def send_tool_results(self, results: Sequence[ToolResult]) -> AssistantTurn:
        self._messages.extend(results_to_messages(results))
        return self._run()

    def _run(self) -> AssistantTurn:
        payload = {"model": self.model, "messages": self._messages,
                   "tools": self._tools, "stream": False}
        response = self._transport(self._url, payload)
        message = response.get("message", {})
        # Replay exact assistant history (content + any tool_calls) on the next request.
        self._messages.append(message)
        return parse_ollama_message(message)
