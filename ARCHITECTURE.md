# Reclaim — System Architecture

> An AI-guided disk-reclamation engine for developers.
> **Local-first · cross-platform · reversible · faster than `du`.**

This file is **architecture only** — diagrams, workflows, and the load-bearing decisions.
Project narrative, usage, and marketing live in the [`README`](./README.md) and
[`docs/`](./docs/). No implementation code here.

---

## Table of contents

1. [Design principles & invariants](#1-design-principles--invariants)
2. [System context](#2-system-context)
3. [Layered architecture](#3-layered-architecture)
4. [Component map](#4-component-map)
5. [Data transformation flow](#5-data-transformation-flow)
6. [Subsystems — responsibilities & architectural points](#6-subsystems--responsibilities--architectural-points)
7. [Workflow diagrams](#7-workflow-diagrams)
8. [Concurrency model](#8-concurrency-model)
9. [Performance strategy](#9-performance-strategy)
10. [Key design decisions (ADRs)](#10-key-design-decisions-adrs)
11. [Package & directory layout](#11-package--directory-layout)
12. [Build phases](#12-build-phases)

---

## 1. Design principles & invariants

Non-negotiable rules every subsystem preserves. These *are* the architecture's backbone.

| # | Invariant | Enforced by |
|---|-----------|-------------|
| **I1** | Reclaim never removes anything without a known **regeneration path**. | Classifier · Safety Gate |
| **I2** | **Unknown ⇒ irreplaceable** (fail-safe default). Ambiguity resolves toward *keep*. | Classifier |
| **I3** | Nothing is **hard-deleted** on apply — reclaim = move to quarantine with a TTL. | Quarantine |
| **I4** | Every operation is **crash-safe and reversible** until its TTL expires. | Journal (write-ahead) |
| **I5** | The **AI proposes, never executes** — all mutations pass the Safety Gate. | Safety Gate |
| **I6** | Safety is **re-checked at apply time**, not only at plan time (TOCTOU defense). | Safety Gate |
| **I7** | The engine is **fully functional with the AI off** — AI is a co-pilot, not the core. | Layering |
| **I8** | **Local-first** — only minimal abstracted facts ever leave the machine (BYOK only). | AI boundary |

> Everything **below** the Safety Gate is deterministic and testable without an LLM.
> The AI sits strictly **above** it and holds no destructive capability.

---

## 2. System context

```
  ┌─────────────┐    commands       ┌────────────────────────┐   readdir / stat    ┌───────────────┐
  │  Developer  │ ─────────────────▶│        RECLAIM         │ ───────────────────▶│ Local         │
  │ (terminal)  │◀───────────────── │  CLI  +  local engine  │◀─────────────────── │ Filesystem    │
  └─────────────┘  tables / plans   └───────────┬────────────┘  sizes / metadata   │ ~/dev, caches │
                                                │                                   └───────────────┘
                      minimal facts             │  only in `reclaim chat`
                      (path, size, tier)        ▼  (skipped entirely with Ollama)
                                     ┌────────────────────────┐
                                     │      LLM provider      │
                                     │  Claude API  │ Ollama  │   Ollama = 100% local, no network
                                     └────────────────────────┘
```

- **No backend, no server, no hosting.** Reclaim is a single local process.
- The only outbound call is the optional LLM request in `chat`; Ollama mode makes it zero.

---

## 3. Layered architecture

Strict layers. Dependencies point **downward only** — no layer reaches up.

```
┌───────────────────────────────────────────────────────────────────────┐
│ L5  INTERFACES        CLI (typer) · TUI (textual) · chat REPL           │
├───────────────────────────────────────────────────────────────────────┤
│ L4  AI AGENT          grounded tool-calling · BYOK Claude / Ollama      │
│                       read-only fact tools + propose_plan (no execute)  │
├───────────────────────────────────────────────────────────────────────┤
│ L3  SAFETY GATE       deterministic guards · TOCTOU re-check · dry-run  │
│                       allowlist · irreplaceable-tier enforcement        │
├───────────────────────────────────────────────────────────────────────┤
│ L2  CORE ENGINE       Scanner → Classifier → Project Analyzer → Planner │
│     (pure library)    Quarantine · Journal · Cache index                │
├───────────────────────────────────────────────────────────────────────┤
│ L1  PLATFORM          fs · block-size · hardlink dedup · cache dirs ·   │
│     ABSTRACTION       long-paths · watchers        (Posix / Windows)    │
├───────────────────────────────────────────────────────────────────────┤
│ L0  STORAGE           SQLite (index/cache/prefs) · quarantine store ·   │
│                       append-only op-journals                           │
└───────────────────────────────────────────────────────────────────────┘
```

Two architectural seams carry the whole design:
**`L1 platform`** (makes cross-platform cheap) and **`L2 core` as a pure library**
(makes a future Rust hot-path swap possible without touching UI/AI).

---

## 4. Component map

How the subsystems wire together. Both the CLI and the AI produce a `Plan`; both funnel
through the **one** Safety Gate.

```
                    ┌──────────────────────────────────────────────┐
                    │                INTERFACES (L5)               │
                    │        CLI   ·   TUI   ·   chat REPL         │
                    └──────┬────────────────────────────┬──────────┘
              (commands)   │                            │   (natural language)
                           ▼                            ▼
                   ┌───────────────┐            ┌──────────────────┐
                   │ Command        │            │  AI AGENT  (L4)  │
                   │ handlers       │            │  grounded loop   │
                   └───────┬────────┘            │  read + propose  │
                           │        both emit a  └────────┬─────────┘
                           │           Plan               │
                           └──────────────┬───────────────┘
                                          ▼
                    ┌──────────────────────────────────────────────┐
                    │              SAFETY GATE (L3)                 │
                    │     validate · TOCTOU re-check · dry-run      │
                    └───────────────────┬──────────────────────────┘
                                        │  approved operations only
      ┌───────────┬───────────┬─────────┼──────────┬──────────────┐
      ▼           ▼           ▼         ▼          ▼              ▼
 ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌────────┐ ┌────────────┐ ┌─────────┐
 │ Scanner │▶│Classifier│▶│ Project │ │Planner │ │ Quarantine │ │ Journal │   CORE (L2)
 │         │ │          │ │ Analyzer│ │        │ │   store    │ │  (WAL)  │
 └────┬────┘ └────┬─────┘ └────┬────┘ └────────┘ └─────┬──────┘ └────┬────┘
      └───────────┴────────────┴───────────┬───────────┴─────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │           PLATFORM ABSTRACTION (L1)          │
                    └───────────────────┬──────────────────────────┘
                                        ▼
                    ┌──────────────────────────────────────────────┐
                    │  STORAGE (L0)  SQLite · journals · quarantine │
                    └──────────────────────────────────────────────┘
```

---

## 5. Data transformation flow

The pipeline is a series of immutable objects; each stage refines the last. This chain is
the **stable contract** between subsystems (and the portability boundary).

```
   Local filesystem
        │  scan  (parallel walk)
        ▼
   FsNode ─────────── path · allocated size (blocks) · dev/ino for dedup
        │  group by project root
        ▼
   ProjectFacts ───── kind · git-state · dormancy · protected paths
        │  classify WITH project context
        ▼
   Candidate ──────── tier 🟢/🟡/🔴 · regen command · confidence · reason
        │  select for a goal (greedy: safest & biggest first)
        ▼
   Plan  =  [ Operation … ]  +  total bytes  +  human-readable risks
        │  Safety Gate approves (+ TOCTOU re-check)
        ▼
   Quarantine move (journaled)  ───▶  space freed · undoable for N days
```

---

## 6. Subsystems — responsibilities & architectural points

One line of responsibility + the genuinely load-bearing design points per subsystem.
Deep flows are in [§7](#7-workflow-diagrams).

**Platform Abstraction (L1)** — *hide every OS difference behind one interface.*
- Isolates: allocated-size calc, hardlink dedup key, cache-dir locations, long-path &
  case rules, file-watchers. Nothing else in the codebase branches on OS.
- Makes macOS + Linux + Windows a **single codebase**, verified by a 3-OS CI matrix.

**Scanner (L2)** — *walk roots fast, with correct byte accounting.*
- Threaded parallel walk (I/O-bound → GIL released on syscalls).
- **Opaque-blob pruning:** recognizes a reclaimable unit (e.g. `node_modules`), sums its
  size, and does **not** descend — the core reason we beat `du` (see §9).
- Reports **allocated** size (blocks), counts **hardlinks once**, never follows symlinks.

**Classifier (L2)** — *assign every candidate a tier + regen path.* (owns I1, I2)
- Data-driven rules; basename rules compile to an **O(1) lookup** used inline by the scanner.
- Evaluated as a **safety lattice**: highest-precedence (most protective) rule wins;
  no match ⇒ irreplaceable.
- Context-aware: `target/` is Rust output *only if* a `Cargo.toml` encloses it.

**Project Analyzer (L2)** — *turn paths into projects with facts.*
- Root/type detection via markers (`.git`, `package.json`, `Cargo.toml`, …).
- **Defensive git-state:** any uncertainty (dirty, detached, no upstream, git missing) ⇒
  treated as WIP ⇒ protected.
- Activity via **mtime** (atime is unreliable under `noatime`) ⇒ dormancy score.

**Planner (L2)** — *goal → concrete, ranked Plan.*
- Greedy selection ordered by **(safety, then size)** — explainable, O(n log n).
- Fully usable from the CLI with zero AI (`reclaim plan --free 20G`).

**Safety Gate (L3)** — *the single choke point for every mutation.* (owns I5, I6)
- Re-validates each op: tier, regen path, allowlist, **git re-check at apply time**,
  existence — before anything moves.
- The AI's `propose_plan` hits this exact gate; a hallucination cannot widen its privileges.

**Quarantine & Journal (L2)** — *make removal crash-safe and reversible.* (owns I3, I4)
- Move-to-quarantine, never `rm`; **write-ahead journal** records intent before acting.
- Same-FS = atomic rename; cross-FS = copy→verify→delete, per-file journaled.
- TTL purge is when blocks are truly freed; every step idempotent.

**Storage & Cache (L0)** — *durable local state, no server.*
- SQLite (WAL) for scan-cache, project registry, op-log, preferences.
- Per-op quarantine manifest + journal as append-only files (recoverable even if DB is lost).

**AI Agent (L4)** — *intent → Plan, with zero destructive power.* (owns I7, I8)
- Provider-agnostic (BYOK Claude / Ollama); tools are **read-only + propose_plan** — no
  `delete`, no `shell`.
- Grounded on engine facts; only **path/size/tier/git-state** ever leave the machine.

**Interfaces (L5)** — *thin adapters, no business logic.*
- CLI (`typer`) + rendering (`rich`); TUI (`textual`, later). All logic stays in L2/L3.

---

## 7. Workflow diagrams

### 7.1 Scan
```
  reclaim scan
      │
      ▼
  seed work queue with target roots (~/dev, known cache dirs)
      │
      ▼
  worker pool walks dirs in parallel (os.scandir, cached stat)
      │
      ├── entry is symlink? ─────────────▶ skip
      ├── dir is a reclaimable unit? ─────▶ sum size as one blob, DON'T descend ─▶ Candidate
      ├── dir otherwise? ─────────────────▶ push to queue
      └── file? ──────────────────────────▶ account (dedup by dev/ino, allocated size)
      │
      ▼
  merge per-worker totals ─▶ persist incremental cache ─▶ inventory ready
```

### 7.2 Classify — the safety lattice
```
                     entry (dir / file)
                           │
             ┌─────────────▼──────────────┐
             │ matches a PROTECT rule?     │── yes ─▶ 🔴 IRREPLACEABLE
             │ .env · data/ · *.sqlite ·   │          (top precedence)
             │ uncommitted git · user pin  │
             └─────────────┬──────────────┘
                           │ no
             ┌─────────────▼──────────────┐
             │ matches a reclaimable rule  │── no ──▶ 🔴 IRREPLACEABLE
             │ AND its context holds?      │          (fail-safe default, I2)
             │ e.g. target/ + Cargo.toml   │
             └─────────────┬──────────────┘
                           │ yes
             ┌─────────────▼──────────────┐
             │ regeneration cost?          │
             └──────┬───────────────┬──────┘
              cheap │               │ costly
                    ▼               ▼
             🟢 REGENERABLE   🟡 REGENERABLE-COSTLY
                    └───────┬───────┘
                            ▼
                 confidence < threshold? ── yes ─▶ escalate to user / AI
```

### 7.3 Reclaim / apply — through the Safety Gate
```
  User/AI      Planner      Safety Gate        Journal        Quarantine     FS
    │  goal ─────▶│             │                 │              │           │
    │             │─ Plan ─────▶│                 │              │           │
    │             │             │─ re-check git & existence (TOCTOU) ──────▶ │
    │             │             │◀─ still safe ────────────────────────────  │
    │◀─ confirm? ─┼─────────────│                 │              │           │
    │  yes ───────┼────────────▶│                 │              │           │
    │             │             │─ write intent ─▶│ PLANNED      │           │
    │             │             │                 │─ MOVING ─────┼── move ──▶ │
    │             │             │                 │◀─ done ──────┤           │
    │             │             │                 │ COMMITTED    │           │
    │◀── freed X GB · undoable for N days ────────│              │           │
```

### 7.4 Journal state machine
```
  PLANNED ──▶ PREPARING ──▶ MOVING ──▶ COMMITTED ──(TTL expires)──▶ PURGED
     │            │            │            │
     │            │            │            └──(undo)──▶ reverse-replay ──▶ RESTORED
     │            │            └──(crash)──▶ roll back partial moves ────▶ ABORTED
     └────────────┴──(crash before any move)──▶ nothing moved (safe) ───▶ ABORTED
```

### 7.5 Crash recovery (runs at every startup)
```
  startup
     │
     ▼
  any journal not in {COMMITTED, PURGED, ABORTED}?
     │ yes
     ▼
  state == MOVING? ── yes ─▶ reverse the moves recorded so far ─▶ ABORTED
     │ no  (PLANNED / PREPARING)
     ▼
  nothing was moved ─▶ mark ABORTED        (idempotent — safe to re-run anytime)
```

### 7.6 Undo
```
  reclaim undo <op-id>
     │
     ▼
  load manifest (original paths + checksums)
     │
     ▼
  for each item:  original path now occupied? ── yes ─▶ warn + skip (never clobber)
     │ no
     ▼
  move item back from quarantine ─▶ verify checksum ─▶ mark RESTORED
```

### 7.7 AI grounding loop
```
  user intent  ("free ~20G but skip anything I'm working on")
       │
       ▼
  ┌────────────────────────────────────────────────────────────┐
  │ AI AGENT                                                    │
  │  1. call READ-ONLY tools ──▶ engine facts                   │◀─ list_reclaimable
  │  2. reason over FACTS (not the model's own memory)          │◀─ get_project_facts
  │  3. build a Plan + plain-English "why each item is safe"    │
  │  4. propose_plan(...) ──────────────────────────────────────┼─▶ SAFETY GATE (same as CLI)
  └────────────────────────────────────────────────────────────┘
       │
       ▼
  human confirms ─▶ gate re-validates ─▶ quarantine
  guarantees: no delete tool · no shell · only minimal facts leave the machine
```

### 7.8 Incremental scan cache
```
  visit dir D
     │
     ▼
  cache has D? ── no ─▶ walk D fully, store (mtime, aggregate size, child signature)
     │ yes
     ▼
  D.mtime unchanged AND child signature matches?
     │ yes                          │ no
     ▼                              ▼
  reuse cached aggregate        re-walk D, update cache
  (skip this subtree)
     [ `--fresh` forces a full re-walk regardless ]
```

---

## 8. Concurrency model

```
    ┌──────────────── thread-safe work queue (directories) ────────────────┐
    │   [ D1  D2  D3  … ]              outstanding-task counter: N          │
    └───▲────────────────┬────────────────────┬────────────────────▲───────┘
        │ push subdirs    │ pop                │ pop                 │ push
   ┌────┴────┐       ┌────▼────┐          ┌────▼────┐          ┌─────┴───┐
   │ worker1 │  ···  │ workerk │   ···    │ workerm │   ···    │ workern │   pool = min(32, cpu×4)
   └────┬────┘       └────┬────┘          └────┬────┘          └────┬────┘
        │  scandir + stat (GIL released during syscalls)            │
        ▼                                                           ▼
   per-worker accumulators (running sizes · hardlink seen-set) ── merged at end
```

Points:
- **Threads, not async/processes** — I/O-bound syscalls release the GIL; async FS is a
  thread pool in disguise; processes pay pickling cost.
- **Over-subscribe cores** (`cpu×4`) because workers spend most time waiting on I/O.
- **Correct termination** via an `outstanding` counter + condition variable — avoids the
  classic "empty queue ⇒ done" race (queue can be briefly empty mid-production).
- **No global lock** on the hot path — per-worker accumulators merged once at the end.
- **Bounded concurrency** caps open file descriptors on huge trees.

---

## 9. Performance strategy

Why Reclaim is faster than `du` and disk analyzers.

| Technique | Effect | Others? |
|-----------|--------|---------|
| `os.scandir` cached `stat` | ~1 syscall/entry instead of 2 | some analyzers |
| **Opaque-blob pruning of reclaimable units** | skips building metadata for the millions of files inside `node_modules`/caches | **nobody** — the core edge |
| Threaded parallel walk (I/O-bound) | multiplies throughput on the syscall-bound workload | `dust`/`dua` (Rust) |
| O(1) basename pre-classification | deep work happens only where it matters | — |
| Incremental cache / watcher index | near-instant re-scans | — |
| Streaming top-N via a heap | report without a global sort | — |

**Target:** cold scan **≥ 3× faster than `du -sh`** on a typical dev home dir, with correct
allocated-size accounting. If the interpreter ever becomes the bottleneck (profiled, not
assumed), the hot walk moves to a **Rust extension (PyO3)** across the §5 data contract.

---

## 10. Key design decisions (ADRs)

Each with the rejected alternative and the reason.

| ID | Decision | Chosen | Rejected | Why |
|----|----------|--------|----------|-----|
| **AD1** | Language | Python 3.12 | Rust/Go first | Ship the whole product in a stack we can *explain*; scanning is I/O-bound; portability boundary keeps a Rust swap open. |
| **AD2** | Scanner concurrency | Threads | asyncio / multiprocessing | I/O syscalls release the GIL; async FS isn't real; processes pay pickling. |
| **AD3** | Removal semantics | Quarantine + TTL | direct `rm` | Reversibility is the entire trust story (I3/I4). |
| **AD4** | Crash safety | Write-ahead journal | best-effort deletes | Deterministic recovery, borrowed from DB durability. |
| **AD5** | AI capability | Propose-only, no shell/delete tool | agent runs commands | A hallucination must be *incapable* of data loss (I5). |
| **AD6** | Safety re-check | At apply time (TOCTOU) | check only at plan time | State drifts between planning and applying (I6). |
| **AD7** | Unknown files | Default irreplaceable | default deletable | False-keep is cheap; false-delete is catastrophic (I2). |
| **AD8** | Cross-platform | One codebase + `platform/` layer | per-OS builds | Differences are small and isolatable; 3-OS CI verifies. |
| **AD9** | Storage | SQLite + append-only journals | a service / flat files only | ACID, local, zero-setup; journals survive DB loss. |
| **AD10** | Plan selection | Greedy (safety, then size) | optimal knapsack | Explainable, O(n log n), matches user intuition. |
| **AD11** | Scan speed edge | Opaque-blob pruning | walk everything like `du` | Don't build metadata for files we treat as one blob. |

---

## 11. Package & directory layout

The `platform/` seam and the pure-library `core/` are the two boundaries that keep
AD1 and AD8 real.

```
reclaim/
├── ARCHITECTURE.md            ← this file
├── README.md                  ← project narrative / usage
├── pyproject.toml             ← pipx-installable; entry point → cli
├── docs/                      ← product docs (00–07)
├── src/reclaim/
│   ├── core/                  ← L2 engine (pure library; no UI/network/OS policy)
│   │   scanner · classifier · rules · project · planner · quarantine · journal · model
│   ├── safety/                ← L3 Safety Gate
│   ├── platform/              ← L1 abstraction (base · posix · windows)
│   ├── storage/               ← L0 SQLite + incremental cache
│   ├── ai/                    ← L4 agent (loop · tools · providers: claude/ollama)
│   └── cli/                   ← L5 interface (typer app · rich render)
├── tests/                     ← fixtures + scanner/classifier/journal/gate suites
└── .github/workflows/ci.yml   ← 3-OS matrix (mac · linux · windows)
```

---

## 12. Build phases

Each phase ships something demoable on its own. Detail in
[`docs/06-roadmap.md`](./docs/06-roadmap.md).

```
  PHASE 0 ──────▶ PHASE 1 ──────────▶ PHASE 2 ──────────▶ PHASE 3
  Spike &         Deterministic       AI agent layer      Ambient product
  foundations     engine (MVP)        (the differentiator)(polish & wow)

  parallel        scanner·classifier  grounded chat       daemon (watcher)
  scanner vs du   project·planner     BYOK + Ollama       TUI dashboard
  module seams    quarantine·journal  propose-only tools  user rules/config
  3-OS CI         safety gate·undo    explanations        pipx release + GIF
  ─────────────   ─────────────────   ─────────────────   ─────────────────
  "beats du"      "safe reversible    "free 20G but       "warns before the
                   reclaim, AI off"     skip my WIP"        disk fills"
```

> **Sequencing principle:** depth-first on **speed + safety**, not breadth-first on
> features. One rock-solid, fast, reversible reclaim beats ten shaky cleaners.

---

*Living document — update the ADR table (§10) whenever a load-bearing decision changes.*
