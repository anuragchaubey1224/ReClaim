"""Classifier tests — the safety lattice and the scan→classify join.

These encode the load-bearing safety invariants for Phase 1a:
  * a project with uncommitted/unpushed work ⇒ its reclaimable units are PROTECTED (🔴),
  * protected paths (secrets/data) win over everything,
  * clean/dormant context raises confidence but never lowers safety,
  * unknown directories never become candidates (only known reclaimable units do).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from reclaim.core.classifier import classify, classify_scan
from reclaim.core.model import (
    Candidate,
    GitState,
    GitStatus,
    ProjectFacts,
    Tier,
)
from reclaim.core.project import ProjectAnalyzer
from reclaim.core.rules import is_reclaimable_unit
from reclaim.core.scanner import Scanner


def _candidate(name: str = "node_modules", tier: Tier = Tier.REGENERABLE,
               root: str = "/x/proj") -> Candidate:
    return Candidate(
        path=Path(root) / name,
        kind=name,
        size_allocated=1000,
        size_apparent=1000,
        file_count=3,
        tier=tier,
        regen_command="npm install",
    )


def _facts(status: GitStatus, *, dormant_days: int | None = 1,
           root: str = "/x/proj") -> ProjectFacts:
    return ProjectFacts(
        root=Path(root),
        project_type="node",
        git=GitState(status, status.value),
        last_activity_days=dormant_days,
    )


# -- rules --------------------------------------------------------------------

def test_rules_base_tiers() -> None:
    assert is_reclaimable_unit("node_modules").tier is Tier.REGENERABLE
    assert is_reclaimable_unit("DerivedData").tier is Tier.REGENERABLE_COSTLY  # costly 🟡
    assert is_reclaimable_unit("totally-unknown-dir") is None                  # I2
    assert is_reclaimable_unit("dist") is None                                 # gated off
    assert is_reclaimable_unit("dist", include_context_sensitive=True) is not None


# -- the lattice: git-WIP hard protect (the headline safety invariant) --------

def test_dirty_project_protects_its_units() -> None:
    c = classify(_candidate(), _facts(GitStatus.DIRTY))
    assert c.tier is Tier.IRREPLACEABLE
    assert not c.is_reclaimable


def test_no_upstream_project_is_protected() -> None:
    # Defensive: a repo we can't prove is pushed must not be reclaimed from.
    c = classify(_candidate(), _facts(GitStatus.NO_UPSTREAM))
    assert c.tier is Tier.IRREPLACEABLE


def test_unknown_git_project_is_protected() -> None:
    c = classify(_candidate(), _facts(GitStatus.UNKNOWN))
    assert c.tier is Tier.IRREPLACEABLE


# -- the lattice: safe contexts keep the base tier, vary confidence -----------

def test_clean_pushed_project_is_green() -> None:
    c = classify(_candidate(), _facts(GitStatus.CLEAN, dormant_days=1))
    assert c.tier is Tier.REGENERABLE
    assert c.confidence == 0.90


def test_dormant_clean_project_is_highest_confidence() -> None:
    c = classify(_candidate(), _facts(GitStatus.CLEAN, dormant_days=200))
    assert c.tier is Tier.REGENERABLE
    assert c.confidence == 0.99


def test_no_project_context_is_lower_confidence() -> None:
    c = classify(_candidate(), None)
    assert c.tier is Tier.REGENERABLE
    assert c.confidence == 0.75


def test_non_git_dir_keeps_tier() -> None:
    c = classify(_candidate(), _facts(GitStatus.NO_GIT))
    assert c.tier is Tier.REGENERABLE
    assert c.confidence == 0.80


def test_costly_tier_is_preserved_when_safe() -> None:
    c = classify(_candidate("DerivedData", tier=Tier.REGENERABLE_COSTLY),
                 _facts(GitStatus.CLEAN))
    assert c.tier is Tier.REGENERABLE_COSTLY          # stays 🟡, not downgraded


# -- the lattice: protect paths win over everything ---------------------------

def test_protected_path_is_irreplaceable() -> None:
    # Even in a clean project, a protected directory name is never reclaimable.
    c = classify(_candidate("data"), _facts(GitStatus.CLEAN))
    assert c.tier is Tier.IRREPLACEABLE


# -- scan → classify orchestration --------------------------------------------

def test_classify_scan_attaches_tiers_and_projects(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "package.json").write_text("{}")
    (proj / ".git").mkdir()
    (proj / "src" / "app.js").write_text("x" * 100)
    (proj / "node_modules" / "pkg").mkdir(parents=True)
    (proj / "node_modules" / "pkg" / "i.js").write_text("y" * 5000)

    STATUS = ("status", "--porcelain")

    def clean_git(root: Path, args: tuple[str, ...]):
        return subprocess.CompletedProcess(("git", *args), 0, "", "")

    raw = Scanner(workers=2).scan(tmp_path)
    res = classify_scan(raw, ProjectAnalyzer(run_git=clean_git))

    # Only the known reclaimable unit is a candidate; source dirs never are (I2).
    kinds = {c.kind for c in res.candidates}
    assert kinds == {"node_modules"}

    nm = next(c for c in res.candidates if c.kind == "node_modules")
    assert nm.tier is Tier.REGENERABLE
    assert nm.project_root == proj

    assert len(res.projects) == 1
    assert res.projects[0].root == proj


def test_classify_scan_protects_dirty_project(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "node_modules" / "pkg").mkdir(parents=True)
    (proj / "package.json").write_text("{}")
    (proj / ".git").mkdir()
    (proj / "node_modules" / "pkg" / "i.js").write_text("y" * 5000)

    def dirty_git(root: Path, args: tuple[str, ...]):
        out = " M index.js\n" if tuple(args) == ("status", "--porcelain") else ""
        return subprocess.CompletedProcess(("git", *args), 0, out, "")

    raw = Scanner(workers=2).scan(tmp_path)
    res = classify_scan(raw, ProjectAnalyzer(run_git=dirty_git))

    nm = next(c for c in res.candidates if c.kind == "node_modules")
    assert nm.tier is Tier.IRREPLACEABLE          # dirty repo ⇒ protected
    assert res.reclaimable_allocated == 0         # nothing is reclaimable here
