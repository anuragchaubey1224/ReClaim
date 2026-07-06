"""Classifier (L2) — assign every candidate a final tier + confidence + reason.

Owns invariants I1 (never reclaim without a regeneration path) and I2 (unknown ⇒
irreplaceable). Implemented as a **safety lattice** (ARCHITECTURE.md §7.2): the most
protective rule wins, evaluated top-down.

  1. PROTECT match on the path            → 🔴 IRREPLACEABLE   (top precedence)
  2. enclosing project is work-in-progress → 🔴 IRREPLACEABLE   (git-WIP hard protect)
  3. otherwise keep the unit's base tier   → 🟢 / 🟡, with a context-derived confidence

The scanner only ever emits *known reclaimable units*, so step 3's base tier is always
green/yellow; the classifier's job is to decide when project context should override that
downward toward safety, and how confident we are when it doesn't.

Confidence encodes how strongly context supports reclaiming: a dormant, clean, pushed
project scores highest; a loose directory with no enclosing project scores lowest. Below
`CONFIDENCE_THRESHOLD` a candidate is still shown but flagged for the user/AI to confirm.
"""

from __future__ import annotations

from dataclasses import replace

from reclaim.core.model import Candidate, ProjectFacts, ScanResult, Tier
from reclaim.core.project import ProjectAnalyzer
from reclaim.core.rules import protect_reason

# Below this, a green/yellow candidate is "low confidence" — surfaced but not auto-selected
# by the planner without an explicit opt-in.
CONFIDENCE_THRESHOLD = 0.7


def classify(candidate: Candidate, project: ProjectFacts | None) -> Candidate:
    """Return a copy of `candidate` with its final tier, confidence, and reason set."""
    root = project.root if project is not None else None

    # 1. Protect lattice top: a protected path is irreplaceable regardless of anything else.
    #    (Known reclaimable units won't match, but this keeps the lattice invariant honest.)
    pr = protect_reason(candidate.path.name, is_dir=True)
    if pr is not None:
        return replace(candidate, tier=Tier.IRREPLACEABLE, confidence=1.0,
                       reason=pr, project_root=root)

    # 2. Git-WIP hard protect: never reclaim from a project with unsaved/unpushed work.
    if project is not None and project.is_protected:
        return replace(
            candidate,
            tier=Tier.IRREPLACEABLE,
            confidence=0.99,
            reason=f"enclosing project is {project.git.status.value} "
                   f"({project.git.detail}) — protected",
            project_root=root,
        )

    # 3. Keep the base regen-cost tier; derive confidence from project context.
    confidence, reason = _score(candidate, project)
    return replace(candidate, tier=candidate.tier, confidence=confidence,
                   reason=reason, project_root=root)


def _score(candidate: Candidate, project: ProjectFacts | None) -> tuple[float, str]:
    """How safe is reclaiming this, given context? → (confidence 0..1, human reason)."""
    base = "regenerable via " + (candidate.regen_command or "rebuild")
    if project is None:
        return 0.75, f"{base}; no enclosing project"
    if project.git.status.name == "NO_GIT":
        return 0.80, f"{base}; not under version control"
    # git is clean & pushed here (WIP was handled in step 2).
    if project.is_dormant:
        return 0.99, (f"{base}; dormant {project.last_activity_days}d · "
                      f"git clean & pushed")
    return 0.90, f"{base}; git clean & pushed"


def classify_scan(
    result: ScanResult, analyzer: ProjectAnalyzer | None = None
) -> ScanResult:
    """Enrich a raw scan: attach project facts and final tiers to every candidate.

    This is the join point of the §5 pipeline — FsNode/Candidate meets ProjectFacts. Runs
    after the fast walk so the hot path stays free of git/classification work."""
    analyzer = analyzer or ProjectAnalyzer()

    classified: list[Candidate] = []
    roots: dict = {}   # root Path -> ProjectFacts, deduped across candidates
    for cand in result.candidates:
        facts = analyzer.facts_for(cand.path)
        if facts is not None:
            roots[facts.root] = facts
        classified.append(classify(cand, facts))

    return replace(
        result,
        candidates=tuple(classified),
        projects=tuple(roots.values()),
    )
