"""Project Analyzer (L2) — turn paths into projects with facts.

Responsibilities (ARCHITECTURE.md §6 Project Analyzer, docs/04 §5):
  * Root detection — walk up from a path to the nearest enclosing marker (`.git`,
    `package.json`, `Cargo.toml`, `pyproject.toml`, `go.mod`, …).
  * Type detection — refine by lockfiles/manifests ("node (pnpm)", "python (poetry)").
  * Defensive git-state — shell out to git; ANY uncertainty (dirty, detached, no upstream,
    unpushed, git missing/errored) is treated as work-in-progress ⇒ the project is
    protected. Only a clean-and-pushed repo is deemed safe (fail-safe, I2).
  * Activity — newest mtime under the root (skipping reclaimable blobs and `.git`) →
    dormancy score. atime is avoided (unreliable under `noatime`).

Pure L2: no UI, no deletion. `git`/`time` are injectable so the analyzer is deterministic
under test. Results are cached per root — git is called at most once per project.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from reclaim.core.model import GitState, GitStatus, ProjectFacts
from reclaim.core.rules import RECLAIMABLE_UNITS

# Marker file/dir -> project type, most specific first. `.git` is handled separately (it
# both bounds a root and triggers git-state) but also appears here as a fallback marker.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("setup.py", "python"),
    ("package.json", "node"),
    ("Gemfile", "ruby"),
    ("pom.xml", "java (maven)"),
    ("build.gradle", "java (gradle)"),
    ("build.gradle.kts", "java (gradle)"),
    ("composer.json", "php"),
    ("mix.exs", "elixir"),
    (".git", "unknown"),          # a repo with no recognized manifest
)

_ROOT_MARKER_NAMES = frozenset(name for name, _ in _MARKERS)

# Dirs never descended into when measuring activity: the reclaimable blobs (their mtimes
# reflect builds, not the developer's work) and `.git` (churns on every git op).
_ACTIVITY_SKIP = frozenset(RECLAIMABLE_UNITS) | {".git"}

# Git subprocess guardrails.
_GIT_TIMEOUT = 5.0

GitRunner = Callable[[Path, tuple[str, ...]], "subprocess.CompletedProcess[str]"]


def _run_git(root: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=root,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        check=False,
    )


class ProjectAnalyzer:
    """Detects project roots and computes their fact sheets, with per-root caching."""

    def __init__(
        self,
        *,
        now: float | None = None,
        run_git: GitRunner | None = None,
        activity_skip: frozenset[str] | None = None,
    ) -> None:
        self._now = now                       # None ⇒ live clock (deterministic in tests)
        self._run_git = run_git or _run_git
        self._facts: dict[Path, ProjectFacts] = {}
        # Dir basenames not descended into when measuring activity. Defaults to the built-in
        # reclaimable units + `.git`; the classifier passes the config-extended set so custom
        # build dirs are skipped too (their mtimes reflect builds, not the developer's work).
        self._activity_skip = activity_skip if activity_skip is not None else _ACTIVITY_SKIP

    # -- root & type -----------------------------------------------------------

    def find_root(self, start: Path) -> Path | None:
        """Nearest ancestor of `start` that looks like a project root, else None.

        `start` is typically a reclaimable unit's path, so we begin at its parent and walk
        up to the filesystem root."""
        for d in start.parents:
            for name in _ROOT_MARKER_NAMES:
                if (d / name).exists():
                    return d
        return None

    def _detect_type(self, root: Path) -> str:
        for name, base_type in _MARKERS:
            if not (root / name).exists():
                continue
            if base_type == "python":
                return self._refine_python(root)
            if base_type == "node":
                return self._refine_node(root)
            return base_type
        return "unknown"

    @staticmethod
    def _refine_python(root: Path) -> str:
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            if "[tool.poetry]" in text:
                return "python (poetry)"
            if "[tool.pdm]" in text:
                return "python (pdm)"
            if "[tool.hatch" in text:
                return "python (hatch)"
            return "python (pyproject)"
        return "python (pip)"

    @staticmethod
    def _refine_node(root: Path) -> str:
        if (root / "pnpm-lock.yaml").exists():
            return "node (pnpm)"
        if (root / "yarn.lock").exists():
            return "node (yarn)"
        if (root / "bun.lockb").exists():
            return "node (bun)"
        if (root / "package-lock.json").exists():
            return "node (npm)"
        return "node"

    # -- git state (defensive) -------------------------------------------------

    def _git_state(self, root: Path) -> GitState:
        """Reduce the repo to a single safety verdict. Any error ⇒ UNKNOWN ⇒ protected."""
        if not (root / ".git").exists():
            return GitState(GitStatus.NO_GIT, "not a git repository")
        try:
            # Dirty working tree?
            status = self._run_git(root, ("status", "--porcelain"))
            if status.returncode != 0:
                return GitState(GitStatus.UNKNOWN, "git status failed")
            if status.stdout.strip():
                n = len(status.stdout.strip().splitlines())
                return GitState(GitStatus.DIRTY, f"{n} uncommitted change(s)")

            # Detached HEAD?
            head = self._run_git(root, ("symbolic-ref", "-q", "HEAD"))
            if head.returncode != 0:
                return GitState(GitStatus.DETACHED, "detached HEAD")

            # Upstream configured?
            upstream = self._run_git(root, ("rev-parse", "--abbrev-ref", "@{u}"))
            if upstream.returncode != 0:
                return GitState(GitStatus.NO_UPSTREAM, "no upstream branch")

            # Ahead of upstream (unpushed commits)?
            ahead = self._run_git(root, ("rev-list", "--count", "@{u}..HEAD"))
            if ahead.returncode != 0:
                return GitState(GitStatus.UNKNOWN, "git rev-list failed")
            count = ahead.stdout.strip()
            if count.isdigit() and int(count) > 0:
                return GitState(GitStatus.UNPUSHED, f"{count} unpushed commit(s)")

            return GitState(GitStatus.CLEAN, "clean and pushed")
        except (OSError, subprocess.SubprocessError):
            # git binary missing, timeout, etc. — fail safe.
            return GitState(GitStatus.UNKNOWN, "git unavailable")

    # -- activity / dormancy ---------------------------------------------------

    def _newest_mtime(self, root: Path) -> float | None:
        """Newest mtime of any **file** under `root`, skipping reclaimable blobs and `.git`.

        Only file mtimes count — directory mtimes are noisy (bumped by any child add/remove)
        and a blob dir's own mtime reflects a build, not the developer's work. Cheap because
        the large blobs (node_modules, caches) are never descended into."""
        newest: float | None = None
        stack = [str(root)]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name not in self._activity_skip:
                                    stack.append(entry.path)
                                continue        # never count a dir's mtime
                            st = entry.stat(follow_symlinks=False)
                            if newest is None or st.st_mtime > newest:
                                newest = st.st_mtime
                        except OSError:
                            continue
            except OSError:
                continue
        return newest

    def _dormancy_days(self, root: Path) -> int | None:
        newest = self._newest_mtime(root)
        if newest is None:
            return None
        now = self._now if self._now is not None else time.time()
        return max(0, int((now - newest) // 86400))

    # -- public API ------------------------------------------------------------

    def facts_for_root(self, root: Path) -> ProjectFacts:
        """Compute (and cache) the fact sheet for a known project root."""
        cached = self._facts.get(root)
        if cached is not None:
            return cached
        facts = ProjectFacts(
            root=root,
            project_type=self._detect_type(root),
            git=self._git_state(root),
            last_activity_days=self._dormancy_days(root),
        )
        self._facts[root] = facts
        return facts

    def facts_for(self, path: Path) -> ProjectFacts | None:
        """Fact sheet for the project enclosing `path`, or None if it's outside any project."""
        root = self.find_root(path)
        return self.facts_for_root(root) if root is not None else None
