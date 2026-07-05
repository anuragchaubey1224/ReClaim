"""Core data model — the immutable objects that flow through the engine.

Phase 0 scope: only the types the scanner needs. The full ProjectFacts / Plan / Operation
model (ARCHITECTURE.md §5) arrives in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Tier(str, Enum):
    """Safety classification of a reclaimable candidate (ARCHITECTURE.md §6.3)."""

    REGENERABLE = "green"          # safe: a cheap regeneration command exists
    REGENERABLE_COSTLY = "yellow"  # rebuildable but slow/expensive to re-acquire
    IRREPLACEABLE = "red"          # never touch (default for anything unknown)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One reclaimable unit found on disk, e.g. a single `node_modules` directory."""

    path: Path
    kind: str                       # "node_modules", ".venv", "target (rust)", …
    size_allocated: int             # bytes actually occupied on disk (what we'd free)
    size_apparent: int              # logical bytes (sum of st_size)
    file_count: int
    tier: Tier = Tier.REGENERABLE
    regen_command: str | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Aggregate result of a scan. Deliberately holds aggregates + reclaimable units,
    NOT a record per file — that flat-memory property is part of the performance design."""

    roots: tuple[Path, ...]
    total_allocated: int
    total_apparent: int
    file_count: int
    dir_count: int
    error_count: int
    elapsed_seconds: float
    candidates: tuple[Candidate, ...]

    @property
    def reclaimable_allocated(self) -> int:
        return sum(c.size_allocated for c in self.candidates)

    def top(self, n: int = 20) -> list[Candidate]:
        """The n largest reclaimable units by allocated size."""
        return sorted(self.candidates, key=lambda c: c.size_allocated, reverse=True)[:n]

    def by_kind(self) -> dict[str, tuple[int, int]]:
        """Aggregate reclaimable space per kind → {kind: (total_allocated, count)}."""
        agg: dict[str, list[int]] = {}
        for c in self.candidates:
            slot = agg.setdefault(c.kind, [0, 0])
            slot[0] += c.size_allocated
            slot[1] += 1
        return {k: (v[0], v[1]) for k, v in agg.items()}
