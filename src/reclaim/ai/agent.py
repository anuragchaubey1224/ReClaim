"""The grounding loop (L4) — drives a `Provider` through the tool-calling contract.

This is the "agent" of docs/05: it turns a natural-language goal into tool calls over the
engine's facts, and lets the model *propose* (never execute) a reclaim plan. The loop is a
dozen lines because the provider owns history and the tools own the facts:

    user intent ─▶ model ─▶ [read-only tools + propose_plan] ─▶ engine facts ─▶ model
         ▲                                                                        │
         └──────────────  plain-English answer / proposed plan  ◀────────────────┘

Safety is structural, not prompted (docs/05 §Guardrails):
  * The toolset is the three **read-only** fact tools plus **one** `propose_plan` tool. There
    is no delete/shell tool — the most the model can do is hand back a *selection of paths*.
  * `propose_plan` does **not** remove anything. It builds a `Plan` and returns it as a
    `Proposal`; the CLI then runs it through the Safety Gate (fresh, at apply time — I6) and
    requires human confirmation before `QuarantineStore.apply`.
  * Every model turn's tool calls run against the frozen scan snapshot, so the model reasons
    over engine facts and cannot invent a path or a size.

The loop is `Provider`-agnostic, so a scripted fake drives it in tests with zero network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from reclaim.ai.providers.base import Provider, ToolResult, ToolSpec, tool_specs
from reclaim.ai.tools import TOOLS, ToolContext, ToolError, dispatch, select_paths
from reclaim.core.model import Plan
from reclaim.humanize import human_bytes

#: The one non-read tool. Still cannot execute — it records a selection the human confirms.
PROPOSE_PLAN = "propose_plan"

# How many tool round-trips one user turn may take before we stop (runaway backstop).
DEFAULT_MAX_STEPS = 8

_PROPOSE_SPEC = ToolSpec(
    name=PROPOSE_PLAN,
    description=(
        "Propose reclaiming a specific set of unit paths. This does NOT delete anything: it "
        "records a plan that the user must review and confirm, and the safety gate re-checks "
        "before anything is removed. Call this once you've chosen which units to reclaim. "
        "Only pass paths that came from list_reclaimable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "Exact unit paths to propose for removal."},
        },
        "required": ["paths"],
        "additionalProperties": False,
    },
)

SYSTEM_PROMPT = """\
You are Reclaim's assistant. You help a developer safely free disk space by removing \
regenerable build artifacts (node_modules, virtualenvs, build caches, …) — never their real \
work.

You can ONLY use these tools; you have no ability to delete, move, or run anything:
  - list_reclaimable: see reclaimable units (green = cheap to rebuild, yellow = costly). \
Protected files are never listed.
  - get_project_facts: a project's git state, dormancy, and type — use it to justify why \
removing something is safe.
  - estimate_plan: preview the space a specific set of paths would free.
  - propose_plan: hand a chosen set of paths to the user for confirmation. This does not \
delete anything; the user confirms and a safety gate re-checks before removal.

Rules:
  - Ground every claim in a tool result. Never invent a path, size, or git state.
  - Prefer green units. If you include a yellow (costly) unit, say so and why.
  - When the user wants to free space: list_reclaimable, optionally check project facts to \
confirm safety, then propose_plan with the chosen paths.
  - Be concise. Report sizes in human units and explain briefly why the selection is safe.
"""


@dataclass(frozen=True, slots=True)
class Proposal:
    """A plan the model proposed, pending human confirmation + Safety Gate (never executed
    here). `excluded`/`not_found` explain any requested path that couldn't be included."""

    plan: Plan
    paths: tuple[str, ...]
    excluded: tuple[dict[str, str], ...] = ()
    not_found: tuple[str, ...] = ()

    @property
    def is_actionable(self) -> bool:
        return not self.plan.is_empty


@dataclass(frozen=True, slots=True)
class AgentReply:
    """One user turn's outcome: the model's prose and, if it proposed one, a `Proposal`."""

    text: str
    proposal: Proposal | None = None
    steps: int = 0
    truncated: bool = False           # hit max_steps with the model still calling tools


class Agent:
    """A grounded chat agent over one classified scan. Stateful for a session."""

    def __init__(self, provider: Provider, ctx: ToolContext, *,
                 system: str = SYSTEM_PROMPT, max_steps: int = DEFAULT_MAX_STEPS) -> None:
        self.provider = provider
        self.ctx = ctx
        self._system = system
        self._max_steps = max_steps
        self._started = False

    def start(self) -> None:
        """Open the session: system prompt + the read-only tools plus `propose_plan`."""
        self.provider.start(self._system, list(tool_specs(TOOLS)) + [_PROPOSE_SPEC])
        self._started = True

    def send(self, message: str) -> AgentReply:
        """Send a user message and run the tool loop until the model answers in prose."""
        if not self._started:
            self.start()
        return self._drive(self.provider.send_user(message))

    def _drive(self, turn) -> AgentReply:
        proposal: Proposal | None = None
        steps = 0
        while turn.wants_tools:
            if steps >= self._max_steps:
                return AgentReply(text=turn.text, proposal=proposal, steps=steps,
                                  truncated=True)
            steps += 1
            results = []
            for call in turn.tool_calls:
                if call.name == PROPOSE_PLAN:
                    proposal, content = self._propose(dict(call.arguments))
                    results.append(ToolResult(call.id, content))
                else:
                    results.append(self._run_tool(call))
            turn = self.provider.send_tool_results(results)
        return AgentReply(text=turn.text, proposal=proposal, steps=steps)

    def _run_tool(self, call) -> ToolResult:
        try:
            out = dispatch(call.name, dict(call.arguments), self.ctx)
            return ToolResult(call.id, json.dumps(out))
        except ToolError as e:
            return ToolResult(call.id, json.dumps({"error": str(e)}), is_error=True)

    def _propose(self, args: dict) -> tuple[Proposal, str]:
        """Build a `Proposal` from the model's chosen paths and a JSON summary for the model.

        The summary tells the model the plan was recorded (and what couldn't be included) —
        it explicitly does *not* claim anything was removed."""
        paths = args.get("paths") or []
        selected, excluded, not_found = select_paths(self.ctx, paths)
        plan = Plan.from_candidates(selected)
        proposal = Proposal(plan=plan, paths=tuple(paths),
                            excluded=tuple(excluded), not_found=tuple(not_found))
        content = json.dumps({
            "proposed": True,
            "item_count": len(plan.operations),
            "total_bytes": plan.total_bytes,
            "total_bytes_human": human_bytes(plan.total_bytes),
            "risks": list(plan.risks),
            "excluded": excluded,
            "not_found": not_found,
            "note": "Plan recorded for the user to review and confirm. Nothing has been "
                    "removed; the safety gate re-checks it before any removal.",
        })
        return proposal, content
