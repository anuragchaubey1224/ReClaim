"""Disk watch — "warn before the wall" (L2, Phase 3c).

A lightweight monitor that periodically checks, for each watched root:

  * **free disk space** on its volume — the wall you're trying not to hit; and
  * **reclaimable-clutter growth** since the last check (reusing the Phase 3b history).

When free space drops below a threshold, or reclaimable clutter grows past one, it emits an
`Alert` — and, crucially, tells you *how much you could get back right now* and the command to
do it. That's the point: warn early, while `reclaim plan` can still save you.

Design mirrors the rest of the engine — a **pure decision core** with I/O injected:
  * `evaluate()` is a pure function (disk usage + reclaimable now + reclaimable before →
    alerts); it is the testable heart and touches nothing.
  * `Monitor.check()` orchestrates one tick: measure (scan) each root, read/record history,
    call `evaluate()`. The scanner, disk probe, and history are all injectable, so the whole
    thing runs deterministically under test with no real disk or filesystem.

The blocking loop and notifications live in the CLI (L5); this module never sleeps or prints.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

from reclaim.core.model import ScanResult
from reclaim.core.planner import parse_size
from reclaim.humanize import human_bytes, human_delta

_INTERVAL_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86_400}


def parse_interval(text: str) -> float:
    """Parse a check interval like ``6h`` / ``30m`` / ``90s`` / ``1d`` → seconds.

    A bare number is read as seconds. Raises ValueError on anything non-positive/unparseable."""
    s = text.strip().lower()
    if not s:
        raise ValueError("empty interval")
    mult = 1
    if s[-1] in _INTERVAL_UNITS:
        mult = _INTERVAL_UNITS[s[-1]]
        s = s[:-1]
    try:
        value = float(s)
    except ValueError:
        raise ValueError(f"invalid interval {text!r} (use e.g. 6h, 30m, 90s)")
    if value <= 0:
        raise ValueError("interval must be positive")
    return value * mult


def resolve_min_free(text: str, total: int) -> int:
    """Resolve a min-free spec to bytes: ``10G`` → absolute; ``10%`` → fraction of `total`."""
    s = text.strip()
    if s.endswith("%"):
        try:
            pct = float(s[:-1])
        except ValueError:
            raise ValueError(f"invalid percentage {text!r}")
        if not 0 < pct <= 100:
            raise ValueError("percentage must be in (0, 100]")
        return int(total * pct / 100)
    return parse_size(s)


class Level(str, Enum):
    """Severity of an alert, low → high."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Alert:
    level: Level
    title: str
    detail: str
    root: str = ""


@dataclass(frozen=True, slots=True)
class DiskUsage:
    total: int
    used: int
    free: int

    @property
    def free_fraction(self) -> float:
        return self.free / self.total if self.total else 0.0


DiskProbe = Callable[[Path], DiskUsage]
Measure = Callable[[Path], ScanResult]


def probe_disk(path: Path) -> DiskUsage:
    """Real disk usage of the volume containing `path` (`shutil.disk_usage`)."""
    u = shutil.disk_usage(str(path))
    return DiskUsage(total=u.total, used=u.used, free=u.free)


@dataclass(frozen=True, slots=True)
class Thresholds:
    """When to warn. `min_free` is a bytes string (``10G``) or a percentage (``10%``); a check
    at/below it warns, and below half of it is critical. `growth` (optional) warns when
    reclaimable clutter grew by at least that many bytes since the previous check."""

    min_free: str = "10%"
    growth: Optional[str] = None


def evaluate(
    root: str,
    disk: DiskUsage,
    reclaimable: int,
    prior_reclaimable: Optional[int],
    thresholds: Thresholds,
) -> list[Alert]:
    """Pure: given the current world for one root, return the alerts it warrants (possibly none)."""
    alerts: list[Alert] = []

    min_free_bytes = resolve_min_free(thresholds.min_free, disk.total)
    if disk.free <= min_free_bytes:
        critical = disk.free <= min_free_bytes // 2
        detail = (
            f"{human_bytes(disk.free)} free on the volume for {root} "
            f"({disk.free_fraction * 100:.0f}%, threshold {human_bytes(min_free_bytes)})."
        )
        if reclaimable > 0:
            detail += (
                f" You have {human_bytes(reclaimable)} of reclaimable clutter — "
                f"run `reclaim plan {root}` to free it before you hit the wall."
            )
        alerts.append(Alert(
            level=Level.CRITICAL if critical else Level.WARNING,
            title="Low disk space",
            detail=detail,
            root=root,
        ))

    if thresholds.growth and prior_reclaimable is not None:
        growth_bytes = parse_size(thresholds.growth)
        delta = reclaimable - prior_reclaimable
        if delta >= growth_bytes:
            alerts.append(Alert(
                level=Level.WARNING,
                title="Reclaimable clutter growing",
                detail=(
                    f"Reclaimable clutter under {root} grew {human_delta(delta)} since the "
                    f"last check ({human_bytes(reclaimable)} now). "
                    f"Run `reclaim status {root}` to review."
                ),
                root=root,
            ))

    return alerts


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one monitor tick across all roots."""

    alerts: tuple[Alert, ...] = ()
    measured: tuple[tuple[str, DiskUsage, int], ...] = field(default=())  # (root, disk, reclaimable)

    @property
    def has_alerts(self) -> bool:
        return bool(self.alerts)


class Monitor:
    """Runs disk/growth checks across watched roots. All I/O is injected for testability."""

    def __init__(
        self,
        roots: Sequence[Path],
        thresholds: Thresholds,
        *,
        measure: Measure,
        disk_probe: DiskProbe = probe_disk,
        history=None,
    ) -> None:
        self._roots = [Path(r) for r in roots]
        self._thresholds = thresholds
        self._measure = measure
        self._disk_probe = disk_probe
        self._history = history          # optional HistoryStore: enables growth alerts + records

    def check(self) -> CheckResult:
        """One tick: for each root, probe disk, measure reclaimable, compare to history, evaluate.

        History (if present) supplies the previous reclaimable figure for growth detection, then
        records the fresh snapshot — so `reclaim watch` also feeds `reclaim trends`."""
        alerts: list[Alert] = []
        measured: list[tuple[str, DiskUsage, int]] = []
        for root in self._roots:
            disk = self._disk_probe(root)
            res = self._measure(root)
            reclaimable = res.reclaimable_allocated

            prior: Optional[int] = None
            if self._history is not None:
                snaps = self._history.load(root)
                if snaps:
                    prior = snaps[-1].reclaimable_allocated
                self._history.record_scan(res, root)

            alerts.extend(evaluate(str(root), disk, reclaimable, prior, self._thresholds))
            measured.append((str(root), disk, reclaimable))
        return CheckResult(alerts=tuple(alerts), measured=tuple(measured))
