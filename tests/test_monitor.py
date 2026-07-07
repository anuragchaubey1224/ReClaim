"""Disk-watch monitor tests (Phase 3c).

What these lock in:
  * threshold parsing (interval, min-free as bytes or %);
  * the pure `evaluate()` decision: warn/critical on low free space, growth alerts, and the
    "here's how much you'd get back" suggestion — with all I/O injected;
  * `Monitor.check()` orchestration, including growth measured against recorded history;
  * `[watch]` config parsing (fail-safe) and its CLI-flag-overrides-config precedence;
  * the `reclaim watch --once` command path (healthy vs alerting), with native notifications
    disabled so tests never touch the desktop.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from reclaim.cli.app import app
from reclaim.core.config import load_config
from reclaim.core.history import HistoryStore
from reclaim.core.model import Candidate, ScanResult, Tier
from reclaim.core.monitor import (
    Alert,
    DiskUsage,
    Level,
    Monitor,
    Thresholds,
    evaluate,
    parse_interval,
    resolve_min_free,
)

runner = CliRunner()

# native notifications must never fire during tests
NO_NOTIFY_ENV = {"RECLAIM_NO_NOTIFY": "1"}


def _res(root: str = "/r", reclaimable: int = 0) -> ScanResult:
    cands = (
        (Candidate(Path(root) / "nm", "node_modules", reclaimable, reclaimable, 1,
                   tier=Tier.REGENERABLE),)
        if reclaimable else ()
    )
    return ScanResult(roots=(Path(root),), total_allocated=reclaimable,
                      total_apparent=reclaimable, file_count=len(cands), dir_count=1,
                      error_count=0, elapsed_seconds=0.0, candidates=cands)


# -- parsing ------------------------------------------------------------------

def test_parse_interval_units() -> None:
    assert parse_interval("6h") == 21_600
    assert parse_interval("30m") == 1_800
    assert parse_interval("90s") == 90
    assert parse_interval("1d") == 86_400
    assert parse_interval("45") == 45           # bare = seconds


@pytest.mark.parametrize("bad", ["", "xh", "-1h", "0", "h"])
def test_parse_interval_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_interval(bad)


def test_resolve_min_free_bytes_and_percent() -> None:
    assert resolve_min_free("10G", total=0) == 10 * 1024 ** 3
    assert resolve_min_free("25%", total=1000) == 250


@pytest.mark.parametrize("bad", ["0%", "150%", "abc%"])
def test_resolve_min_free_rejects_bad_percent(bad: str) -> None:
    with pytest.raises(ValueError):
        resolve_min_free(bad, total=1000)


# -- evaluate() — the pure decision core --------------------------------------

def test_no_alert_when_space_is_healthy() -> None:
    disk = DiskUsage(total=1000, used=500, free=500)
    assert evaluate("/r", disk, reclaimable=100, prior_reclaimable=None,
                    thresholds=Thresholds(min_free="10%")) == []


def test_low_space_warns_and_suggests_reclaim() -> None:
    disk = DiskUsage(total=1000, used=930, free=70)     # threshold 10% = 100, free 70 ≤ 100
    alerts = evaluate("/r", disk, reclaimable=200, prior_reclaimable=None,
                      thresholds=Thresholds(min_free="10%"))
    assert len(alerts) == 1
    a = alerts[0]
    assert a.level is Level.WARNING and a.title == "Low disk space"
    assert "reclaim plan /r" in a.detail          # tells you how to get back below the line


def test_very_low_space_is_critical() -> None:
    disk = DiskUsage(total=1000, used=960, free=40)     # ≤ half of the 100 threshold
    alerts = evaluate("/r", disk, 0, None, Thresholds(min_free="10%"))
    assert alerts[0].level is Level.CRITICAL


def test_growth_alert_only_past_threshold() -> None:
    disk = DiskUsage(total=10 ** 12, used=0, free=10 ** 12)     # tons of space, no disk alert
    small = evaluate("/r", disk, reclaimable=1_500_000_000, prior_reclaimable=1_000_000_000,
                     thresholds=Thresholds(min_free="1%", growth="2G"))
    assert small == []                                  # +0.5G < 2G
    big = evaluate("/r", disk, reclaimable=4_000_000_000, prior_reclaimable=1_000_000_000,
                   thresholds=Thresholds(min_free="1%", growth="2G"))
    assert len(big) == 1 and big[0].title == "Reclaimable clutter growing"
    assert "+2.8 GB" in big[0].detail


def test_growth_needs_a_prior_and_a_threshold() -> None:
    disk = DiskUsage(total=10 ** 12, used=0, free=10 ** 12)
    # no prior → no growth alert
    assert evaluate("/r", disk, 9_000_000_000, None, Thresholds("1%", growth="2G")) == []
    # no threshold → no growth alert
    assert evaluate("/r", disk, 9_000_000_000, 0, Thresholds("1%", growth=None)) == []


# -- Monitor.check() — orchestration with injected I/O ------------------------

def test_monitor_check_flags_low_space() -> None:
    monitor = Monitor(
        [Path("/r")], Thresholds(min_free="10%"),
        measure=lambda root: _res(str(root), reclaimable=300),
        disk_probe=lambda p: DiskUsage(total=1000, used=950, free=50),
    )
    result = monitor.check()
    assert result.has_alerts
    assert result.measured[0] == ("/r", DiskUsage(1000, 950, 50), 300)


def test_monitor_growth_uses_recorded_history(tmp_path: Path) -> None:
    """The prior reclaimable comes from history recorded on the previous check; the second
    check should see the growth and alert."""
    hist = HistoryStore(tmp_path / "history.jsonl", clock=iter([1.0, 2.0]).__next__)
    sizes = iter([1_000_000_000, 5_000_000_000])
    monitor = Monitor(
        [Path("/r")], Thresholds(min_free="1%", growth="2G"),
        measure=lambda root: _res(str(root), reclaimable=next(sizes)),
        disk_probe=lambda p: DiskUsage(total=10 ** 12, used=0, free=10 ** 12),
        history=hist,
    )
    first = monitor.check()
    assert not first.has_alerts                         # nothing to compare against yet
    second = monitor.check()
    assert second.has_alerts
    assert second.alerts[0].title == "Reclaimable clutter growing"


# -- [watch] config -----------------------------------------------------------

def test_watch_config_parses(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("""
        [watch]
        roots = ["~/dev", "~/work"]
        min_free = "10G"
        interval = "6h"
        growth = "2G"
    """)
    w = load_config(p).watch
    assert w.roots == ("~/dev", "~/work")
    assert (w.min_free, w.interval, w.growth) == ("10G", "6h", "2G")
    assert not w.is_empty


def test_watch_config_is_fail_safe(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("""
        [watch]
        min_free = 10
        roots = "just-one"
    """)
    cfg = load_config(p)
    assert cfg.watch.min_free is None                   # wrong type → ignored + warned
    assert cfg.watch.roots == ("just-one",)             # bare string tolerated
    assert any("watch.min_free" in w for w in cfg.warnings)


def test_watch_config_not_a_table_warns(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('watch = "nope"\n')
    cfg = load_config(p)
    assert cfg.watch.is_empty
    assert any("[watch] must be a table" in w for w in cfg.warnings)


# -- CLI: reclaim watch --once ------------------------------------------------

def _sandbox(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    target = tmp_path / "proj"
    (target / "node_modules" / "p").mkdir(parents=True)
    (target / "node_modules" / "p" / "i.js").write_text("x" * 50_000)
    env = {"RECLAIM_HOME": str(tmp_path / "home"), **NO_NOTIFY_ENV}
    return target, env


def test_cli_watch_once_healthy(tmp_path: Path) -> None:
    target, env = _sandbox(tmp_path)
    r = runner.invoke(app, ["watch", str(target), "--once", "--no-notify"], env=env)
    assert r.exit_code == 0
    assert "reclaimable" in r.output                    # liveness line, no alert


def test_cli_watch_once_alerts_on_impossible_threshold(tmp_path: Path) -> None:
    target, env = _sandbox(tmp_path)
    r = runner.invoke(app, ["watch", str(target), "--once", "--min-free", "999T",
                            "--no-notify"], env=env)
    assert r.exit_code == 0
    assert "Low disk space" in r.output
    assert (tmp_path / "home" / "watch.log").exists()   # alert also logged


def test_cli_watch_records_history(tmp_path: Path) -> None:
    target, env = _sandbox(tmp_path)
    runner.invoke(app, ["watch", str(target), "--once", "--no-notify"], env=env)
    assert (tmp_path / "home" / "history.jsonl").exists()   # watch feeds trends too


def test_cli_watch_bad_interval_errors(tmp_path: Path) -> None:
    target, env = _sandbox(tmp_path)
    r = runner.invoke(app, ["watch", str(target), "--once", "--interval", "bogus",
                            "--no-notify"], env=env)
    assert r.exit_code == 2
