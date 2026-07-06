"""CLI integration tests — the whole reclaim loop through typer, end to end.

Each invocation points the quarantine store at a tmp dir via RECLAIM_HOME, so nothing
touches the real `~/.reclaim`. Loose (non-git) project dirs are used so no real `git` is
shelled out — the classifier reads them as regenerable-with-no-project.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from reclaim.cli.app import app

runner = CliRunner()


@pytest.fixture()
def sandbox(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A scan target with two reclaimable units + a store home. Returns (target, env)."""
    target = tmp_path / "work"
    nm = target / "myapp" / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x" * 20_000)
    (target / "myapp" / "src").mkdir(parents=True)
    (target / "myapp" / "src" / "app.js").write_text("y" * 100)
    venv = target / "api" / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "pkg.py").write_text("z" * 15_000)
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    return target, env


def _unit_paths(target: Path) -> tuple[Path, Path]:
    return target / "myapp" / "node_modules", target / "api" / ".venv"


def test_plan_previews_without_mutating(sandbox) -> None:
    target, env = sandbox
    nm, venv = _unit_paths(target)

    result = runner.invoke(app, ["plan", str(target)], env=env)

    assert result.exit_code == 0
    assert "Plan" in result.output
    assert nm.exists() and venv.exists()          # preview never mutates


def test_apply_then_undo_roundtrip(sandbox) -> None:
    target, env = sandbox
    nm, venv = _unit_paths(target)

    applied = runner.invoke(app, ["apply", str(target), "--yes"], env=env)
    assert applied.exit_code == 0
    assert "reclaimed" in applied.output
    assert not nm.exists() and not venv.exists()  # moved to quarantine

    undone = runner.invoke(app, ["undo"], env=env)   # latest op
    assert undone.exit_code == 0
    assert "restored" in undone.output
    assert nm.exists() and venv.exists()          # back in place


def test_ls_lists_committed_op(sandbox) -> None:
    target, env = sandbox
    runner.invoke(app, ["apply", str(target), "--yes"], env=env)

    result = runner.invoke(app, ["ls"], env=env)
    assert result.exit_code == 0
    assert "committed" in result.output


def test_apply_aborts_without_confirmation(sandbox) -> None:
    target, env = sandbox
    nm, _ = _unit_paths(target)

    result = runner.invoke(app, ["apply", str(target)], input="n\n", env=env)

    assert result.exit_code == 0
    assert "aborted" in result.output
    assert nm.exists()                            # declined ⇒ nothing moved


def test_apply_nothing_to_reclaim(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {"RECLAIM_HOME": str(tmp_path / "home")}

    result = runner.invoke(app, ["apply", str(empty), "--yes"], env=env)
    assert result.exit_code == 0
    assert "nothing to reclaim" in result.output


def test_undo_with_nothing_present(tmp_path: Path) -> None:
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    result = runner.invoke(app, ["undo"], env=env)
    assert result.exit_code == 0
    assert "nothing to undo" in result.output


def test_free_target_limits_selection(sandbox) -> None:
    target, env = sandbox
    # node_modules is the larger unit (~20k) vs .venv (~15k); a small target takes only it.
    result = runner.invoke(app, ["plan", str(target), "--free", "10K"], env=env)
    assert result.exit_code == 0
    assert "node_modules" in result.output


def test_protect_saves_and_prefs_lists(sandbox) -> None:
    _, env = sandbox
    saved = runner.invoke(app, ["protect", "~/work/**", "--note", "mine"], env=env)
    assert saved.exit_code == 0
    assert "protected" in saved.output

    listed = runner.invoke(app, ["prefs"], env=env)
    assert listed.exit_code == 0
    assert "~/work/**" in listed.output


def test_protected_unit_is_not_reclaimed(sandbox) -> None:
    target, env = sandbox
    nm, venv = _unit_paths(target)

    protected = runner.invoke(app, ["protect", str(nm)], env=env)
    assert protected.exit_code == 0

    applied = runner.invoke(app, ["apply", str(target), "--yes"], env=env)
    assert applied.exit_code == 0
    assert nm.exists()                                # hard-protected by the saved rule
    assert not venv.exists()                          # the unprotected unit is still reclaimed
