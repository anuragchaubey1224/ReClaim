"""Safety Gate tests — apply-time re-validation (TOCTOU defense, I6).

The headline invariant: a plan built while a project was safe is REJECTED at the gate if
the project became work-in-progress in the meantime. The gate re-reads git on every call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from reclaim.core.model import Operation, Plan, Tier
from reclaim.core.project import ProjectAnalyzer
from reclaim.safety.gate import SafetyGate


def _project_with_unit(tmp_path: Path, unit: str = "node_modules") -> Path:
    proj = tmp_path / "proj"
    (proj / unit / "pkg").mkdir(parents=True)
    (proj / unit / "pkg" / "i.js").write_text("x" * 500)
    (proj / "package.json").write_text("{}")
    (proj / ".git").mkdir()
    return proj / unit


def _op(source: Path, kind: str = "node_modules") -> Operation:
    return Operation(source=source, kind=kind, size_allocated=500, file_count=1,
                     tier=Tier.REGENERABLE, regen_command="npm install")


def _gate(status_out: str = "") -> SafetyGate:
    """A gate whose analyzer reports `status_out` for `git status --porcelain`."""
    def fake_git(root: Path, args: tuple[str, ...]):
        out = status_out if tuple(args) == ("status", "--porcelain") else ""
        return subprocess.CompletedProcess(("git", *args), 0, out, "")
    return SafetyGate(analyzer_factory=lambda: ProjectAnalyzer(run_git=fake_git))


def test_gate_approves_clean_project(tmp_path: Path) -> None:
    unit = _project_with_unit(tmp_path)
    result = _gate(status_out="").validate(Plan((_op(unit),)))
    assert result.all_approved
    assert len(result.approved.operations) == 1


def test_gate_rejects_newly_dirty_project(tmp_path: Path) -> None:
    # Plan was fine at plan time; the project has since gone dirty → reject at apply time.
    unit = _project_with_unit(tmp_path)
    result = _gate(status_out=" M src/main.js\n").validate(Plan((_op(unit),)))
    assert not result.all_approved
    assert result.approved.is_empty
    assert "dirty" in result.rejected[0].reason


def test_gate_rejects_missing_source(tmp_path: Path) -> None:
    missing = tmp_path / "proj" / "node_modules"      # never created
    result = _gate().validate(Plan((_op(missing),)))
    assert result.rejected[0].reason == "source no longer exists"


def test_gate_rejects_protected_path(tmp_path: Path) -> None:
    data = tmp_path / "proj" / "data"
    data.mkdir(parents=True)
    result = _gate().validate(Plan((_op(data, kind="data"),)))
    assert not result.all_approved
    assert "data" in result.rejected[0].reason


def test_gate_rejects_unknown_unit(tmp_path: Path) -> None:
    src = tmp_path / "proj" / "src"
    src.mkdir(parents=True)
    result = _gate().validate(Plan((_op(src, kind="src"),)))
    assert "not a known reclaimable unit" in result.rejected[0].reason
