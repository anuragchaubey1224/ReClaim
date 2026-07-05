# 05 · AI Agent Design — How the AI Earns Its Place

> The single biggest risk to this project's credibility is being seen as a "ChatGPT
> wrapper with `rm`." This document exists to make sure the AI adds *real, defensible*
> value — and to give you the language to defend that choice in an interview.

---

## The rule that keeps us honest

> **The tool must be genuinely good with the AI turned off.**
> The AI is a *co-pilot on top of* a deterministic engine — never the engine itself.

If someone deletes the AI layer, `reclaim scan / status / apply / undo` still works
perfectly as a rule-based CLI. The AI makes it *easier and more trustworthy to use* — it
does not make the decisions that matter to safety. Say exactly this in an interview and
the "wrapper" objection evaporates.

---

## Where AI adds real value (and where it must NOT)

### ✅ AI's job: understand intent, plan, and explain
1. **Natural-language goals → concrete plans.**
   *"Free up ~20GB but don't touch anything I'm actively working on"* →
   the agent queries the engine's facts, selects dormant/clean projects, and assembles a
   candidate plan with an impact total. Turning fuzzy human intent into a precise,
   parameterized operation is exactly what LLMs are good at — and rules alone can't do.
2. **Explanation & trust-building.**
   *"Why is this safe?"* → grounded, plain-English answer citing the actual facts
   (regenerable tier, rebuild command, dormant, git-clean). This is the feature that
   overcomes user hesitation — the real product problem from [`01-vision.md`](./01-vision.md).
3. **Handling the long tail.**
   Novel or weird project layouts the static ruleset doesn't recognize. The AI can
   *reason* about an unfamiliar directory ("this looks like a build cache because…") and
   **propose** classifying it — but that proposal still routes through human confirmation
   and the safety gate. It never silently reclassifies toward "less safe".
4. **Preference memory.**
   *"Never touch `~/work/**`"* → persisted as a rule the agent respects thereafter.

### ❌ AI's non-job: it never decides safety and never executes with authority
- It **cannot** move an item from 🔴 irreplaceable to 🟢 safe.
- It **cannot** run a deletion directly. It emits a *proposed action object* that the
  deterministic **Safety Gate** re-validates (allowlist, git-WIP, tier) before anything
  happens.
- Its outputs are grounded on engine-provided facts, never free-form filesystem claims —
  this prevents hallucinated paths / sizes / actions.

---

## Architecture: grounded tool-use, not free-form chat

The agent is implemented with **tool-calling (function calling)**, not by letting an LLM
emit shell commands. It's given a small, safe toolset that only *reads* facts or
*proposes* validated actions:

```
Tools exposed to the agent (all mediated by the engine — none are raw shell):
  • list_reclaimable(filter)       → facts from the classifier (read-only)
  • get_project_facts(path)        → git state, activity, type (read-only)
  • estimate_plan(selection)       → total bytes, per-item tier, risks (read-only)
  • propose_plan(selection)        → hands a plan to the Safety Gate for human confirm
  • save_preference(rule)          → persists a user protection rule
```

Note there is **no `delete` tool and no `run_shell` tool.** The most the agent can do is
*propose a plan*, which a human must confirm and which the deterministic gate re-checks.
This is the core safety-through-architecture argument — and a great thing to draw on a
whiteboard.

### The grounding loop
```
user intent ─▶ agent ─▶ [read-only tools] ─▶ engine facts ─▶ agent reasons
     ▲                                                            │
     └──────────  plain-English plan + explanation  ◀────────────┘
                              │
                     user confirms ─▶ Safety Gate re-validates ─▶ execute + journal
```

---

## Guardrails (say these words in the interview)

- **Least privilege:** the model's toolset physically excludes destructive capability.
- **Grounding:** every claim the model makes is backed by an engine fact it fetched, not
  its own memory of the filesystem → no hallucinated deletions.
- **Human-in-the-loop:** confirmation is mandatory; `--yes` is opt-in and still gated.
- **Defense in depth:** even a fully compromised/hallucinating model can't cause data
  loss, because the deterministic gate below it enforces the irreplaceable tier and the
  quarantine-not-delete rule.
- **Auditability:** every proposal and action is logged in the op-journal.

---

## Model, cost & privacy

The LLM behind the agent is pluggable — the engine only depends on the *tool-calling
contract*, not on a specific provider. Two supported modes, both **₹0 to operate** (full
cost breakdown in [`07-form-factor-and-cost.md`](./07-form-factor-and-cost.md)):

| Mode | Model | Data leaves machine? | Cost |
|------|-------|----------------------|------|
| **BYOK** (default) | Claude API with the user's own key | Only minimal facts (see below) | User pays their own tokens; author pays nothing |
| **Ollama** (fallback) | A local model | **Nothing** — fully offline | Free |

**Privacy by construction:** because the agent is *grounded* on engine facts, the only
thing ever sent to a cloud model is a small, abstracted fact set — **path, size, tier,
git-state — never file contents.** With Ollama, nothing leaves the machine at all. This
is a genuine selling point, not an afterthought: *local-first, minimal-disclosure,
zero-backend.*

---

## Why this is a *strong* use of AI (the resume framing)

Most "AI projects" fail interviews because the AI is doing something a rule or a regex
could do, dressed up in a prompt. Here the division is principled:

- **Deterministic where correctness/safety matters** (classification, execution, undo).
- **AI where ambiguity and human communication matter** (intent, explanation, the long
  tail, trust).

That is exactly the architecture real production AI systems use. Being able to articulate
*why the boundary sits where it does* is a senior-level signal — arguably more impressive
than the disk-cleaning itself.
