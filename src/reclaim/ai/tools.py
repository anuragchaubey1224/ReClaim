"""Read-only fact tools (L4) — the only capabilities the AI agent is ever given.

These are pure functions over an already-computed, *classified* `ScanResult` (docs/05
"grounded tool-use, not free-form chat"). The scan happens once — expensive — then the agent
queries the cached engine facts through this narrow surface. Three tools:

  * `list_reclaimable(filter)` — the classifier's reclaimable units as fact rows.
  * `get_project_facts(path)`  — a project's git-state / activity / type.
  * `estimate_plan(paths)`     — totals + per-item tier + risks for a specific selection.

Design guarantees that make this safe to hand to an LLM (docs/05 §Guardrails):
  * **Read-only.** Nothing here mutates the filesystem or the store. There is deliberately
    no `delete`/`run_shell` tool — the most the agent can do (in 2b) is *propose* a plan,
    which the Safety Gate re-validates.
  * **Grounded.** Every value returned is an engine fact (path, size, tier, git-state,
    regen command) — never a free-form model claim. The agent reasons over these, so it
    cannot hallucinate a path or a size.
  * **Fail-safe.** 🔴 units are never selectable: `list_reclaimable` omits them and
    `estimate_plan` reports any 🔴/unknown path it is handed as *excluded*, never planned.

The functions return plain JSON-serialisable dicts so a provider can hand them straight back
to the model as tool results. `dispatch()` runs a tool by name for the agent loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from reclaim.core.classifier import CONFIDENCE_THRESHOLD
from reclaim.core.model import Candidate, Plan, ProjectFacts, ScanResult, Tier
from reclaim.humanize import human_bytes

# How many rows `list_reclaimable` returns by default. Bounded so a huge scan can't blow the
# model's context; the response always reports `total_matched` + `truncated` so nothing is
# silently hidden (honest grounding).
DEFAULT_LIST_LIMIT = 50

_TIER_NAME = {Tier.REGENERABLE: "green", Tier.REGENERABLE_COSTLY: "yellow",
              Tier.IRREPLACEABLE: "red"}


class ToolError(Exception):
    """A tool was called that doesn't exist, or with structurally invalid arguments.

    Domain-level 'nothing matched' is *not* an error — it's returned as data so the agent
    can adapt. This is reserved for programming/agent mistakes the loop surfaces as an
    `is_error` tool result."""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Immutable snapshot the tools read from: one classified scan + a planner-free view.

    Held for the life of a chat turn. All three tools are pure functions of this context and
    their arguments, which is what makes them testable with zero LLM/network."""

    scan: ScanResult

    @property
    def facts_by_root(self) -> dict[Path, ProjectFacts]:
        return {p.root: p for p in self.scan.projects}

    def _facts_for_root(self, root: Path | None) -> ProjectFacts | None:
        return self.facts_by_root.get(root) if root is not None else None


# --------------------------------------------------------------------------- #
# Fact serialisation — Candidate / ProjectFacts → JSON-safe dict
# --------------------------------------------------------------------------- #

def _candidate_row(c: Candidate, facts: ProjectFacts | None) -> dict[str, Any]:
    return {
        "path": str(c.path),
        "kind": c.kind,
        "size_bytes": c.size_allocated,
        "size_human": human_bytes(c.size_allocated),
        "file_count": c.file_count,
        "tier": _TIER_NAME[c.tier],
        "confidence": round(c.confidence, 2),
        "low_confidence": c.confidence < CONFIDENCE_THRESHOLD,
        "reason": c.reason,
        "regen_command": c.regen_command,
        "project_root": str(c.project_root) if c.project_root else None,
        "dormant": facts.is_dormant if facts is not None else None,
    }


def _project_row(p: ProjectFacts) -> dict[str, Any]:
    return {
        "root": str(p.root),
        "project_type": p.project_type,
        "git_status": p.git.status.value,
        "git_detail": p.git.detail,
        "is_wip": p.git.is_wip,
        "is_protected": p.is_protected,
        "last_activity_days": p.last_activity_days,
        "is_dormant": p.is_dormant,
    }


