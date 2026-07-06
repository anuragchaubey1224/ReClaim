# 06 · Roadmap

Phased so that **each phase ships something demoable and resume-worthy on its own.**
Don't wait for the AI to have a working product — the deterministic engine is the
foundation and is impressive by itself.

---

## Phase 0 — Spike & decide _(a weekend)_ ✅ **COMPLETE**
Prove the risky part early. **Language decided: Python** (see [`04-technical-depth.md`](./04-technical-depth.md) §7).
- [x] Parallel scanner (`os.scandir` + thread pool, `Queue.join()` termination); measured vs `du`: **~1.57× faster** on 290K files (see [`08-benchmarks-and-results.md`](./08-benchmarks-and-results.md)).
- [x] Byte-accounting confirmed on real machine: **0-byte difference from `du`** over 290K files; hard-link dedup validated with a controlled test (shared inode counted once).
- [x] Module boundaries established (`platform / core.scanner / core.rules / core.model / cli`) so the hot path stays swappable later.
- **Exit criteria:** ✅ scans the home/projects dir fast and prints total reclaimable bytes.

## Phase 1 — The engine (MVP) _(the core project)_
This alone is a legitimate, strong portfolio project.
- [ ] Concurrent scanner with pruning + correct sizing.
- [ ] Three-tier rule-based classifier for the common artifacts.
- [ ] Project analyzer: root detection, type, **git-state**, activity.
- [ ] `reclaim scan` / `reclaim status` with a clean grouped report.
- [ ] Quarantine store + write-ahead journal + `reclaim apply` / `reclaim undo`.
- [ ] Tests for the safety invariants (never touches 🔴, undo restores exactly).
- **Exit criteria:** a stranger can safely reclaim space with it, no AI involved.

## Phase 2 — The AI agent _(the differentiator)_
- [ ] `reclaim chat` with grounded tool-calling (Claude API).
- [ ] Read-only fact tools + `propose_plan` routed through the Safety Gate.
- [ ] Natural-language goals → plans; "why is this safe?" explanations.
- [ ] Preference memory (`never touch ~/work/**`).
- **Exit criteria:** the demo paragraph in [`01-vision.md`](./01-vision.md) actually works.

## Phase 3 — The ambient product _(polish & wow)_
- [ ] Background daemon: watch disk growth, warn before the wall.
- [ ] Trends ("Docker grew 12 GB this month").
- [ ] TUI dashboard.
- [ ] User config file for custom rules/protections.
- [ ] `brew install` / one-line installer, real README, demo GIF.

---

## Resume / portfolio checklist (do these alongside the build)
- [ ] A README with a **demo GIF** and a **benchmark number** ("X GB scanned in Ys, Z× faster than `du`").
- [ ] An **ARCHITECTURE.md** (lift from [`04-technical-depth.md`](./04-technical-depth.md)) — signals you design before you code.
- [ ] Tests that encode the **safety invariants** — reviewers love "this test proves it can never delete uncommitted work."
- [ ] One blog post / writeup on *"how I made file deletion reversible and crash-safe"* — this is the piece that gets shared.
- [ ] Resume bullet framing (draft):
  > Built **Reclaim**, an AI-guided disk-reclamation engine (Python): an I/O-parallel
  > filesystem scanner (comparable to `du`, via `os.scandir` + a thread pool), a
  > fail-safe 3-tier classifier, and crash-safe reversible deletes via a write-ahead
  > journal + quarantine, with a grounded LLM agent that plans and explains cleanups but
  > is architecturally incapable of unsafe actions. Modular engine with a portability
  > boundary for future hot-path rewrite (PyO3).

## Sequencing principle
> Build **depth-first on safety and speed**, not breadth-first on features.
> One rock-solid, fast, reversible reclaim of `node_modules` beats ten shaky cleaners.
