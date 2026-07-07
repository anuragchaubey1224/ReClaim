# Reclaim — Documentation

> **Working name:** `Reclaim` _(provisional — see alternatives below)_

**One-liner:**
Reclaim is an AI-guided disk **reclamation engine** for developers. It safely finds
and removes *regenerable* clutter — `node_modules`, build caches, Docker layers,
virtualenvs, `__pycache__` — with full explainability and **one-command undo**.

**The core promise (our wedge):**
> Every byte we remove is regenerable. We never delete your work —
> we reclaim only what your machine can rebuild.

---

## Why this doc exists

Before writing a single line of code, this folder pins down *what* we are building,
*why it is different* from the dozen tools that already exist, and *where the real
engineering depth is*. This is the difference between "another AI wrapper project"
and a product that shows genuine CS depth to a hiring manager.

## Reading order

| # | Document | What it answers |
|---|----------|-----------------|
| 1 | [`01-vision.md`](./01-vision.md) | The problem, the target user, the vision, why now |
| 2 | [`02-differentiation.md`](./02-differentiation.md) | The competitive landscape and our concrete edge |
| 3 | [`03-product-design.md`](./03-product-design.md) | The "Reclaim model", the classification engine, the safety model, UX flow, feature set + MVP scope |
| 4 | [`04-technical-depth.md`](./04-technical-depth.md) | The CS concepts and architecture — the **resume gold** |
| 5 | [`05-ai-agent-design.md`](./05-ai-agent-design.md) | How the AI adds real value instead of being a chat wrapper |
| 6 | [`06-roadmap.md`](./06-roadmap.md) | Phased milestones from MVP to full product |
| 7 | [`07-form-factor-and-cost.md`](./07-form-factor-and-cost.md) | What you install (CLI), where it runs (Mac/Linux/Windows), and why it costs ₹0 to operate |
| 8 | [`08-benchmarks-and-results.md`](./08-benchmarks-and-results.md) | Living log of measured test + benchmark results per phase (for README/resume) |

## Hands-on guides

| Guide | For |
|-------|-----|
| [`windows-testing-guide.md`](./windows-testing-guide.md) | Step-by-step setup + safe test walkthrough for a Windows tester (sandbox first, then real projects) |
| [`config-reference.md`](./config-reference.md) | The optional `~/.reclaim/config.toml` — teach Reclaim custom reclaimable units + custom protections |

## Name options (decide later)

| Name | Feel | Note |
|------|------|------|
| **Reclaim** | Verb, confident, on-message | Current working name. Common word (weaker for SEO/branding) |
| **Attic** | Metaphor: where old stuff piles up | Distinctive, brandable |
| **Sweep** / **DevSweep** | Action, clean | Simple, memorable |
| **Kondo** | Marie-Kondo reference | ❌ A real tool already uses this |
| **Reap** | "reap what you can regrow" | Edgy, short |

**Decision:** keep `Reclaim` as working name; revisit once the MVP is real.

> **The technical architecture** (system + every subsystem, data model, concurrency,
> performance strategy, ADRs, and the detailed phase-by-phase build plan) lives in the
> root-level [`../ARCHITECTURE.md`](../ARCHITECTURE.md). These docs are the *why/what*;
> that file is the *how*.

## Current status

- [x] Ideation & product definition (this folder)
- [x] System architecture & build plan (`../ARCHITECTURE.md`)
- [x] Phase 0 — scanner spike + repo scaffold ([results](./08-benchmarks-and-results.md): 5/5 tests, ~1.4× faster than `du`)
- [x] Phase 1 — deterministic engine (MVP) — scan/classify/plan/apply/undo/purge, reversible + crash-safe
- [x] Phase 2 — AI agent layer — grounded `reclaim chat`, preference memory, bring-your-own-provider
- [ ] Phase 3 — ambient product: **[x] 3a config file** ([`config-reference.md`](./config-reference.md)) · trends · background daemon · TUI · packaging