# --------------------------------------------------------------------------- #
# Tool: list_reclaimable
# --------------------------------------------------------------------------- #

def list_reclaimable(
    ctx: ToolContext,
    *,
    kind: str | None = None,
    tier: str | None = None,
    min_bytes: int = 0,
    dormant_only: bool = False,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Reclaimable units (🟢/🟡 only) as fact rows, largest first.

    Grounding surface for "what can I clean up?". 🔴 is never included. `tier` narrows to one
    class; `dormant_only` keeps units whose enclosing project is dormant. Rows are capped at
    `limit`, but `total_matched`/`truncated` always report the full picture."""
    if tier is not None and tier not in ("green", "yellow"):
        raise ToolError(f"tier must be 'green' or 'yellow', got {tier!r}")
    facts_by_root = ctx.facts_by_root

    matched: list[tuple[Candidate, ProjectFacts | None]] = []
    for c in ctx.scan.candidates:
        if not c.is_reclaimable:                       # drops 🔴 (fail-safe)
            continue
        if kind is not None and c.kind != kind:
            continue
        if tier is not None and _TIER_NAME[c.tier] != tier:
            continue
        if c.size_allocated < min_bytes:
            continue
        facts = facts_by_root.get(c.project_root) if c.project_root else None
        if dormant_only and (facts is None or not facts.is_dormant):
            continue
        matched.append((c, facts))

    matched.sort(key=lambda t: t[0].size_allocated, reverse=True)
    total_bytes = sum(c.size_allocated for c, _ in matched)
    limit = max(0, limit)
    shown = matched[:limit]
    return {
        "returned": len(shown),
        "total_matched": len(matched),
        "truncated": len(matched) > len(shown),
        "total_bytes": total_bytes,
        "total_bytes_human": human_bytes(total_bytes),
        "items": [_candidate_row(c, f) for c, f in shown],
    }


# --------------------------------------------------------------------------- #
# Tool: get_project_facts
# --------------------------------------------------------------------------- #

def get_project_facts(ctx: ToolContext, *, path: str) -> dict[str, Any]:
    """Git-state / activity / type for the project at (or enclosing) `path`.

    Resolves only against projects the scan already analysed — the agent reasons over facts
    the engine produced, never a fresh free-form claim. Returns `found: false` for a path no
    scanned project covers."""
    target = Path(path).expanduser()
    projects = ctx.scan.projects

    exact = next((p for p in projects if p.root == target), None)
    if exact is not None:
        return {"found": True, **_project_row(exact)}

    # Deepest enclosing project root, if any (most specific wins).
    enclosing = [p for p in projects if _is_within(target, p.root)]
    if enclosing:
        best = max(enclosing, key=lambda p: len(p.root.parts))
        return {"found": True, **_project_row(best)}

    return {
        "found": False,
        "path": str(target),
        "message": "no scanned project covers this path; "
                   "call list_reclaimable to see known units and their project_root",
    }


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Tool: estimate_plan
# --------------------------------------------------------------------------- #

def estimate_plan(ctx: ToolContext, *, paths: list[str]) -> dict[str, Any]:
    """Totals + per-item tier + risks for a *specific* selection of paths.

    The agent picks paths (from `list_reclaimable`) and asks "what would this free?". Honest
    by construction: a path that isn't a reclaimable unit is reported under `excluded`
    (🔴/unknown unit) or `not_found` — it can never sneak into the totals. This is preview
    only; committing is `propose_plan` → Safety Gate (Phase 2b)."""
    by_path: dict[str, Candidate] = {str(c.path): c for c in ctx.scan.candidates}

    selected: list[Candidate] = []
    excluded: list[dict[str, str]] = []
    not_found: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        key = str(Path(raw).expanduser())
        if key in seen:
            continue
        seen.add(key)
        cand = by_path.get(key)
        if cand is None:
            not_found.append(raw)
        elif cand.is_reclaimable:
            selected.append(cand)
        else:
            excluded.append({"path": raw,
                             "reason": f"{_TIER_NAME[cand.tier]} — {cand.reason or 'protected'}"})

    plan = Plan.from_candidates(selected)
    by_tier: dict[str, dict[str, int]] = {}
    for op in plan.operations:
        slot = by_tier.setdefault(_TIER_NAME[op.tier], {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += op.size_allocated

    return {
        "item_count": len(plan.operations),
        "total_bytes": plan.total_bytes,
        "total_bytes_human": human_bytes(plan.total_bytes),
        "total_files": plan.total_files,
        "by_tier": by_tier,
        "risks": list(plan.risks),
        "items": [{"path": str(op.source), "kind": op.kind,
                   "size_bytes": op.size_allocated, "size_human": human_bytes(op.size_allocated),
                   "tier": _TIER_NAME[op.tier]} for op in plan.operations],
        "excluded": excluded,
        "not_found": not_found,
    }


# --------------------------------------------------------------------------- #
# Tool registry + dispatch
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Tool:
    """One agent-callable tool: its provider-neutral schema plus the handler that runs it.

    `handler(ctx, **arguments)` returns a JSON-serialisable dict. The `input_schema` is the
    JSON Schema handed to the model (tier-restricted enums, no free-form action fields)."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]] = field(compare=False)


_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string",
                 "description": "Restrict to one unit kind, e.g. 'node_modules', '.venv'."},
        "tier": {"type": "string", "enum": ["green", "yellow"],
                 "description": "green = cheap to regenerate; yellow = slow/expensive."},
        "min_bytes": {"type": "integer", "minimum": 0,
                      "description": "Ignore units smaller than this many bytes."},
        "dormant_only": {"type": "boolean",
                         "description": "Only units in projects with no recent activity."},
        "limit": {"type": "integer", "minimum": 0,
                  "description": f"Max rows to return (default {DEFAULT_LIST_LIMIT})."},
    },
    "required": [],
    "additionalProperties": False,
}

_FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string",
                 "description": "A project root or any path inside one (e.g. a unit's path)."},
    },
    "required": ["path"],
    "additionalProperties": False,
}

_ESTIMATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "paths": {"type": "array", "items": {"type": "string"},
                  "description": "Exact unit paths (from list_reclaimable) to include."},
    },
    "required": ["paths"],
    "additionalProperties": False,
}


TOOLS: tuple[Tool, ...] = (
    Tool(
        name="list_reclaimable",
        description=(
            "List reclaimable developer artifacts the engine found (node_modules, build "
            "caches, virtualenvs, …), largest first. Read-only. Only ever returns safe "
            "(green) and costly-but-rebuildable (yellow) units — never protected files. "
            "Use this first to see what could be cleaned up."
        ),
        input_schema=_LIST_SCHEMA,
        handler=list_reclaimable,
    ),
    Tool(
        name="get_project_facts",
        description=(
            "Get the git state (clean/dirty/unpushed), activity/dormancy, and type of the "
            "project at or enclosing a path. Read-only. Use this to justify why removing a "
            "unit is or isn't safe (e.g. 'the project is git-clean and dormant')."
        ),
        input_schema=_FACTS_SCHEMA,
        handler=get_project_facts,
    ),
    Tool(
        name="estimate_plan",
        description=(
            "Estimate the total space freed by removing a specific set of unit paths, with "
            "per-item tier and risks. Read-only preview — does not remove anything. Any path "
            "that is protected or not a known unit is reported back as excluded, never "
            "included in the total."
        ),
        input_schema=_ESTIMATE_SCHEMA,
        handler=estimate_plan,
    ),
)

TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def dispatch(name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Run tool `name` with `arguments` against `ctx`; return its JSON-safe result.

    Raises `ToolError` for an unknown tool or arguments the handler rejects (unexpected
    keyword / wrong shape) — the agent loop turns that into an `is_error` tool result so the
    model can recover rather than the whole run crashing."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name!r}")
    try:
        return tool.handler(ctx, **arguments)
    except ToolError:
        raise
    except TypeError as e:                              # bad/extra kwargs from the model
        raise ToolError(f"invalid arguments for {name}: {e}") from e
