"""Provider-contract tests — the tool-calling abstraction, both backends, no network.

Claude is exercised through an injected fake client; Ollama through an injected fake
transport. Together they prove the contract is genuinely provider-neutral: the same
start → send_user → send_tool_results loop drives both.
"""

from __future__ import annotations

from types import SimpleNamespace

from reclaim.ai.providers import (
    AssistantTurn,
    Provider,
    ToolCall,
    ToolResult,
    ToolSpec,
    tool_specs,
)
from reclaim.ai.providers.claude import (
    ClaudeProvider,
    parse_message,
    results_to_content,
    specs_to_anthropic,
)
from reclaim.ai.providers.ollama import (
    OllamaProvider,
    parse_ollama_message,
    results_to_messages,
    specs_to_ollama,
)
from reclaim.ai.providers.openai_compatible import (
    OpenAICompatibleProvider,
    parse_openai_message,
    results_to_openai_messages,
    specs_to_openai,
)
from reclaim.ai.tools import TOOLS


# -- contract adapter ---------------------------------------------------------

def test_tool_specs_adapts_engine_tools() -> None:
    specs = tool_specs(TOOLS)
    assert len(specs) == len(TOOLS)
    assert all(isinstance(s, ToolSpec) for s in specs)
    names = {s.name for s in specs}
    assert names == {"list_reclaimable", "get_project_facts", "explain_unit", "estimate_plan"}
    facts = next(s for s in specs if s.name == "get_project_facts")
    assert facts.input_schema["required"] == ["path"]


def test_assistant_turn_wants_tools() -> None:
    assert not AssistantTurn(text="hi").wants_tools
    assert AssistantTurn(text="", tool_calls=(ToolCall("1", "x", {}),)).wants_tools


# -- Claude: pure helpers -----------------------------------------------------

def test_specs_to_anthropic_shape() -> None:
    out = specs_to_anthropic(tool_specs(TOOLS))
    assert {"name", "description", "input_schema"} <= set(out[0])
    assert out[0]["input_schema"]["type"] == "object"


def test_parse_message_splits_text_and_tool_use() -> None:
    msg = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="thinking", thinking="…"),          # ignored
            SimpleNamespace(type="text", text="Let me check. "),
            SimpleNamespace(type="tool_use", id="tu_1",
                            name="list_reclaimable", input={"kind": "node_modules"}),
        ],
    )
    turn = parse_message(msg)
    assert turn.text == "Let me check. "
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls[0].id == "tu_1"
    assert turn.tool_calls[0].name == "list_reclaimable"
    assert turn.tool_calls[0].arguments == {"kind": "node_modules"}
    assert turn.raw is msg.content                                    # for replay


def test_results_to_content_marks_errors() -> None:
    out = results_to_content([ToolResult("tu_1", "{}"),
                              ToolResult("tu_2", "boom", is_error=True)])
    assert out[0] == {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"}
    assert out[1]["is_error"] is True


# -- Claude: full loop via a fake SDK client ----------------------------------

class _FakeMessages:
    def __init__(self, scripted: list) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _FakeAnthropic:
    def __init__(self, scripted: list) -> None:
        self.messages = _FakeMessages(scripted)


def _text_msg(text: str):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=text)])


def _tool_msg(call_id: str, name: str, args: dict):
    return SimpleNamespace(stop_reason="tool_use",
                           content=[SimpleNamespace(type="tool_use", id=call_id,
                                                    name=name, input=args)])


def test_claude_is_a_provider() -> None:
    assert isinstance(ClaudeProvider(client=_FakeAnthropic([])), Provider)


def test_claude_tool_loop_and_request_shape() -> None:
    fake = _FakeAnthropic([
        _tool_msg("tu_1", "list_reclaimable", {"tier": "green"}),
        _text_msg("You can free 800 MB."),
    ])
    provider = ClaudeProvider(client=fake)
    provider.start("SYS", tool_specs(TOOLS))

    turn1 = provider.send_user("what can I clean?")
    assert turn1.wants_tools
    assert turn1.tool_calls[0].name == "list_reclaimable"

    turn2 = provider.send_tool_results([ToolResult("tu_1", '{"total_bytes": 800}')])
    assert not turn2.wants_tools
    assert turn2.text == "You can free 800 MB."

    # First request carried the model, adaptive thinking, system prompt, and all 3 tools.
    first = fake.messages.calls[0]
    assert first["model"] == "claude-opus-4-8"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"}
    assert first["system"] == "SYS"
    assert {t["name"] for t in first["tools"]} == {
        "list_reclaimable", "get_project_facts", "explain_unit", "estimate_plan"}
    # History replays exact assistant content: user, assistant(tool_use), user(result), assistant.
    assert len(provider._messages) == 4
    assert provider._messages[1]["role"] == "assistant"
    assert provider._messages[2]["content"][0]["type"] == "tool_result"


# -- Ollama: pure helpers -----------------------------------------------------

def test_specs_to_ollama_shape() -> None:
    out = specs_to_ollama(tool_specs(TOOLS))
    assert out[0]["type"] == "function"
    assert {"name", "description", "parameters"} <= set(out[0]["function"])


def test_parse_ollama_message_object_arguments() -> None:
    msg = {"role": "assistant", "content": "checking",
           "tool_calls": [{"function": {"name": "list_reclaimable",
                                        "arguments": {"tier": "green"}}}]}
    turn = parse_ollama_message(msg)
    assert turn.text == "checking"
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls[0].id == "call_0"          # synthesised (Ollama has no ids)
    assert turn.tool_calls[0].arguments == {"tier": "green"}


