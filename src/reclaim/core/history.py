"""Scan history + trends (L2, Phase 3b) — "what grew since last month?".

Each read-only scan appends a compact **snapshot** to an append-only JSONL log at
`$RECLAIM_HOME/history.jsonl`. Comparing the latest snapshot against one from N days ago yields
a **trend**: the per-kind change in reclaimable clutter ("node_modules +3.2 GB since 30d ago").

Design choices:
  * **Append-only JSONL**, one snapshot per line — cheap to write, human-readable, no DB, and a
    corrupt line never poisons the rest (bad lines are skipped on load).
  * **Keyed by scanned root** — trends only compare like with like, so a snapshot records the
    resolved path it scanned and `load(root)` filters to it.
  * **Injectable clock** — timestamps are deterministic under test.
  * **Never breaks a scan** — recording swallows I/O errors; a full disk or a read-only home
    degrades to "no history", never a failed scan (the engine's job is reclaiming, not logging).

This module is pure L2: no UI. The CLI renders `Trend`; the engine just records and computes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reclaim.core.model import ScanResult

_SECONDS_PER_DAY = 86_400

# Duration suffixes accepted by `parse_since` → multiplier in days.
_SINCE_UNITS: dict[str, int] = {"d": 1, "w": 7, "m": 30, "y": 365}


def parse_since(text: str) -> float:
    """Parse a look-back window like ``7d`` / ``2w`` / ``3m`` / ``1y`` (or bare days) → days.

    A bare number is read as days. Raises ValueError on anything unparseable or non-positive."""
    s = text.strip().lower()
    if not s:
        raise ValueError("empty duration")
    mult = 1
    if s[-1] in _SINCE_UNITS:
        mult = _SINCE_UNITS[s[-1]]
        s = s[:-1]
    try:
        value = float(s)
    except ValueError:
        raise ValueError(f"invalid duration {text!r} (use e.g. 7d, 2w, 3m, or a number of days)")
    if value <= 0:
        raise ValueError("duration must be positive")
    return value * mult


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A point-in-time summary of one scan — the unit of history.

    Deliberately tiny: totals plus per-kind reclaimable bytes, never a per-file record."""

    ts: float                       # unix time of the scan
    root: str                       # resolved path scanned (so trends compare like with like)
    total_allocated: int
    total_apparent: int
    reclaimable_allocated: int
    file_count: int
    dir_count: int
    by_kind: dict[str, int]         # kind -> reclaimable allocated bytes

    @classmethod
    def from_scan(cls, res: ScanResult, root: Path | str, ts: float) -> "Snapshot":
        by_kind: dict[str, int] = {}
        for c in res.candidates:
            if c.is_reclaimable:                # only the green/yellow bytes we'd actually free
                by_kind[c.kind] = by_kind.get(c.kind, 0) + c.size_allocated
        return cls(
            ts=float(ts),
            root=str(root),
            total_allocated=res.total_allocated,
            total_apparent=res.total_apparent,
            reclaimable_allocated=res.reclaimable_allocated,
            file_count=res.file_count,
            dir_count=res.dir_count,
            by_kind=by_kind,
        )

    def to_json(self) -> dict:
        return {
            "ts": self.ts,
            "root": self.root,
            "total_allocated": self.total_allocated,
            "total_apparent": self.total_apparent,
            "reclaimable_allocated": self.reclaimable_allocated,
            "file_count": self.file_count,
            "dir_count": self.dir_count,
            "by_kind": self.by_kind,
        }

    @classmethod
    def from_json(cls, d: object) -> "Snapshot | None":
        """Parse one record, or None if it's malformed (fail-safe: a bad line is skipped)."""
        if not isinstance(d, dict):
            return None
        try:
            raw_kinds = d.get("by_kind") or {}
            by_kind = {str(k): int(v) for k, v in raw_kinds.items()}
            return cls(
                ts=float(d["ts"]),
                root=str(d["root"]),
                total_allocated=int(d["total_allocated"]),
                total_apparent=int(d.get("total_apparent", 0)),
                reclaimable_allocated=int(d["reclaimable_allocated"]),
                file_count=int(d.get("file_count", 0)),
                dir_count=int(d.get("dir_count", 0)),
                by_kind=by_kind,
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return None


@dataclass(frozen=True, slots=True)
class KindDelta:
    """The change in reclaimable bytes for one kind between two snapshots."""

    kind: str
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True, slots=True)
class Trend:
    """A baseline→latest comparison for one root: overall + per-kind deltas."""

    root: str
    baseline: Snapshot
    latest: Snapshot
    kinds: tuple[KindDelta, ...]        # changed kinds only, sorted by |delta| desc

    @property
    def span_seconds(self) -> float:
        return self.latest.ts - self.baseline.ts

    @property
    def span_days(self) -> float:
        return self.span_seconds / _SECONDS_PER_DAY

    @property
    def reclaimable_delta(self) -> int:
        return self.latest.reclaimable_allocated - self.baseline.reclaimable_allocated

    @property
    def total_delta(self) -> int:
        return self.latest.total_allocated - self.baseline.total_allocated

    @classmethod
    def between(cls, root: str, baseline: Snapshot, latest: Snapshot) -> "Trend":
        deltas = [
            KindDelta(k, baseline.by_kind.get(k, 0), latest.by_kind.get(k, 0))
            for k in set(baseline.by_kind) | set(latest.by_kind)
        ]
        deltas = [d for d in deltas if d.delta != 0]
        deltas.sort(key=lambda d: abs(d.delta), reverse=True)
        return cls(root=root, baseline=baseline, latest=latest, kinds=tuple(deltas))


class HistoryStore:
    """Append/load scan snapshots and compute trends. `clock` is injectable for tests."""

    def __init__(self, path: Path, *, clock: Callable[[], float] | None = None) -> None:
        self._path = Path(path)
        self._clock = clock or time.time

    def record(self, snapshot: Snapshot) -> None:
        """Append one snapshot as a JSON line (creates the store dir if needed)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot.to_json()) + "\n")

    def record_scan(self, res: ScanResult, root: Path | str) -> Snapshot | None:
        """Snapshot a scan and append it. Returns the snapshot, or None if writing failed.

        Recording must never break a scan, so any I/O error is swallowed (best-effort log)."""
        try:
            snap = Snapshot.from_scan(res, root, self._clock())
            self.record(snap)
            return snap
        except OSError:
            return None

    def load(self, root: Path | str | None = None) -> list[Snapshot]:
        """All snapshots (optionally for one root), oldest first. Bad lines are skipped."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return []
        target = str(root) if root is not None else None
        snaps: list[Snapshot] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            snap = Snapshot.from_json(record)
            if snap is None:
                continue
            if target is None or snap.root == target:
                snaps.append(snap)
        snaps.sort(key=lambda s: s.ts)
        return snaps

    def trend(self, root: Path | str, since_days: float) -> Trend | None:
        """Compare the latest snapshot for `root` with one ~`since_days` old.

        The baseline is the most recent snapshot at least `since_days` old; if none is that
        old, the earliest available snapshot is used (so a short history still shows *some*
        trend, over whatever span exists). Returns None if there aren't two distinct points."""
        snaps = self.load(root)
        if len(snaps) < 2:
            return None
        latest = snaps[-1]
        cutoff = latest.ts - since_days * _SECONDS_PER_DAY
        older = [s for s in snaps if s.ts <= cutoff]
        baseline = older[-1] if older else snaps[0]
        if baseline.ts >= latest.ts:            # no usable span (all share the latest ts)
            return None
        return Trend.between(str(root), baseline, latest)
