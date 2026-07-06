"""Planner (L2) — turn a goal into a concrete, ranked Plan.

Greedy selection ordered by **(safety, then size)** (ARCHITECTURE.md §6 Planner, AD10):
safest tier first (🟢 before 🟡), largest first within a tier — so a free-space target is
met with the fewest, safest removals. O(n log n), fully explainable, and usable from the CLI
with zero AI (`reclaim plan --free 20G`).

The planner only ever selects candidates the classifier already marked reclaimable (🟢/🟡);
🔴 items are dropped when the Plan is built (`Plan.from_candidates`) and re-checked again by
the Safety Gate at apply time. The planner's job is *selection*, not safety.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reclaim.core.classifier import CONFIDENCE_THRESHOLD
from reclaim.core.model import Candidate, Plan, ProjectFacts, ScanResult, Tier
from reclaim.humanize import human_bytes

_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}


def parse_size(text: str) -> int:
    """Parse a human size like '20G', '500m', '1.5GB', '4096' → bytes.

    Raises ValueError on garbage so the CLI can report it cleanly."""
    s = text.strip().upper()
    if s.endswith("B"):
        s = s[:-1]
    if not s:
        raise ValueError(f"invalid size: {text!r}")
    mult = 1
    if s[-1] in _UNITS:
        mult = _UNITS[s[-1]]
        s = s[:-1]
    try:
        value = float(s)
    except ValueError as e:
        raise ValueError(f"invalid size: {text!r}") from e
    if value < 0:
        raise ValueError(f"size cannot be negative: {text!r}")
    return int(value * mult)


@dataclass(frozen=True, slots=True)
class PlanGoal:
    """What the user wants reclaimed. All fields optional; the default is 'all safe units'."""

    free_bytes: int | None = None            # stop once this much is selected; None = all
    include_costly: bool = False             # also take 🟡 (slow/expensive to rebuild)
    dormant_only: bool = False               # only units in dormant projects
    kinds: frozenset[str] | None = None      # restrict to specific kinds
    min_bytes: int = 0                       # ignore units smaller than this
    include_low_confidence: bool = False     # also take units below the confidence threshold


@dataclass(frozen=True, slots=True)
class PlanResult:
    """A built plan plus why each candidate was left out (for an explainable preview)."""

    plan: Plan
    goal: PlanGoal
    considered: int                          # reclaimable candidates examined
    excluded: tuple[tuple[Candidate, str], ...] = field(default_factory=tuple)

    @property
    def selected(self) -> int:
        return len(self.plan.operations)


# Selection order: green (0) before yellow (1); this is the "safety first" key.
_TIER_RANK = {Tier.REGENERABLE: 0, Tier.REGENERABLE_COSTLY: 1}


class Planner:
    def plan(self, result: ScanResult, goal: PlanGoal | None = None) -> PlanResult:
        goal = goal or PlanGoal()
        facts_by_root: dict[Path, ProjectFacts] = {p.root: p for p in result.projects}

        eligible: list[Candidate] = []
        excluded: list[tuple[Candidate, str]] = []
        considered = 0
        for c in result.candidates:
            if not c.is_reclaimable:
                continue                     # 🔴 never counts as considered
            considered += 1
            reason = self._reject_reason(c, goal, facts_by_root)
            if reason is None:
                eligible.append(c)
            else:
                excluded.append((c, reason))

        # Rank by (safety tier, then size desc): safest first, biggest first within a tier.
        eligible.sort(key=lambda c: (_TIER_RANK.get(c.tier, 9), -c.size_allocated))

        selected: list[Candidate] = []
        running = 0
        for c in eligible:
            if goal.free_bytes is not None and running >= goal.free_bytes:
                excluded.append((c, "free-space target already met"))
                continue
            selected.append(c)
            running += c.size_allocated

        plan = Plan.from_candidates(selected)
        risks = list(plan.risks)
        if goal.free_bytes is not None and running < goal.free_bytes:
            risks.append(
                f"target {human_bytes(goal.free_bytes)} not fully reachable — "
                f"only {human_bytes(running)} of safe space available"
            )
        plan = Plan(plan.operations, tuple(risks))
        return PlanResult(plan=plan, goal=goal, considered=considered,
                          excluded=tuple(excluded))

    def _reject_reason(
        self, c: Candidate, goal: PlanGoal, facts_by_root: dict[Path, ProjectFacts]
    ) -> str | None:
        if c.tier is Tier.REGENERABLE_COSTLY and not goal.include_costly:
            return "costly (🟡) — needs --include-costly"
        if not goal.include_low_confidence and c.confidence < CONFIDENCE_THRESHOLD:
            return f"low confidence ({c.confidence:.2f})"
        if c.size_allocated < goal.min_bytes:
            return "below --min-size"
        if goal.kinds is not None and c.kind not in goal.kinds:
            return "kind not requested"
        if goal.dormant_only:
            facts = facts_by_root.get(c.project_root) if c.project_root else None
            if facts is None or not facts.is_dormant:
                return "project not dormant"
        return None