def test_parse_ollama_message_json_string_arguments() -> None:
    msg = {"content": "",
           "tool_calls": [{"function": {"name": "estimate_plan",
                                        "arguments": '{"paths": ["/p/nm"]}'}}]}
    turn = parse_ollama_message(msg)
    assert turn.tool_calls[0].arguments == {"paths": ["/p/nm"]}


def test_parse_ollama_message_no_tools_is_end_turn() -> None:
    turn = parse_ollama_message({"content": "all done"})
    assert turn.stop_reason == "end_turn"
    assert not turn.wants_tools


def test_results_to_messages_role_tool() -> None:
    out = results_to_messages([ToolResult("call_0", '{"ok": true}')])
    assert out == [{"role": "tool", "content": '{"ok": true}'}]


# -- Ollama: full loop via a fake transport -----------------------------------

def test_ollama_tool_loop_and_payload_shape() -> None:
    scripted = [
        {"message": {"role": "assistant", "content": "",
                     "tool_calls": [{"function": {"name": "list_reclaimable",
                                                  "arguments": {}}}]}},
        {"message": {"role": "assistant", "content": "Freed 800 MB."}},
    ]
    sent: list[dict] = []

    def transport(url: str, payload: dict) -> dict:
        # Snapshot messages — the provider reuses one live list (real HTTP serialises
        # immediately, so its later mutation is invisible to the wire).
        sent.append({**payload, "messages": list(payload["messages"])})
        return scripted[len(sent) - 1]

    provider = OllamaProvider(transport=transport)
    assert isinstance(provider, Provider)
    provider.start("SYS", tool_specs(TOOLS))

    turn1 = provider.send_user("what can I clean?")
    assert turn1.tool_calls[0].name == "list_reclaimable"

    turn2 = provider.send_tool_results([ToolResult("call_0", '{"total_bytes": 800}')])
    assert turn2.text == "Freed 800 MB."

    first = sent[0]
    assert first["model"] == "llama3.1"
    assert first["stream"] is False
    assert first["messages"][0] == {"role": "system", "content": "SYS"}
    assert {t["function"]["name"] for t in first["tools"]} == {
        "list_reclaimable", "get_project_facts", "explain_unit", "estimate_plan"}
    # Second request includes the tool result message in order.
    assert sent[1]["messages"][-1] == {"role": "tool", "content": '{"total_bytes": 800}'}


# -- OpenAI-compatible: pure helpers (OpenRouter / OpenAI / local) -------------

def test_specs_to_openai_shape() -> None:
    out = specs_to_openai(tool_specs(TOOLS))
    assert out[0]["type"] == "function"
    assert {"name", "description", "parameters"} <= set(out[0]["function"])


def test_parse_openai_message_with_tool_calls() -> None:
    msg = {"role": "assistant", "content": "checking",
           "tool_calls": [{"id": "call_1", "type": "function",
                           "function": {"name": "list_reclaimable",
                                        "arguments": '{"tier": "green"}'}}]}
    turn = parse_openai_message(msg)
    assert turn.text == "checking"
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls[0].id == "call_1"          # real id round-trips
    assert turn.tool_calls[0].arguments == {"tier": "green"}


def test_parse_openai_message_null_content() -> None:
    msg = {"role": "assistant", "content": None,
           "tool_calls": [{"id": "call_2", "function": {"name": "estimate_plan",
                                                        "arguments": ""}}]}
    turn = parse_openai_message(msg)
    assert turn.text == ""                             # null content → empty string
    assert turn.tool_calls[0].arguments == {}


def test_parse_openai_message_no_tools_is_end_turn() -> None:
    turn = parse_openai_message({"role": "assistant", "content": "all done"})
    assert turn.stop_reason == "end_turn" and not turn.wants_tools


def test_results_to_openai_messages_keyed_by_id() -> None:
    out = results_to_openai_messages([ToolResult("call_1", '{"ok": true}')])
    assert out == [{"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'}]


def test_openai_compatible_tool_loop_and_payload() -> None:
    scripted = [
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "list_reclaimable", "arguments": "{}"}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Freed 800 MB."}}]},
    ]
    sent: list[dict] = []

    def transport(url: str, payload: dict) -> dict:
        sent.append({**payload, "messages": list(payload["messages"])})
        return scripted[len(sent) - 1]

    provider = OpenAICompatibleProvider(
        model="anthropic/claude-opus-4-8", base_url="https://openrouter.ai/api/v1",
        name="openrouter", transport=transport)
    assert isinstance(provider, Provider)
    provider.start("SYS", tool_specs(TOOLS))

    turn1 = provider.send_user("what can I clean?")
    assert turn1.tool_calls[0].name == "list_reclaimable"
    turn2 = provider.send_tool_results([ToolResult("call_1", '{"total_bytes": 800}')])
    assert turn2.text == "Freed 800 MB."

    first = sent[0]
    assert first["model"] == "anthropic/claude-opus-4-8" and first["stream"] is False
    assert first["messages"][0] == {"role": "system", "content": "SYS"}
    assert {t["function"]["name"] for t in first["tools"]} == {
        "list_reclaimable", "get_project_facts", "explain_unit", "estimate_plan"}
    assert sent[1]["messages"][-1] == {
        "role": "tool", "tool_call_id": "call_1", "content": '{"total_bytes": 800}'}


def test_openai_compatible_sets_bearer_auth() -> None:
    p = OpenAICompatibleProvider(model="m", base_url="https://x/v1", api_key="sk-123")
    assert p._headers["Authorization"] == "Bearer sk-123"
    # No key (e.g. local server) → no auth header sent.
    assert "Authorization" not in OpenAICompatibleProvider(
        model="m", base_url="http://localhost:8000/v1")._headers
