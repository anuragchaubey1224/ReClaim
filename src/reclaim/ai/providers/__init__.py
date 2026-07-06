"""Provider abstraction — the tool-calling contract the agent loop is written against.

The engine depends on this *contract*, never on a specific vendor (docs/05 §Model, cost &
privacy). Two backends ship:

  * `ClaudeProvider` — BYOK Claude via the official Anthropic SDK (the default). Only minimal
    facts (path / size / tier / git-state) ever leave the machine — never file contents.
  * `OllamaProvider` — a local model over Ollama's HTTP API. Nothing leaves the machine.

Both implement `base.Provider`. Import the concrete providers from their own modules
(`reclaim.ai.providers.claude` / `.ollama`) so importing this package never pulls in the
optional `anthropic` SDK.
"""

from reclaim.ai.providers.base import (
    AssistantTurn,
    Provider,
    ToolCall,
    ToolResult,
    ToolSpec,
    tool_specs,
)

__all__ = [
    "AssistantTurn",
    "Provider",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "tool_specs",
]
