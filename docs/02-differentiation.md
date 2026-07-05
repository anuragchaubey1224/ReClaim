# 02 · Differentiation — Why This Isn't "Just Another Cleaner"

> Be honest about the landscape. An interviewer *will* know these tools. Pretending
> we're first is a red flag; showing exactly where we're better is a green one.

## The existing landscape

| Tool | What it does | Its limit |
|------|--------------|-----------|
| `ncdu`, `dust`, `dua`, `gdu` | Fast disk-usage analyzers (treemaps / sorted sizes) | **Show** you the bytes; make **you** decide and delete. Zero safety, zero meaning. |
| `kondo` | Cleans project artifacts (`node_modules`, `target/`, `.venv`) | Rule-based, all-or-nothing, no reversibility, no project *awareness* (ignores git state, activity), no explanation. |
| `npkill` | Interactively finds & deletes `node_modules` | Single artifact type. No safety net, no undo. |
| `docker system prune`, `pip cache purge`, `npm cache clean` | Per-ecosystem cleaners | Siloed. You must know each command and run them one by one. No unified view. |
| CleanMyMac, DaisyDisk | GUI cleaners for the whole Mac | Consumer-focused, paid, opaque heuristics, not dev-aware, scary for developers who don't trust a black box near their code. |

**Takeaway:** the space is crowded with tools that either *only show* (analyzers) or
*bluntly delete* (cleaners). Nobody owns the middle: **understand → explain →
safely & reversibly reclaim, with project awareness.**

## Our four differentiators

### 1. Project-aware, not file-aware 🧠
Existing tools see paths and sizes. Reclaim sees **projects**. For each project it knows:
- **Git state** — uncommitted or unpushed changes ⇒ hands off, always.
- **Activity** — last-accessed / last-modified ⇒ dormant projects are far safer.
- **Type** — Node / Python / Rust / Go / Docker ⇒ knows exactly what regenerates and how.

A dumb cleaner deletes `node_modules` in the project you're actively coding in right now.
Reclaim knows that project is hot and leaves it, and reclaims the *dormant* one instead.

### 2. Reversibility as a first-class guarantee ↩️
Nobody else offers real undo. Reclaim never does a bare `rm`. It **quarantines** items to
a managed trash zone with a TTL, so any reclaim is undoable for N days. This turns a
scary irreversible action into a safe, try-it-and-see one. (See the safety model in
[`03-product-design.md`](./03-product-design.md).)

### 3. The regenerability classification model 🏷️
The heart of the product. Every candidate is classified not as "big/small" but as:
- **Regenerable** (safe) — rebuildable by a known command.
- **Regenerable-but-costly** (caution) — rebuildable but slow/expensive (large Docker base images, HuggingFace model caches).
- **Irreplaceable** (never touch) — source, secrets, local data, uncommitted work.

This is a *meaning* layer no existing tool has. It's what makes "total confidence" possible.

### 4. Explainability via a grounded AI agent 💬
Analyzers give you a treemap and a shrug. Reclaim gives you a sentence: *"This 4 GB is
Rust build output for a project last touched in January; it rebuilds with `cargo build`;
reclaiming it is safe and undoable."* The AI turns raw facts into a decision a human can
trust — and can answer "what happens if I remove this?" before you commit.

## Positioning statement

> **For** developers whose machines fill up with invisible build clutter,
> **Reclaim is** an AI-guided reclamation engine
> **that** safely removes only regenerable files, understands your projects, and makes
> every action reversible —
> **unlike** disk analyzers that only show bytes or cleaners that bluntly delete without
> understanding what's safe.

## The one-sentence "why we win"

Everyone else answers *"what's taking space?"*
Reclaim answers *"what can I safely get back, and how do I undo it if I'm wrong?"* —
which is the question developers actually hesitate on.
