"""Planner tests — goal-driven selection, greedy by (safety, then size)."""

from __future__ import annotations

from pathlib import Path

import pytest

from reclaim.core.model import (
    Candidate,
    GitState,
    GitStatus,
    ProjectFacts,
    ScanResult,
    Tier,
)
from reclaim.core.planner import Planner, PlanGoal, parse_size


def _cand(name: str, size: int, *, tier: Tier = Tier.REGENERABLE,
          conf: float = 0.9, root: Path | None = None) -> Candidate:
    return Candidate(Path(f"/x/{name}"), name, size, size, 1, tier=tier,
                     regen_command="rebuild", confidence=conf, project_root=root)


def _result(*candidates: Candidate, projects: tuple[ProjectFacts, ...] = ()) -> ScanResult:
    return ScanResult(
        roots=(Path("/x"),), total_allocated=0, total_apparent=0, file_count=0,
        dir_count=0, error_count=0, elapsed_seconds=0.0,
        candidates=candidates, projects=projects,
    )


# -- size parsing -------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1024", 1024), ("1K", 1024), ("1k", 1024), ("1KB", 1024),
    ("20G", 20 * 1024**3), ("1.5G", int(1.5 * 1024**3)), ("500m", 500 * 1024**2),
])
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "12x", "-5G"])
def test_parse_size_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_size(bad)


# -- selection ----------------------------------------------------------------

def test_free_target_takes_largest_first_until_met() -> None:
    res = _result(_cand("nm", 500), _cand("venv", 300), _cand("cache", 50))
    pr = Planner().plan(res, PlanGoal(free_bytes=700))
    kinds = [op.kind for op in pr.plan.operations]
    assert kinds == ["nm", "venv"]              # 500 + 300 ≥ 700, cache not needed
    assert pr.plan.total_bytes == 800


def test_default_takes_all_green_high_confidence() -> None:
    res = _result(_cand("nm", 500), _cand("venv", 300))
    pr = Planner().plan(res)
    assert {op.kind for op in pr.plan.operations} == {"nm", "venv"}


def test_costly_excluded_unless_opted_in() -> None:
    res = _result(_cand("nm", 500),
                  _cand("derived", 900, tier=Tier.REGENERABLE_COSTLY))
    assert [op.kind for op in Planner().plan(res).plan.operations] == ["nm"]
    opted = Planner().plan(res, PlanGoal(include_costly=True))
    assert {op.kind for op in opted.plan.operations} == {"nm", "derived"}


def test_low_confidence_excluded_unless_opted_in() -> None:
    res = _result(_cand("nm", 500), _cand("iffy", 400, conf=0.5))
    assert [op.kind for op in Planner().plan(res).plan.operations] == ["nm"]
    opted = Planner().plan(res, PlanGoal(include_low_confidence=True))
    assert {op.kind for op in opted.plan.operations} == {"nm", "iffy"}


def test_kind_filter() -> None:
    res = _result(_cand("node_modules", 500), _cand("venv", 300))
    pr = Planner().plan(res, PlanGoal(kinds=frozenset({"node_modules"})))
    assert [op.kind for op in pr.plan.operations] == ["node_modules"]


def test_min_size_filter() -> None:
    res = _result(_cand("big", 500), _cand("tiny", 10))
    pr = Planner().plan(res, PlanGoal(min_bytes=100))
    assert [op.kind for op in pr.plan.operations] == ["big"]


def test_dormant_only() -> None:
    hot_root, cold_root = Path("/x/hot"), Path("/x/cold")
    projects = (
        ProjectFacts(hot_root, "node", GitState(GitStatus.CLEAN), last_activity_days=2),
        ProjectFacts(cold_root, "node", GitState(GitStatus.CLEAN), last_activity_days=200),
    )
    res = _result(_cand("hot_nm", 500, root=hot_root),
                  _cand("cold_nm", 300, root=cold_root),
                  projects=projects)
    pr = Planner().plan(res, PlanGoal(dormant_only=True))
    assert [op.kind for op in pr.plan.operations] == ["cold_nm"]


def test_safety_before_size_ordering() -> None:
    # A huge yellow must rank BELOW smaller greens (safety first, then size).
    res = _result(_cand("small_green", 100),
                  _cand("big_yellow", 9999, tier=Tier.REGENERABLE_COSTLY))
    pr = Planner().plan(res, PlanGoal(include_costly=True))
    assert [op.kind for op in pr.plan.operations] == ["small_green", "big_yellow"]


def test_unreachable_target_reports_risk() -> None:
    res = _result(_cand("nm", 100))
    pr = Planner().plan(res, PlanGoal(free_bytes=10_000))
    assert any("not fully reachable" in r for r in pr.plan.risks)
