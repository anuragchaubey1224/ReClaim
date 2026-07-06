"""OpenAI-compatible provider — one backend for OpenRouter, OpenAI, and local servers.

The engine depends only on the tool-calling *contract* (docs/05 §Model, cost & privacy), so
"bring your own provider" is just another `Provider` implementation. Any service that speaks
the OpenAI `POST /chat/completions` shape with function-calling works through this one class:

  * OpenRouter — `base_url="https://openrouter.ai/api/v1"`, `OPENROUTER_API_KEY`
  * OpenAI     — `base_url="https://api.openai.com/v1"`, `OPENAI_API_KEY`
  * Groq / Together / Fireworks — their `/v1` base URL + key
  * Local vLLM / LM Studio / llama.cpp — a localhost base URL, usually no key

Uses only the standard library (`urllib`) — no OpenAI SDK, exactly like the Ollama backend.
The HTTP call sits behind an injectable transport, and the schema/response helpers are pure,
so the whole provider is unit-testable without a network or a key.

Claude (`ClaudeProvider`) remains the default and recommended backend; this is the opt-in
"any key" path.
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

# transport(url, payload) -> decoded JSON response. Injected in tests; the default POSTs JSON
# with the provider's auth headers.
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def specs_to_openai(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Render provider-neutral `ToolSpec`s into OpenAI function-tool definitions."""
    return [
        {"type": "function",
         "function": {"name": t.name, "description": t.description,
                      "parameters": dict(t.input_schema)}}
        for t in tools
    ]


def parse_openai_message(message: dict[str, Any]) -> AssistantTurn:
    """Turn an OpenAI `choices[0].message` into an `AssistantTurn`.

    Tool calls carry real ids here (`call_...`), which round-trip as `tool_call_id`.
    `arguments` is a JSON string per the OpenAI spec, decoded to a dict."""
    calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        raw = fn.get("arguments") or ""
        args = json.loads(raw) if raw.strip() else {}
        calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""),
                              arguments=dict(args)))
    return AssistantTurn(
        text=message.get("content") or "",
        tool_calls=tuple(calls),
        stop_reason="tool_use" if calls else "end_turn",
        raw=message,
    )


def results_to_openai_messages(results: Sequence[ToolResult]) -> list[dict[str, Any]]:
    """Render `ToolResult`s into OpenAI `role: tool` messages, keyed by tool_call_id."""
    return [{"role": "tool", "tool_call_id": r.call_id, "content": r.content}
            for r in results]


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        name: str = "openai-compatible",
        transport: Transport | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._headers = headers
        self._transport = transport or self._default_transport
        self._tools: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []

    def _default_transport(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers)
        with urllib.request.urlopen(req) as resp:            # noqa: S310 - user-chosen host
            return json.loads(resp.read().decode("utf-8"))

    def start(self, system: str, tools: Sequence[ToolSpec]) -> None:
        self._tools = specs_to_openai(tools)
        self._messages = [{"role": "system", "content": system}] if system else []

    def send_user(self, text: str) -> AssistantTurn:
        self._messages.append({"role": "user", "content": text})
        return self._run()

    def send_tool_results(self, results: Sequence[ToolResult]) -> AssistantTurn:
        self._messages.extend(results_to_openai_messages(results))
        return self._run()

    def _run(self) -> AssistantTurn:
        payload = {"model": self.model, "messages": self._messages,
                   "tools": self._tools, "stream": False}
        response = self._transport(self._url, payload)
        message = response["choices"][0]["message"]
        # Replay exact assistant history (content + tool_calls) on the next request.
        self._messages.append(message)
        return parse_openai_message(message)
