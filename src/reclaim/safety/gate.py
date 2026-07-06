"""Safety Gate (L3) — the single choke point every mutation passes through (owns I5, I6).

Both the CLI and the AI produce a `Plan`; both funnel through `SafetyGate.validate` before
anything moves. The gate re-validates each operation **at apply time, not just plan time**
(TOCTOU defense, I6/AD6): state drifts between planning and applying — a repo can go dirty,
a directory can be deleted or replaced. So the gate re-reads the world and re-runs the exact
same deterministic classifier the plan was built from; a hallucinating AI or a stale plan
cannot widen its privileges past this point (I5).

Checks per operation, fail-safe (any doubt ⇒ reject):
  1. the source still exists;
  2. its basename isn't a protected path;
  3. it's still a recognized reclaimable unit;
  4. re-classifying it with a FRESH git/activity read still yields 🟢/🟡 (not 🔴).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reclaim.core.classifier import classify
from reclaim.core.model import Candidate, Operation, Plan
from reclaim.core.preferences import PreferenceStore
from reclaim.core.project import ProjectAnalyzer
from reclaim.core.rules import is_reclaimable_unit, protect_reason


@dataclass(frozen=True, slots=True)
class Rejection:
    operation: Operation
    reason: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """The gate's verdict: a plan containing only the approved ops, plus the rejections."""

    approved: Plan
    rejected: tuple[Rejection, ...]

    @property
    def has_rejections(self) -> bool:
        return bool(self.rejected)

    @property
    def all_approved(self) -> bool:
        return not self.rejected


class SafetyGate:
    def __init__(
        self,
        analyzer_factory: Callable[[], ProjectAnalyzer] | None = None,
        preferences: PreferenceStore | None = None,
    ) -> None:
        # A FRESH analyzer per validate() → git/activity are re-read, never served from a
        # plan-time cache. Injectable so tests can drive the git state deterministically.
        self._analyzer_factory = analyzer_factory or ProjectAnalyzer
        # User protection rules, re-read at apply time — a rule added after planning still
        # blocks removal here (defense in depth with the classifier's scan-time check).
        self._preferences = preferences

    def validate(self, plan: Plan) -> GateResult:
        analyzer = self._analyzer_factory()
        approved: list[Operation] = []
        rejected: list[Rejection] = []
        for op in plan.operations:
            reason = self._check(op, analyzer)
            if reason is None:
                approved.append(op)
            else:
                rejected.append(Rejection(op, reason))
        return GateResult(Plan(tuple(approved), plan.risks), tuple(rejected))

    def _check(self, op: Operation, analyzer: ProjectAnalyzer) -> str | None:
        src = op.source

        # 1. Existence — the item may have been removed/rebuilt since planning.
        if not src.exists():
            return "source no longer exists"

        # 1b. User preference — an explicit "never touch" rule blocks removal (re-read fresh).
        if self._preferences is not None:
            pref = self._preferences.matches(src)
            if pref is not None:
                return f"user preference: never touch {pref.pattern}"

        # 2. Protected path — never reclaim a secret/data path (defensive top of lattice).
        pr = protect_reason(src.name, is_dir=True)
        if pr is not None:
            return pr

        # 3. Still a recognized reclaimable unit — guards against a malformed/tampered plan.
        if is_reclaimable_unit(src.name, include_context_sensitive=True) is None:
            return f"'{src.name}' is not a known reclaimable unit"

        # 4. Re-classify with a fresh read: if the enclosing project went dirty/unpushed (or
        #    anything else pushed it to 🔴) since planning, reject it now.
        facts = analyzer.facts_for(src)
        cand = Candidate(
            path=src, kind=op.kind, size_allocated=op.size_allocated,
            size_apparent=0, file_count=op.file_count, tier=op.tier,
            regen_command=op.regen_command,
        )
        verdict = classify(cand, facts)
        if not verdict.is_reclaimable:
            return verdict.reason or "reclassified as protected"
        return None
