"""Scan-history + trends tests (Phase 3b).

What these lock in:
  * a snapshot faithfully summarizes a scan and round-trips through JSON;
  * history is append-only, keyed by scanned root, and **fail-safe** — a corrupt line, a
    missing file, or a write error never crashes a scan;
  * a trend picks the right baseline for a look-back window and reports correct per-kind deltas;
  * duration parsing accepts d/w/m/y and rejects nonsense;
  * the CLI records on scan and renders trends/history.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from reclaim.core.history import HistoryStore, Snapshot, Trend, parse_since
from reclaim.core.model import Candidate, ScanResult, Tier
from reclaim.cli.app import app

runner = CliRunner()


def _scan(root: str = "/r", **kinds: int) -> ScanResult:
    """A ScanResult with one reclaimable candidate per kind=bytes kwarg."""
    cands = [
        Candidate(Path(root) / k, k, b, b, 1, tier=Tier.REGENERABLE)
        for k, b in kinds.items()
    ]
    total = sum(kinds.values())
    return ScanResult(roots=(Path(root),), total_allocated=total + 1000, total_apparent=total,
                      file_count=len(cands), dir_count=len(cands), error_count=0,
                      elapsed_seconds=0.1, candidates=tuple(cands))


class _Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance_days(self, d: float) -> None:
        self.t += d * 86_400


# -- parse_since --------------------------------------------------------------

def test_parse_since_units() -> None:
    assert parse_since("7d") == 7
    assert parse_since("2w") == 14
    assert parse_since("3m") == 90
    assert parse_since("1y") == 365
    assert parse_since("15") == 15          # bare number = days
    assert parse_since(" 2W ") == 14        # whitespace + case tolerant


@pytest.mark.parametrize("bad", ["", "xd", "-5d", "0", "d", "2x"])
def test_parse_since_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_since(bad)


# -- snapshot -----------------------------------------------------------------

def test_snapshot_from_scan_sums_reclaimable_per_kind() -> None:
    snap = Snapshot.from_scan(_scan(node_modules=1000, __pycache__=250), "/r", ts=5.0)
    assert snap.reclaimable_allocated == 1250
    assert snap.by_kind == {"node_modules": 1000, "__pycache__": 250}
    assert snap.root == "/r" and snap.ts == 5.0


def test_snapshot_excludes_protected_from_by_kind() -> None:
    res = ScanResult(
        roots=(Path("/r"),), total_allocated=3000, total_apparent=3000, file_count=2,
        dir_count=2, error_count=0, elapsed_seconds=0.1,
        candidates=(
            Candidate(Path("/r/nm"), "node_modules", 1000, 1000, 1, tier=Tier.REGENERABLE),
            Candidate(Path("/r/data"), "data", 2000, 2000, 1, tier=Tier.IRREPLACEABLE),
        ),
    )
    snap = Snapshot.from_scan(res, "/r", ts=1.0)
    assert snap.by_kind == {"node_modules": 1000}      # 🔴 excluded from the trend metric


def test_snapshot_json_roundtrip() -> None:
    snap = Snapshot.from_scan(_scan(node_modules=1000, target=500), "/r", ts=9.0)
    assert Snapshot.from_json(snap.to_json()) == snap


# -- store: append-only, keyed by root, fail-safe -----------------------------

def test_records_and_loads_keyed_by_root(tmp_path: Path) -> None:
    h = HistoryStore(tmp_path / "history.jsonl", clock=_Clock())
    h.record_scan(_scan("/a", node_modules=1000), "/a")
    h.record_scan(_scan("/b", target=2000), "/b")
    assert len(h.load()) == 2                          # all
    assert [s.root for s in h.load("/a")] == ["/a"]    # filtered


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert HistoryStore(tmp_path / "nope.jsonl").load() == []


def test_load_skips_corrupt_lines(tmp_path: Path) -> None:
    p = tmp_path / "history.jsonl"
    p.write_text(
        "not json\n"
        '{"ts": 1.0, "root": "/r", "total_allocated": 5, "reclaimable_allocated": 2}\n'
        "{}\n"                                          # valid json, missing required keys
        '{"ts": 2.0, "root": "/r", "total_allocated": 9, "reclaimable_allocated": 4}\n'
    )
    snaps = HistoryStore(p).load()
    assert [s.reclaimable_allocated for s in snaps] == [2, 4]   # two good, two skipped


def test_record_scan_swallows_write_errors(tmp_path: Path) -> None:
    # Point the store at a path whose parent is a *file*, so mkdir/append fail.
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    h = HistoryStore(blocker / "history.jsonl", clock=_Clock())
    assert h.record_scan(_scan(node_modules=1), "/r") is None   # returns None, does not raise


# -- trends -------------------------------------------------------------------

def test_trend_reports_per_kind_deltas(tmp_path: Path) -> None:
    clock = _Clock()
    h = HistoryStore(tmp_path / "history.jsonl", clock=clock)
    h.record_scan(_scan("/r", node_modules=1_000_000, venv=2_000_000), "/r")
    clock.advance_days(40)
    h.record_scan(_scan("/r", node_modules=5_000_000, venv=1_000_000), "/r")

    t = h.trend("/r", since_days=30)
    assert t is not None
    assert t.reclaimable_delta == 3_000_000
    assert abs(t.span_days - 40) < 1e-6
    deltas = {k.kind: k.delta for k in t.kinds}
    assert deltas == {"node_modules": 4_000_000, "venv": -1_000_000}
    assert t.kinds[0].kind == "node_modules"           # sorted by |delta| desc


def test_trend_needs_two_points(tmp_path: Path) -> None:
    h = HistoryStore(tmp_path / "history.jsonl", clock=_Clock())
    h.record_scan(_scan("/r", node_modules=1000), "/r")
    assert h.trend("/r", 30) is None


def test_trend_falls_back_to_earliest_when_window_predates_history(tmp_path: Path) -> None:
    # Two scans a day apart; a 30-day window has no snapshot that old, so baseline = earliest.
    clock = _Clock()
    h = HistoryStore(tmp_path / "history.jsonl", clock=clock)
    h.record_scan(_scan("/r", node_modules=1000), "/r")
    clock.advance_days(1)
    h.record_scan(_scan("/r", node_modules=3000), "/r")
    t = h.trend("/r", since_days=30)
    assert t is not None and t.reclaimable_delta == 2000


def test_trend_picks_baseline_at_least_window_old(tmp_path: Path) -> None:
    clock = _Clock()
    h = HistoryStore(tmp_path / "history.jsonl", clock=clock)
    h.record_scan(_scan("/r", node_modules=1000), "/r")      # day 0
    clock.advance_days(20)
    h.record_scan(_scan("/r", node_modules=2000), "/r")      # day 20
    clock.advance_days(20)
    h.record_scan(_scan("/r", node_modules=5000), "/r")      # day 40
    # 7-day window from day 40 → baseline is the most recent snapshot ≥7d old = day 20 (2000).
    t = h.trend("/r", since_days=7)
    assert t is not None and t.reclaimable_delta == 3000     # 5000 - 2000


def test_trend_only_includes_changed_kinds(tmp_path: Path) -> None:
    clock = _Clock()
    h = HistoryStore(tmp_path / "history.jsonl", clock=clock)
    h.record_scan(_scan("/r", node_modules=1000, venv=500), "/r")
    clock.advance_days(10)
    h.record_scan(_scan("/r", node_modules=4000, venv=500), "/r")   # venv unchanged
    t = h.trend("/r", 5)
    assert [k.kind for k in t.kinds] == ["node_modules"]            # venv omitted (delta 0)


def test_trend_between_is_pure() -> None:
    a = Snapshot.from_scan(_scan("/r", node_modules=1000), "/r", ts=0.0)
    b = Snapshot.from_scan(_scan("/r", node_modules=2500), "/r", ts=100.0)
    t = Trend.between("/r", a, b)
    assert t.reclaimable_delta == 1500 and t.kinds[0].delta == 1500


# -- CLI ----------------------------------------------------------------------

def test_cli_scan_records_and_trends_render(tmp_path: Path) -> None:
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    target = tmp_path / "proj"
    (target / "node_modules" / "p").mkdir(parents=True)
    (target / "node_modules" / "p" / "i.js").write_text("x" * 40_000)

    # first scan records a snapshot
    r1 = runner.invoke(app, ["scan", str(target)], env=env)
    assert r1.exit_code == 0

    # grow the tree, scan again
    (target / "node_modules" / "p" / "big.js").write_text("y" * 200_000)
    r2 = runner.invoke(app, ["scan", str(target)], env=env)
    assert r2.exit_code == 0

    h = runner.invoke(app, ["history", str(target)], env=env)
    assert h.exit_code == 0 and "reclaimable" in h.output

    t = runner.invoke(app, ["trends", str(target)], env=env)
    assert t.exit_code == 0
    assert "grew" in t.output and "node_modules" in t.output


def test_cli_trends_without_history_is_graceful(tmp_path: Path) -> None:
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    r = runner.invoke(app, ["trends", str(tmp_path / "never-scanned")], env=env)
    assert r.exit_code == 0
    assert "not enough history" in r.output


def test_cli_no_history_env_opts_out(tmp_path: Path) -> None:
    env = {"RECLAIM_HOME": str(tmp_path / "home"), "RECLAIM_NO_HISTORY": "1"}
    target = tmp_path / "proj"
    (target / ".venv" / "lib").mkdir(parents=True)
    (target / ".venv" / "lib" / "b.py").write_text("z" * 30_000)
    runner.invoke(app, ["scan", str(target)], env=env)
    assert not (tmp_path / "home" / "history.jsonl").exists()   # nothing recorded


def test_cli_trends_bad_since_errors(tmp_path: Path) -> None:
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    r = runner.invoke(app, ["trends", str(tmp_path), "--since", "bogus"], env=env)
    assert r.exit_code == 2
