"""Core data model — the immutable objects that flow through the engine.

This is the **stable contract** between subsystems (ARCHITECTURE.md §5): the scanner emits
raw `Candidate`s, the project analyzer produces `ProjectFacts`, and the classifier refines
each candidate's `tier` / `confidence` / `reason` using that project context. Everything is
frozen — stages produce *new* objects (via `dataclasses.replace`) rather than mutating.

Phase 1a scope: the scan/classify half of the pipeline. The `Plan` / `Operation` half
(quarantine + journal, ARCHITECTURE.md §5 lower rows) arrives in Phase 1b.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Days since the newest file mtime in a project past which it is considered DORMANT.
# mtime (not atime — unreliable under `noatime`, ARCHITECTURE.md §6 Project Analyzer).
DORMANT_AFTER_DAYS = 30


class Tier(str, Enum):
    """Safety classification of a reclaimable candidate (ARCHITECTURE.md §6.3 / §7.2)."""

    REGENERABLE = "green"          # safe: a cheap regeneration command exists
    REGENERABLE_COSTLY = "yellow"  # rebuildable but slow/expensive to re-acquire
    IRREPLACEABLE = "red"          # never touch (default for anything unknown, I2)


class GitStatus(str, Enum):
    """Result of inspecting a project's git repository (ARCHITECTURE.md §6 Project Analyzer,
    docs/04 §5). Anything other than CLEAN is treated defensively as work-in-progress."""

    CLEAN = "clean"              # committed AND pushed — the only safe-to-reclaim state
    DIRTY = "dirty"             # uncommitted changes in the working tree
    UNPUSHED = "unpushed"       # committed but ahead of upstream (would lose local commits)
    DETACHED = "detached"       # detached HEAD — ambiguous, protect
    NO_UPSTREAM = "no_upstream"  # no tracking branch — can't prove it's pushed
    NO_GIT = "no_git"           # not a git repo at all
    UNKNOWN = "unknown"         # git missing / errored — fail safe, protect


# Which git states count as work-in-progress → hard-protect the project's contents.
# CLEAN is the ONLY non-WIP state. NO_GIT is not WIP by itself (a non-repo dir can still
# hold regenerable artifacts), but its candidates get no git-based safety boost.
_WIP_STATUSES = frozenset(
    {GitStatus.DIRTY, GitStatus.UNPUSHED, GitStatus.DETACHED,
     GitStatus.NO_UPSTREAM, GitStatus.UNKNOWN}
)


@dataclass(frozen=True, slots=True)
class GitState:
    """A project's git posture, reduced to a single safety verdict."""

    status: GitStatus
    detail: str = ""            # human-readable, e.g. "3 files modified, 1 commit ahead"

    @property
    def is_wip(self) -> bool:
        """True ⇒ the project has (or may have) unsaved work ⇒ protect it (defensive)."""
        return self.status in _WIP_STATUSES

    @property
    def is_clean(self) -> bool:
        return self.status is GitStatus.CLEAN


@dataclass(frozen=True, slots=True)
class ProjectFacts:
    """A detected project root turned into facts (ARCHITECTURE.md §5, docs/03 §2).

    Activity + git-state turn a blunt rule ("reclaim .venv") into a judgement ("this
    dormant, clean, pushed project's .venv is very safe to reclaim")."""

    root: Path
    project_type: str                 # "node", "python (poetry)", "rust", "unknown", …
    git: GitState
    last_activity_days: int | None    # days since newest mtime under the root; None if unknown

    @property
    def is_dormant(self) -> bool:
        d = self.last_activity_days
        return d is not None and d >= DORMANT_AFTER_DAYS

    @property
    def is_protected(self) -> bool:
        """The whole project is off-limits — currently == git says work-in-progress."""
        return self.git.is_wip


@dataclass(frozen=True, slots=True)
class Candidate:
    """One reclaimable unit found on disk, e.g. a single `node_modules` directory.

    The scanner emits it with a provisional `tier`; the classifier replaces that with the
    final tier + `confidence` + `reason` given the enclosing project's facts."""

    path: Path
    kind: str                       # "node_modules", ".venv", "target (rust)", …
    size_allocated: int             # bytes actually occupied on disk (what we'd free)
    size_apparent: int              # logical bytes (sum of st_size)
    file_count: int
    tier: Tier = Tier.REGENERABLE
    regen_command: str | None = None
    confidence: float = 1.0         # 0..1; below the classifier threshold ⇒ escalate/keep
    reason: str = ""                # plain-English "why this tier", for the report + AI
    project_root: Path | None = None  # the project this unit belongs to, if any

    @property
    def is_reclaimable(self) -> bool:
        """Green or yellow — something the engine is willing to move (never red)."""
        return self.tier in (Tier.REGENERABLE, Tier.REGENERABLE_COSTLY)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Aggregate result of a scan. Deliberately holds aggregates + reclaimable units,
    NOT a record per file — that flat-memory property is part of the performance design.

    After the classify pass, `candidates` carry final tiers and `projects` is populated."""

    roots: tuple[Path, ...]
    total_allocated: int
    total_apparent: int
    file_count: int
    dir_count: int
    error_count: int
    elapsed_seconds: float
    candidates: tuple[Candidate, ...]
    projects: tuple[ProjectFacts, ...] = field(default_factory=tuple)

    @property
    def reclaimable_allocated(self) -> int:
        """Bytes in candidates the engine would actually move (green + yellow)."""
        return sum(c.size_allocated for c in self.candidates if c.is_reclaimable)

    def of_tier(self, tier: Tier) -> list[Candidate]:
        return [c for c in self.candidates if c.tier is tier]

    def top(self, n: int = 20, *, reclaimable_only: bool = False) -> list[Candidate]:
        """The n largest units by allocated size (optionally only green/yellow ones)."""
        pool = [c for c in self.candidates if c.is_reclaimable] if reclaimable_only \
            else list(self.candidates)
        return sorted(pool, key=lambda c: c.size_allocated, reverse=True)[:n]

    def by_kind(self) -> dict[str, tuple[int, int]]:
        """Aggregate reclaimable space per kind → {kind: (total_allocated, count)}."""
        agg: dict[str, list[int]] = {}
        for c in self.candidates:
            slot = agg.setdefault(c.kind, [0, 0])
            slot[0] += c.size_allocated
            slot[1] += 1
        return {k: (v[0], v[1]) for k, v in agg.items()}

    def by_tier(self) -> dict[Tier, tuple[int, int]]:
        """Aggregate space per tier → {tier: (total_allocated, count)}."""
        agg: dict[Tier, list[int]] = {t: [0, 0] for t in Tier}
        for c in self.candidates:
            slot = agg[c.tier]
            slot[0] += c.size_allocated
            slot[1] += 1
        return {t: (v[0], v[1]) for t, v in agg.items()}
