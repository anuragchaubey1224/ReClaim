"""Dashboard tests (Phase 3d).

The dashboard is a *composition* layer, so the tests render `build_dashboard()` to a string
and assert the right facts surface — no fragile pixel/layout assertions, just "does the number
/ section / status appear". Plus a CLI test for the one-shot `reclaim dashboard` path.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from reclaim.cli.app import app
from reclaim.cli.dashboard import build_dashboard
from reclaim.core.history import Snapshot, Trend
from reclaim.core.model import (
    Candidate,
    GitState,
    GitStatus,
    ProjectFacts,
    ScanResult,
    Tier,
)
from reclaim.core.monitor import DiskUsage

runner = CliRunner()


def _render(renderable, width: int = 100) -> str:
    buf = StringIO()
    Console(file=buf, width=width).print(renderable)
    return buf.getvalue()


def _scan() -> ScanResult:
    root = Path("/proj")
    cands = (
        Candidate(root / "web/node_modules", "node_modules", 4_400_000, 4_400_000, 10,
                  tier=Tier.REGENERABLE, project_root=root / "web"),
        Candidate(root / "api/.venv", ".venv", 3_100_000, 3_100_000, 5,
                  tier=Tier.REGENERABLE_COSTLY, project_root=root / "api"),
    )
    projs = (
        ProjectFacts(root / "web", "node", GitState(GitStatus.CLEAN, "clean"), 40),
        ProjectFacts(root / "api", "python", GitState(GitStatus.DIRTY, "2 changes"), 1),
    )
    return ScanResult(roots=(root,), total_allocated=20_000_000, total_apparent=20_000_000,
                      file_count=1234, dir_count=200, error_count=0, elapsed_seconds=0.42,
                      candidates=cands, projects=projs)


def _disk(free: int = 40_000_000_000, total: int = 500_000_000_000) -> DiskUsage:
    return DiskUsage(total=total, used=total - free, free=free)


# -- build_dashboard renders the expected sections ----------------------------

def test_dashboard_has_all_sections() -> None:
    out = _render(build_dashboard(Path("/proj"), _scan(), _disk(), None))
    for section in ("Reclaim", "Summary", "Disk", "Top units", "Projects", "Trend"):
        assert section in out


def test_dashboard_shows_tiers_and_units() -> None:
    out = _render(build_dashboard(Path("/proj"), _scan(), _disk(), None))
    assert "Regenerable" in out and "Protected" in out
    assert "node_modules" in out and ".venv" in out
    assert "7.2 MB" in out                     # 4.4 + 3.1 reclaimable, humanized


def test_dashboard_shows_project_git_state() -> None:
    out = _render(build_dashboard(Path("/proj"), _scan(), _disk(), None))
    assert "clean" in out and "dirty" in out


def test_dashboard_disk_low_vs_ok() -> None:
    ok = _render(build_dashboard(Path("/proj"), _scan(), _disk(free=400_000_000_000), None,
                                 min_free="10%"))
    assert "above your" in ok
    low = _render(build_dashboard(Path("/proj"), _scan(), _disk(free=1_000_000_000), None,
                                  min_free="10%"))
    assert "below your" in low                 # free 1G < 10% of 500G = 50G


def test_dashboard_trend_none_is_graceful() -> None:
    out = _render(build_dashboard(Path("/proj"), _scan(), _disk(), None))
    assert "not enough history" in out


def test_dashboard_trend_shows_deltas() -> None:
    a = Snapshot.from_scan(
        ScanResult((Path("/proj"),), 0, 0, 0, 0, 0, 0.0,
                   (Candidate(Path("/proj/nm"), "node_modules", 1_000_000, 1_000_000, 1,
                              tier=Tier.REGENERABLE),)),
        "/proj", ts=0.0)
    b = Snapshot.from_scan(_scan(), "/proj", ts=100.0)
    out = _render(build_dashboard(Path("/proj"), _scan(), _disk(), Trend.between("/proj", a, b)))
    assert "grew" in out and "node_modules" in out


def test_dashboard_handles_empty_scan() -> None:
    empty = ScanResult(roots=(Path("/x"),), total_allocated=0, total_apparent=0,
                       file_count=0, dir_count=0, error_count=0, elapsed_seconds=0.0,
                       candidates=())
    out = _render(build_dashboard(Path("/x"), empty, _disk(), None))
    assert "nothing reclaimable" in out and "no projects detected" in out


def test_dashboard_bad_min_free_falls_back() -> None:
    # An unparseable threshold must not crash the pure builder (falls back to 10%).
    out = _render(build_dashboard(Path("/proj"), _scan(), _disk(), None, min_free="not-a-size"))
    assert "Disk" in out


# -- CLI: reclaim dashboard (one-shot) ----------------------------------------

def test_cli_dashboard_oneshot(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    (target / "node_modules" / "p").mkdir(parents=True)
    (target / "node_modules" / "p" / "i.js").write_text("x" * 60_000)
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    r = runner.invoke(app, ["dashboard", str(target)], env=env)
    assert r.exit_code == 0
    assert "Summary" in r.output and "node_modules" in r.output


def test_cli_dashboard_bad_refresh_errors(tmp_path: Path) -> None:
    env = {"RECLAIM_HOME": str(tmp_path / "home")}
    r = runner.invoke(app, ["dashboard", str(tmp_path), "--refresh", "bogus"], env=env)
    assert r.exit_code == 2
