"""The provider-neutral tool-calling contract (L4).

A `Provider` drives one chat session with a tool-capable model. The contract is deliberately
tiny and **turn-based** so the agent loop (Phase 2b) is a dozen lines and any backend — or a
scripted fake for tests — is trivial to implement:

    provider.start(system, tool_specs(TOOLS))
    turn = provider.send_user("free ~20GB but don't touch what I'm working on")
    while turn.wants_tools:
        results = [ToolResult(call.id, json.dumps(dispatch(call.name, call.arguments, ctx)))
                   for call in turn.tool_calls]
        turn = provider.send_tool_results(results)
    print(turn.text)                       # the model's grounded answer / proposal

The provider owns its own native conversation history internally (Anthropic content blocks,
Ollama messages, …); callers only ever pass provider-neutral values in and get an
`AssistantTurn` back. Nothing here can execute an action — a provider returns tool *requests*;
the loop decides what to run, and only the read-only tools in `reclaim.ai.tools` exist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from reclaim.ai.tools import Tool


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as advertised to the model: name, description, JSON-Schema for its input.

    Provider-neutral — each backend renders this into its own tool format (Anthropic
    `input_schema`, Ollama `function.parameters`, …)."""

    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to run one tool. `id` correlates the eventual `ToolResult`."""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of running a `ToolCall`, ready to hand back to the model.

    `content` is the tool's JSON output as a string. `is_error=True` marks a tool failure so
    the model can adapt rather than treating the error text as data."""

    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """One model turn: any prose it produced plus any tool calls it wants run.

    `raw` carries the provider-native assistant message so the provider can replay exact
    history (e.g. Anthropic thinking blocks) on the next request; callers should treat it as
    opaque."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str = ""
    raw: Any = field(default=None, compare=False)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def tool_specs(tools: Sequence[Tool]) -> list[ToolSpec]:
    """Adapt engine `Tool`s into provider-neutral `ToolSpec`s (drops the handler)."""
    return [ToolSpec(t.name, t.description, t.input_schema) for t in tools]


class Provider(ABC):
    """A tool-capable chat backend. Stateful across a single session.

    Lifecycle: `start()` once with the system prompt + tool specs, then alternate
    `send_user()` / `send_tool_results()`, each returning the model's next `AssistantTurn`.
    Implementations keep their own native message history between calls."""

    #: short, stable identifier for logs/UX ("claude", "ollama").
    name: str = "provider"
    #: the model id in use (for display / grounding provenance).
    model: str = ""

    @abstractmethod
    def start(self, system: str, tools: Sequence[ToolSpec]) -> None:
        """Begin a session: set the system prompt and the advertised tools. Resets history."""

    @abstractmethod
    def send_user(self, text: str) -> AssistantTurn:
        """Send a user message; return the model's next turn."""

    @abstractmethod
    def send_tool_results(self, results: Sequence[ToolResult]) -> AssistantTurn:
        """Return tool outputs for the pending tool calls; return the model's next turn."""
