"""Agent-loop tests — the grounding loop driven by a scripted fake provider, no network.

These prove the loop's behaviour and its safety shape: read-only tools are dispatched and fed
back, `propose_plan` yields a plan that excludes protected paths, tool errors don't crash the
run, and a runaway model is bounded by max_steps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from reclaim.ai.agent import SYSTEM_PROMPT, Agent
from reclaim.ai.providers.base import (
    AssistantTurn,
    Provider,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from reclaim.ai.tools import ToolContext
from reclaim.core.model import Candidate, ScanResult, Tier


# -- fakes + builders ---------------------------------------------------------

class FakeProvider(Provider):
    """Replays scripted `AssistantTurn`s and records what the loop sent it."""

    name = "fake"
    model = "fake-1"

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.system: str | None = None
        self.tools: list[ToolSpec] | None = None
        self.user_messages: list[str] = []
        self.tool_result_batches: list[list[ToolResult]] = []

    def start(self, system: str, tools: Sequence[ToolSpec]) -> None:
        self.system = system
        self.tools = list(tools)

    def send_user(self, text: str) -> AssistantTurn:
        self.user_messages.append(text)
        return self._turns.pop(0)

    def send_tool_results(self, results: Sequence[ToolResult]) -> AssistantTurn:
        self.tool_result_batches.append(list(results))
        return self._turns.pop(0)


class LoopProvider(Provider):
    """Always asks for another tool call — used to exercise the max_steps backstop."""

    name = "loop"
    model = "loop-1"

    def start(self, system: str, tools: Sequence[ToolSpec]) -> None:
        pass

    def _loop(self) -> AssistantTurn:
        return AssistantTurn(text="", tool_calls=(ToolCall("1", "list_reclaimable", {}),))

    def send_user(self, text: str) -> AssistantTurn:
        return self._loop()

    def send_tool_results(self, results: Sequence[ToolResult]) -> AssistantTurn:
        return self._loop()


def _tool_turn(*calls: ToolCall) -> AssistantTurn:
    return AssistantTurn(text="", tool_calls=tuple(calls))


def _cand(path: str, size: int, *, tier: Tier = Tier.REGENERABLE) -> Candidate:
    p = Path(path)
    return Candidate(p, p.name, size, size, 1, tier=tier, regen_command="rebuild",
                     reason="regenerable")


def _ctx(*candidates: Candidate) -> ToolContext:
    return ToolContext(ScanResult(
        roots=(Path("/p"),), total_allocated=0, total_apparent=0, file_count=0,
        dir_count=0, error_count=0, elapsed_seconds=0.0,
        candidates=candidates, projects=(),
    ))


# -- tests --------------------------------------------------------------------

def test_start_advertises_readonly_tools_plus_propose() -> None:
    provider = FakeProvider([AssistantTurn(text="hi")])
    Agent(provider, _ctx()).send("hello")
    assert provider.system == SYSTEM_PROMPT
    assert {t.name for t in provider.tools} == {
        "list_reclaimable", "get_project_facts", "estimate_plan", "propose_plan"}


def test_dispatches_readonly_tool_then_answers() -> None:
    provider = FakeProvider([
        _tool_turn(ToolCall("1", "list_reclaimable", {})),
        AssistantTurn(text="You can free 300 B."),
    ])
    reply = Agent(provider, _ctx(_cand("/p/nm", 300))).send("what can I clean?")
    assert reply.text == "You can free 300 B."
    assert reply.proposal is None
    assert reply.steps == 1
    fed_back = json.loads(provider.tool_result_batches[0][0].content)
    assert fed_back["total_matched"] == 1               # grounded on the real fact


def test_propose_plan_builds_proposal() -> None:
    provider = FakeProvider([
        _tool_turn(ToolCall("1", "propose_plan", {"paths": ["/p/nm"]})),
        AssistantTurn(text="Proposed removing node_modules."),
    ])
    reply = Agent(provider, _ctx(_cand("/p/nm", 300))).send("free node_modules")
    assert reply.proposal is not None
    assert reply.proposal.plan.total_bytes == 300
    assert reply.proposal.is_actionable
    content = json.loads(provider.tool_result_batches[0][0].content)
    assert content["proposed"] is True and content["item_count"] == 1
    assert "Nothing has been removed" in content["note"]


def test_propose_plan_excludes_protected_path() -> None:
    provider = FakeProvider([
        _tool_turn(ToolCall("1", "propose_plan", {"paths": ["/p/nm", "/p/secret"]})),
        AssistantTurn(text="done"),
    ])
    ctx = _ctx(_cand("/p/nm", 300), _cand("/p/secret", 999, tier=Tier.IRREPLACEABLE))
    reply = Agent(provider, ctx).send("free everything")
    assert reply.proposal.plan.total_bytes == 300       # 🔴 never enters the plan
    assert [e["path"] for e in reply.proposal.excluded] == ["/p/secret"]


def test_unknown_tool_yields_error_result_and_continues() -> None:
    provider = FakeProvider([
        _tool_turn(ToolCall("1", "delete_everything", {})),
        AssistantTurn(text="I can't do that, but here's what I can clean."),
    ])
    reply = Agent(provider, _ctx()).send("nuke it")
    assert "can't" in reply.text
    res = provider.tool_result_batches[0][0]
    assert res.is_error is True
    assert "error" in json.loads(res.content)


def test_max_steps_truncates_runaway() -> None:
    reply = Agent(LoopProvider(), _ctx(_cand("/p/nm", 300)), max_steps=3).send("loop")
    assert reply.truncated is True
    assert reply.steps == 3
