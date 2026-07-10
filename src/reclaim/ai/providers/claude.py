"""Claude BYOK provider — the default backend (docs/05 §Model, cost & privacy).

Talks to Claude through the official `anthropic` SDK using a manual tool-use loop (one
request per turn). BYOK: the user's own `ANTHROPIC_API_KEY` (the SDK also honours an
`ant auth login` profile). Only the small fact set the tools return ever leaves the machine —
never file contents.

The `anthropic` SDK is an **optional** dependency (`pip install "reclaim[ai]"`) imported
lazily, so the engine — and this module's pure helpers — load with the SDK absent (invariant
I7). The schema-conversion and response-parsing helpers are module-level and SDK-free, so the
whole provider is unit-testable by injecting a fake client.
"""

from __future__ import annotations

from typing import Any, Sequence

from reclaim.ai.providers.base import (
    AssistantTurn,
    Provider,
    ProviderUnavailable,
    ToolCall,
    ToolResult,
    ToolSpec,
)

# Per the claude-api guidance: default to Opus 4.8 with adaptive thinking. Non-streaming with
# a modest cap keeps a tool-calling turn well under the SDK's HTTP timeout.
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MAX_TOKENS = 16_000
DEFAULT_EFFORT = "high"


def specs_to_anthropic(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Render provider-neutral `ToolSpec`s into Anthropic tool definitions."""
    return [
        {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
        for t in tools
    ]


def parse_message(msg: Any) -> AssistantTurn:
    """Turn an Anthropic `Message` into an `AssistantTurn`.

    Duck-typed over `msg.content` blocks (`type` in {text, tool_use, thinking, …}) so a plain
    namespace works in tests. Thinking blocks are ignored here but preserved in `raw` for
    replay on the next request."""
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for block in msg.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name,
                                  arguments=dict(block.input)))
    return AssistantTurn(
        text="".join(text_parts),
        tool_calls=tuple(calls),
        stop_reason=getattr(msg, "stop_reason", "") or "",
        raw=msg.content,
    )


def results_to_content(results: Sequence[ToolResult]) -> list[dict[str, Any]]:
    """Render `ToolResult`s into an Anthropic user-turn `tool_result` content list."""
    return [
        {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content,
         **({"is_error": True} if r.is_error else {})}
        for r in results
    ]


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = DEFAULT_EFFORT,
        client: Any = None,
    ) -> None:
        """`client` is injectable for tests; if omitted the Anthropic SDK is imported lazily
        on first use. `api_key=None` lets the SDK resolve the key from the environment /
        `ant auth login` profile."""
        self.model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._effort = effort
        self._client = client
        self._system: str = ""
        self._tools: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ProviderUnavailable(
                    "the Claude provider needs the anthropic SDK — install with "
                    "`pip install \"reclaim[ai]\"`, or use the Ollama provider"
                ) from e
            try:
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except Exception as e:      # noqa: BLE001 - the SDK's own "no credentials" error
                # Constructing the client is offline: it only resolves credentials (explicit
                # key → ANTHROPIC_API_KEY → an `ant auth login` profile). Failing here means
                # there are none, whatever shape the SDK's exception takes.
                raise ProviderUnavailable(
                    "the Claude provider needs an API key — set ANTHROPIC_API_KEY (or run "
                    "`ant auth login`), or use `reclaim chat --ollama` for a local model"
                ) from e
        return self._client

    def preflight(self) -> None:
        """Resolve the SDK + credentials now, so `chat` fails before it scans."""
        self._ensure_client()

    def start(self, system: str, tools: Sequence[ToolSpec]) -> None:
        self._system = system
        self._tools = specs_to_anthropic(tools)
        self._messages = []

    def send_user(self, text: str) -> AssistantTurn:
        self._messages.append({"role": "user", "content": text})
        return self._run()

    def send_tool_results(self, results: Sequence[ToolResult]) -> AssistantTurn:
        self._messages.append({"role": "user", "content": results_to_content(results)})
        return self._run()

    def _run(self) -> AssistantTurn:
        client = self._ensure_client()
        msg = client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": self._effort},
            system=self._system,
            tools=self._tools,
            messages=self._messages,
        )
        # Append the full native content (incl. thinking + tool_use blocks) so the next
        # request replays exact history — required with adaptive thinking + tool use.
        self._messages.append({"role": "assistant", "content": msg.content})
        return parse_message(msg)
