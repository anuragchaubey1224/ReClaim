"""`reclaim chat` tests — the AI loop end to end, driven by a scripted fake provider.

A proposal routes through the *real* Safety Gate + quarantine store (temp `RECLAIM_HOME`), so
these prove the AI path reclaims real on-disk units reversibly and honours confirmation —
without any network. The engine's safety is unchanged: the model only proposes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from typer.testing import CliRunner

from reclaim.ai.agent import Agent
from reclaim.ai.providers.base import AssistantTurn, Provider, ToolCall, ToolResult, ToolSpec
from reclaim.ai.tools import ToolContext
from reclaim.cli.app import app, run_chat
from reclaim.core.model import Candidate, OpState, ScanResult, Tier
from reclaim.core.preferences import PreferenceStore
from reclaim.core.quarantine import QuarantineStore

runner = CliRunner()


class FakeProvider(Provider):
    name = "fake"
    model = "fake-1"

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = list(turns)

    def start(self, system: str, tools: Sequence[ToolSpec]) -> None:
        pass

    def send_user(self, text: str) -> AssistantTurn:
        return self._turns.pop(0)

    def send_tool_results(self, results: Sequence[ToolResult]) -> AssistantTurn:
        return self._turns.pop(0)


def _propose_then_say(paths: list[str], said: str) -> FakeProvider:
    return FakeProvider([
        AssistantTurn(text="", tool_calls=(ToolCall("1", "propose_plan", {"paths": paths}),)),
        AssistantTurn(text=said),
    ])


def _real_unit(tmp_path: Path) -> Path:
    """A real on-disk node_modules the quarantine store can actually move."""
    nm = tmp_path / "work" / "myapp" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x" * 20_000)
    return nm


def _ctx_for(nm: Path) -> ToolContext:
    cand = Candidate(nm, "node_modules", 20_000, 20_000, 1, tier=Tier.REGENERABLE,
                     regen_command="npm install", reason="regenerable")
    return ToolContext(ScanResult(
        roots=(nm.parent,), total_allocated=20_000, total_apparent=20_000, file_count=1,
        dir_count=1, error_count=0, elapsed_seconds=0.0, candidates=(cand,), projects=(),
    ))


def _reads(*lines: str):
    it = iter([*lines, None])
    return lambda: next(it)


# -- run_chat: the testable core ---------------------------------------------

def test_chat_proposes_gates_applies_and_is_undoable(tmp_path: Path) -> None:
    nm = _real_unit(tmp_path)
    agent = Agent(_propose_then_say([str(nm)], "Proposed."), _ctx_for(nm))
    store = QuarantineStore(home=tmp_path / "home")
    writes: list[str] = []

    run_chat(agent, read=_reads("free node_modules"), write=writes.append,
             store=store, confirm=lambda plan: True)

    assert not nm.exists()                                    # moved to quarantine
    committed = [o for o in store.list_ops() if o.state is OpState.COMMITTED]
    assert len(committed) == 1
    assert any("reclaimed" in w for w in writes)

    store.undo(committed[-1].op_id)
    assert nm.exists()                                       # restored byte-identically
    assert (nm / "index.js").read_text() == "x" * 20_000


def test_chat_declined_confirmation_touches_nothing(tmp_path: Path) -> None:
    nm = _real_unit(tmp_path)
    agent = Agent(_propose_then_say([str(nm)], "Proposed."), _ctx_for(nm))
    store = QuarantineStore(home=tmp_path / "home")
    writes: list[str] = []

    run_chat(agent, read=_reads("free it"), write=writes.append,
             store=store, confirm=lambda plan: False)

    assert nm.exists()                                       # declined ⇒ nothing moved
    assert [o for o in store.list_ops() if o.state is OpState.COMMITTED] == []
    assert any("aborted" in w for w in writes)


def test_chat_plain_answer_applies_nothing(tmp_path: Path) -> None:
    nm = _real_unit(tmp_path)
    provider = FakeProvider([
        AssistantTurn(text="", tool_calls=(ToolCall("1", "list_reclaimable", {}),)),
        AssistantTurn(text="You have ~20 KB of reclaimable node_modules."),
    ])
    agent = Agent(provider, _ctx_for(nm))
    store = QuarantineStore(home=tmp_path / "home")
    writes: list[str] = []

    run_chat(agent, read=_reads("what can I clean?"), write=writes.append,
             store=store, confirm=lambda plan: True)

    assert any("20 KB" in w for w in writes)
    assert nm.exists()                                       # no proposal ⇒ no apply
    assert store.list_ops() == []


def test_chat_survives_provider_error(tmp_path: Path) -> None:
    class Boom(Provider):
        name = "boom"
        model = "x"

        def start(self, system, tools) -> None: ...
        def send_user(self, text):
            raise RuntimeError("no API key")
        def send_tool_results(self, results):
            raise RuntimeError("unreachable")

    writes: list[str] = []
    run_chat(Agent(Boom(), _ctx_for(_real_unit(tmp_path))),
             read=_reads("free it"), write=writes.append,
             store=QuarantineStore(home=tmp_path / "home"), confirm=lambda plan: True)
    assert any("no API key" in w for w in writes)             # error reported, loop survived


# -- preference memory in the chat path --------------------------------------

def test_chat_gate_blocks_preference_added_after_scan(tmp_path: Path) -> None:
    # The in-memory scan (ctx) predates the rule, so the model still proposes the unit;
    # the apply-time gate (fed the rule) must block it — defense in depth.
    nm = _real_unit(tmp_path)
    prefs = PreferenceStore(tmp_path / "home" / "preferences.json")
    prefs.add(str(nm.parent))                                # protect the enclosing dir
    agent = Agent(_propose_then_say([str(nm)], "Proposed."), _ctx_for(nm))
    store = QuarantineStore(home=tmp_path / "home")
    writes: list[str] = []

    run_chat(agent, read=_reads("free it"), write=writes.append, store=store,
             confirm=lambda plan: True, preferences=prefs)

    assert nm.exists()                                      # gate rejected it
    assert [o for o in store.list_ops() if o.state is OpState.COMMITTED] == []
    assert any("blocked" in w for w in writes)


def test_chat_save_preference_persists(tmp_path: Path) -> None:
    nm = _real_unit(tmp_path)
    prefs = PreferenceStore(tmp_path / "home" / "preferences.json")
    ctx = ToolContext(_ctx_for(nm).scan, preferences=prefs)
    provider = FakeProvider([
        AssistantTurn(text="", tool_calls=(
            ToolCall("1", "save_preference", {"pattern": "~/work/**"}),)),
        AssistantTurn(text="Protected ~/work."),
    ])
    writes: list[str] = []

    run_chat(Agent(provider, ctx), read=_reads("never touch ~/work"), write=writes.append,
             store=QuarantineStore(home=tmp_path / "home"), confirm=lambda plan: True,
             preferences=prefs)

    assert prefs.matches(Path.home() / "work" / "x") is not None
    assert any("saved rule" in w for w in writes)


# -- chat command wiring (fake provider injected) -----------------------------

def test_chat_command_end_to_end(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "work"
    nm = target / "myapp" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x" * 20_000)

    monkeypatch.setattr(
        "reclaim.cli.app._make_provider",
        lambda use_ollama, model: _propose_then_say([str(nm)], "Proposed removing it."),
    )
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    result = runner.invoke(app, ["chat", str(target), "--yes"], input="free it\n", env=env)

    assert result.exit_code == 0, result.output
    assert "reclaimed" in result.output
    assert not nm.exists()
