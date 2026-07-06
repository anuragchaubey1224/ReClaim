"""Project analyzer tests — root/type detection, defensive git-state, dormancy.

Git and the clock are injected, so these are deterministic and don't shell out to a real
`git` or depend on wall-clock time.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from reclaim.core.model import GitStatus
from reclaim.core.project import ProjectAnalyzer

# The exact git argument tuples the analyzer issues, in order.
STATUS = ("status", "--porcelain")
SYMREF = ("symbolic-ref", "-q", "HEAD")
UPSTREAM = ("rev-parse", "--abbrev-ref", "@{u}")
AHEAD = ("rev-list", "--count", "@{u}..HEAD")


def _fake_git(responses: dict[tuple[str, ...], tuple[int, str]]):
    """A git runner that returns canned (returncode, stdout) per argument tuple.

    Unspecified calls default to (0, "") — which, for the analyzer's logic, reads as
    'clean' (empty status, has HEAD, has upstream, 0 ahead)."""
    def run(root: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        rc, out = responses.get(tuple(args), (0, ""))
        return subprocess.CompletedProcess(("git", *args), rc, out, "")
    return run


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


# -- root & type detection ----------------------------------------------------

def test_find_root_walks_up_to_marker(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "node_modules" / "pkg").mkdir(parents=True)
    (proj / "package.json").write_text("{}")

    a = ProjectAnalyzer()
    found = a.find_root(proj / "node_modules")
    assert found == proj


def test_find_root_none_when_no_marker(tmp_path: Path) -> None:
    loose = tmp_path / "loose" / "node_modules"
    loose.mkdir(parents=True)
    assert ProjectAnalyzer().find_root(loose) is None


def test_type_detection(tmp_path: Path) -> None:
    a = ProjectAnalyzer()

    rust = tmp_path / "rust"
    rust.mkdir()
    (rust / "Cargo.toml").write_text("[package]")
    assert a.facts_for_root(rust).project_type == "rust"

    poetry = tmp_path / "poetry"
    poetry.mkdir()
    (poetry / "pyproject.toml").write_text("[tool.poetry]\nname='x'")
    assert a.facts_for_root(poetry).project_type == "python (poetry)"

    pnpm = tmp_path / "pnpm"
    pnpm.mkdir()
    (pnpm / "package.json").write_text("{}")
    (pnpm / "pnpm-lock.yaml").write_text("")
    assert a.facts_for_root(pnpm).project_type == "node (pnpm)"


# -- git state (the defensive core) -------------------------------------------

def test_git_clean(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    a = ProjectAnalyzer(run_git=_fake_git({
        STATUS: (0, ""), SYMREF: (0, "refs/heads/main"),
        UPSTREAM: (0, "origin/main"), AHEAD: (0, "0"),
    }))
    assert a._git_state(root).status is GitStatus.CLEAN


def test_git_dirty_is_wip(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    a = ProjectAnalyzer(run_git=_fake_git({STATUS: (0, " M app/main.py\n")}))
    gs = a._git_state(root)
    assert gs.status is GitStatus.DIRTY
    assert gs.is_wip


def test_git_detached(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    a = ProjectAnalyzer(run_git=_fake_git({STATUS: (0, ""), SYMREF: (1, "")}))
    assert a._git_state(root).status is GitStatus.DETACHED


def test_git_no_upstream(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    a = ProjectAnalyzer(run_git=_fake_git({
        STATUS: (0, ""), SYMREF: (0, "refs/heads/main"), UPSTREAM: (1, ""),
    }))
    gs = a._git_state(root)
    assert gs.status is GitStatus.NO_UPSTREAM
    assert gs.is_wip                      # can't prove it's pushed ⇒ protect


def test_git_unpushed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    a = ProjectAnalyzer(run_git=_fake_git({
        STATUS: (0, ""), SYMREF: (0, "refs/heads/main"),
        UPSTREAM: (0, "origin/main"), AHEAD: (0, "2"),
    }))
    assert a._git_state(root).status is GitStatus.UNPUSHED


def test_no_git_dir_is_no_git(tmp_path: Path) -> None:
    assert ProjectAnalyzer().facts_for_root(tmp_path).git.status is GitStatus.NO_GIT


def test_git_error_is_unknown_and_protected(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    def boom(root: Path, args: tuple[str, ...]):
        raise OSError("git not found")

    gs = ProjectAnalyzer(run_git=boom)._git_state(root)
    assert gs.status is GitStatus.UNKNOWN
    assert gs.is_wip                      # fail-safe: any error ⇒ protect


# -- dormancy (mtime-based, blob-skipping) ------------------------------------

def test_dormancy_from_file_mtime(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    old = 1_000_000.0
    marker = root / "package.json"
    marker.write_text("{}")
    src = root / "main.py"
    src.write_text("x")
    for f in (marker, src):                       # pin every file's mtime to `old`
        os.utime(f, (old, old))

    a = ProjectAnalyzer(now=old + 100 * 86400)
    facts = a.facts_for_root(root)
    assert facts.last_activity_days == 100
    assert facts.is_dormant


def test_dormancy_ignores_blob_mtimes(tmp_path: Path) -> None:
    # A fresh node_modules must NOT make a dormant project look active: blob subtrees
    # (and their own dir mtime) are skipped when measuring activity.
    root = tmp_path / "proj"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    old = 1_000_000.0
    marker = root / "package.json"
    marker.write_text("{}")
    src = root / "main.py"
    src.write_text("x")
    for f in (marker, src):                       # pin every source file to `old`
        os.utime(f, (old, old))
    fresh = root / "node_modules" / "pkg" / "lib.js"
    fresh.write_text("y")
    os.utime(fresh, (old + 300 * 86400, old + 300 * 86400))   # "just installed"

    facts = ProjectAnalyzer(now=old + 100 * 86400).facts_for_root(root)
    assert facts.last_activity_days == 100                    # source, not the blob
    assert facts.is_dormant
