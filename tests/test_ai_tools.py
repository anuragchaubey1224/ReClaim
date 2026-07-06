"""Read-only fact-tool tests — pure functions over a classified scan, zero LLM/network.

These encode the grounding + fail-safe guarantees of docs/05: the agent's toolset only ever
surfaces 🟢/🟡 facts, never touches the filesystem, and can never slip a 🔴 path into a plan.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reclaim.ai.tools import (
    TOOLS,
    ToolContext,
    ToolError,
    dispatch,
    estimate_plan,
    get_project_facts,
    list_reclaimable,
)
from reclaim.core.model import (
    Candidate,
    GitState,
    GitStatus,
    ProjectFacts,
    ScanResult,
    Tier,
)


def _cand(path: str, size: int, *, kind: str | None = None, tier: Tier = Tier.REGENERABLE,
          conf: float = 0.9, root: Path | None = None) -> Candidate:
    p = Path(path)
    return Candidate(p, kind or p.name, size, size, 3, tier=tier,
                     regen_command="rebuild", confidence=conf, reason="regenerable",
                     project_root=root)


def _result(*candidates: Candidate,
            projects: tuple[ProjectFacts, ...] = ()) -> ScanResult:
    return ScanResult(
        roots=(Path("/proj"),), total_allocated=0, total_apparent=0, file_count=0,
        dir_count=0, error_count=0, elapsed_seconds=0.0,
        candidates=candidates, projects=projects,
    )


def _ctx(*candidates: Candidate, projects: tuple[ProjectFacts, ...] = ()) -> ToolContext:
    return ToolContext(_result(*candidates, projects=projects))


# -- list_reclaimable ---------------------------------------------------------

def test_list_drops_red_and_sorts_largest_first() -> None:
    ctx = _ctx(
        _cand("/proj/a/node_modules", 300),
        _cand("/proj/b/.venv", 500),
        _cand("/proj/secret", 999, tier=Tier.IRREPLACEABLE),   # 🔴 must never appear
    )
    out = list_reclaimable(ctx)
    paths = [i["path"] for i in out["items"]]
    assert paths == ["/proj/b/.venv", "/proj/a/node_modules"]   # size desc, red omitted
    assert out["total_matched"] == 2
    assert out["total_bytes"] == 800
    assert out["truncated"] is False


def test_list_kind_filter() -> None:
    ctx = _ctx(_cand("/proj/a/node_modules", 300, kind="node_modules"),
               _cand("/proj/b/.venv", 500, kind=".venv"))
    out = list_reclaimable(ctx, kind="node_modules")
    assert [i["kind"] for i in out["items"]] == ["node_modules"]


def test_list_tier_filter() -> None:
    ctx = _ctx(_cand("/proj/a/nm", 300),
               _cand("/proj/b/derived", 500, tier=Tier.REGENERABLE_COSTLY))
    green = list_reclaimable(ctx, tier="green")
    yellow = list_reclaimable(ctx, tier="yellow")
    assert [i["tier"] for i in green["items"]] == ["green"]
    assert [i["tier"] for i in yellow["items"]] == ["yellow"]


def test_list_min_bytes_filter() -> None:
    ctx = _ctx(_cand("/proj/big", 500), _cand("/proj/tiny", 10))
    out = list_reclaimable(ctx, min_bytes=100)
    assert [i["path"] for i in out["items"]] == ["/proj/big"]


def test_list_dormant_only() -> None:
    hot, cold = Path("/proj/hot"), Path("/proj/cold")
    projects = (
        ProjectFacts(hot, "node", GitState(GitStatus.CLEAN), last_activity_days=2),
        ProjectFacts(cold, "node", GitState(GitStatus.CLEAN), last_activity_days=200),
    )
    ctx = _ctx(_cand("/proj/hot/nm", 300, root=hot),
               _cand("/proj/cold/nm", 500, root=cold),
               projects=projects)
    out = list_reclaimable(ctx, dormant_only=True)
    assert [i["path"] for i in out["items"]] == ["/proj/cold/nm"]
    assert out["items"][0]["dormant"] is True


def test_list_limit_reports_truncation() -> None:
    ctx = _ctx(*[_cand(f"/proj/u{i}", 100 + i) for i in range(5)])
    out = list_reclaimable(ctx, limit=2)
    assert out["returned"] == 2
    assert out["total_matched"] == 5
    assert out["truncated"] is True


def test_list_row_flags_low_confidence() -> None:
    ctx = _ctx(_cand("/proj/iffy", 100, conf=0.5))
    row = list_reclaimable(ctx)["items"][0]
    assert row["low_confidence"] is True
    assert row["confidence"] == 0.5


def test_list_rejects_bad_tier() -> None:
    with pytest.raises(ToolError):
        list_reclaimable(_ctx(), tier="red")


# -- get_project_facts --------------------------------------------------------

def test_facts_exact_root_match() -> None:
    root = Path("/proj/web")
    facts = ProjectFacts(root, "node", GitState(GitStatus.DIRTY, "2 files"),
                         last_activity_days=1)
    out = get_project_facts(_ctx(projects=(facts,)), path="/proj/web")
    assert out["found"] is True
    assert out["git_status"] == "dirty"
    assert out["is_wip"] is True and out["is_protected"] is True


def test_facts_deepest_enclosing_project_wins() -> None:
    outer = ProjectFacts(Path("/proj"), "unknown", GitState(GitStatus.CLEAN),
                         last_activity_days=5)
    inner = ProjectFacts(Path("/proj/web"), "node", GitState(GitStatus.CLEAN),
                         last_activity_days=5)
    out = get_project_facts(_ctx(projects=(outer, inner)),
                            path="/proj/web/node_modules")
    assert out["found"] is True
    assert out["root"] == "/proj/web"          # most specific enclosing root


def test_facts_unknown_path() -> None:
    out = get_project_facts(_ctx(), path="/nowhere")
    assert out["found"] is False
    assert "list_reclaimable" in out["message"]


# -- estimate_plan ------------------------------------------------------------

def test_estimate_totals_selected_paths() -> None:
    ctx = _ctx(_cand("/proj/a/nm", 300), _cand("/proj/b/venv", 500),
               _cand("/proj/c/cache", 50))
    out = estimate_plan(ctx, paths=["/proj/a/nm", "/proj/b/venv"])
    assert out["item_count"] == 2
    assert out["total_bytes"] == 800
    assert out["by_tier"]["green"] == {"count": 2, "bytes": 800}
    assert out["not_found"] == [] and out["excluded"] == []


def test_estimate_excludes_protected_path() -> None:
    ctx = _ctx(_cand("/proj/nm", 300),
               _cand("/proj/secret", 999, tier=Tier.IRREPLACEABLE))
    out = estimate_plan(ctx, paths=["/proj/nm", "/proj/secret"])
    assert out["total_bytes"] == 300               # 🔴 never counted
    assert [e["path"] for e in out["excluded"]] == ["/proj/secret"]


def test_estimate_reports_unknown_path() -> None:
    ctx = _ctx(_cand("/proj/nm", 300))
    out = estimate_plan(ctx, paths=["/proj/nm", "/proj/ghost"])
    assert out["not_found"] == ["/proj/ghost"]
    assert out["item_count"] == 1


def test_estimate_dedupes_repeated_paths() -> None:
    ctx = _ctx(_cand("/proj/nm", 300))
    out = estimate_plan(ctx, paths=["/proj/nm", "/proj/nm"])
    assert out["item_count"] == 1
    assert out["total_bytes"] == 300


def test_estimate_costly_surfaces_risk() -> None:
    ctx = _ctx(_cand("/proj/derived", 900, tier=Tier.REGENERABLE_COSTLY))
    out = estimate_plan(ctx, paths=["/proj/derived"])
    assert out["by_tier"]["yellow"]["count"] == 1
    assert any("costly" in r for r in out["risks"])


# -- dispatch -----------------------------------------------------------------

def test_dispatch_runs_named_tool() -> None:
    ctx = _ctx(_cand("/proj/nm", 300))
    out = dispatch("list_reclaimable", {}, ctx)
    assert out["total_matched"] == 1


def test_dispatch_unknown_tool_raises() -> None:
    with pytest.raises(ToolError):
        dispatch("delete_everything", {}, _ctx())


def test_dispatch_bad_arguments_raises() -> None:
    with pytest.raises(ToolError):
        dispatch("get_project_facts", {"wrong_kwarg": 1}, _ctx())


def test_registry_exposes_only_readonly_tools() -> None:
    names = {t.name for t in TOOLS}
    assert names == {"list_reclaimable", "get_project_facts", "estimate_plan"}
    # No destructive capability is advertised to the model (docs/05 §Guardrails).
    assert not any(bad in names for bad in ("delete", "apply", "run_shell", "propose_plan"))
