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

## Phase 1 — The engine (MVP) _(the core project)_ ✅ **COMPLETE**
This alone is a legitimate, strong portfolio project. Shipped in three demoable
milestones: **1a** classification intelligence (read-only), **1b** reversible removal core
(journal + quarantine + safety gate), **1c** the CLI reclaim loop (planner + apply/undo).

- [x] Concurrent scanner with pruning + correct sizing. _(Phase 0)_
- [x] Three-tier rule-based classifier for the common artifacts. _(1a — safety lattice)_
- [x] Project analyzer: root detection, type, **git-state**, activity. _(1a — defensive git,
  mtime dormancy)_
- [x] `reclaim scan` / `reclaim status` with a clean grouped report. _(1a — tiered report +
  per-project fact sheets)_
- [x] Quarantine store + write-ahead journal + `reclaim apply` / `reclaim undo`. _(1b engine +
  1c CLI — atomic apply, crash recovery, cross-FS copy-verify-delete, TOCTOU re-check.)_
- [x] Planner (goal → ranked plan) + `plan`/`apply`/`undo`/`ls`/`purge` CLI. _(1c — greedy by
  safety-then-size, dry-run + confirm, `$RECLAIM_HOME` store, startup recovery.)_
- [x] Tests for the safety invariants. _(1a + 1b + 1c — git-WIP hard-protect, unknown⇒🔴,
  protect-paths win, **undo restores byte-identical**, crash rolls back, never clobbers,
  TOCTOU rejects newly-dirty — **70 tests green**.)_
- **Exit criteria:** ✅ a stranger can safely reclaim space with it, no AI involved.

> **1a (done):** `reclaim status` classifies a real 290K-file tree in ~3.4 s into 🟢/🟡/🔴
> with git-state and dormancy per project; nothing is ever removed (read-only).
>
> **1b (done):** reversible removal core — never `rm` (move to quarantine), write-ahead
> journal with startup crash-recovery, atomic all-or-nothing transactions, and a Safety Gate
> that re-checks git at apply time.
>
> **1c (done):** the CLI reclaim loop — `reclaim plan/apply/undo/ls/purge`. Planner selects
> greedily by (safety, then size); apply is dry-run + confirm by default and fully undoable.
> End-to-end scan→plan→gate→apply→undo verified byte-identical.

## Phase 2 — The AI agent _(the differentiator)_ ✅ **COMPLETE**
Shipped in three milestones — **2a** tool layer + provider contract, **2b** grounded chat +
agent loop, **2c** preference memory + explanations — plus bring-your-own-provider.
- [x] `reclaim chat` with grounded tool-calling. _(2b — manual tool-use loop over the engine)_
- [x] Read-only fact tools + `propose_plan` routed through the Safety Gate. _(2a/2b — the
  agent can only read facts and *propose*; every removal re-validates at the gate.)_
- [x] Natural-language goals → plans; "why is this safe?" explanations. _(2c — `explain_unit`)_
- [x] Preference memory (`never touch ~/work/**`). _(2c — `reclaim protect`, enforced by the
  deterministic engine at classify + apply time.)_
- [x] **Bring your own provider** — Claude (default), OpenRouter, OpenAI, any OpenAI-compatible
  endpoint, or a fully-local Ollama; the engine works fully with the agent absent (I7).
- **Exit criteria:** ✅ the demo paragraph in [`01-vision.md`](./01-vision.md) works.

## Phase 3 — The ambient product _(polish & wow)_
- [x] **User config file for custom rules/protections.** _(3a — `~/.reclaim/config.toml`:
  custom reclaimable units + protections, folded into a `Ruleset` threaded through
  scan → classify → gate. Fail-safe parsing; protections always win. `reclaim config` /
  `--init`. See [`config-reference.md`](./config-reference.md).)_
- [x] **Packaging & polish.** _(3e — `pipx`-installable (`pipx install .` / `git+…`); MIT
  `LICENSE`; packaging metadata (v0.1.0, urls, classifiers); a **reproducible** terminal demo
  (`demo/reclaim.tape`, rendered by `vhs`) instead of a hand-recorded GIF; a real,
  current-state root README; `CHANGELOG.md`.)_
- [x] **Trends / history** ("node_modules grew 3 GB this month"). _(3b — every read-only scan
  appends a compact snapshot to `$RECLAIM_HOME/history.jsonl`; `reclaim trends [--since 7d|2w|3m]`
  reports the per-kind change since a baseline, `reclaim history` lists the raw snapshots.)_
- [ ] Background daemon: watch disk growth, warn before the wall.
- [ ] TUI dashboard.

> **3e (done):** `reclaim` installs as a single CLI (`pipx install .` verified end-to-end —
> wheel builds, console script runs). The demo is generated from a checked-in `vhs` tape so it
> never drifts out of date. README reflects the real surface (engine + AI + config, 171 tests,
> 3-OS CI).
>
> **3b (done):** history is an append-only JSONL log keyed by scanned root, with an injectable
> clock and fail-safe load (a corrupt line or write error never breaks a scan). `reclaim trends`
> picks the most recent baseline at least the look-back window old (falling back to the earliest
> snapshot) and shows signed per-kind deltas. Recording is on scan/status only; opt out with
> `RECLAIM_NO_HISTORY`. +24 tests → **195 total.**

> **3a (done):** a `Ruleset` bundles the built-in + user-config rules and flows through the
> whole pipeline, so a custom unit is recognized like `node_modules` and a custom protection is
> enforced at classify time *and* re-enforced at the Safety Gate (I6). Config can only ever
> *add* protection — a name that's both a unit and a protection stays protected (I2). Broken
> config degrades to the built-ins with a visible warning, never a crash. +24 tests.

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
