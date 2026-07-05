# 04 · Technical Depth & Architecture — The Resume Gold

> This is the document that gets you hired. The AI is the hook; **this** is the substance.
> Every section below maps a product feature to a real CS concept you can go deep on in
> an interview. Build these well and you have 30 minutes of genuine technical story.

---

## 1. Architecture at a glance

```
                         ┌──────────────────────────────┐
   reclaim chat  ───────▶│      AI Agent Layer          │  (plans, explains — grounded)
                         │  LLM + tool-calling + memory │
                         └───────────────┬──────────────┘
                                         │ proposes actions (never executes directly)
                         ┌───────────────▼──────────────┐
   reclaim CLI  ────────▶│      Safety Gate             │  (deterministic guards, allowlist,
   (scan/status/         │  irreplaceable-tier enforce  │   git-WIP check, dry-run)
    apply/undo)          └───────────────┬──────────────┘
                                         │ approved ops
        ┌────────────────┬───────────────┼───────────────┬────────────────┐
        ▼                ▼               ▼               ▼                ▼
   ┌─────────┐    ┌────────────┐   ┌───────────┐   ┌───────────┐   ┌────────────┐
   │ Scanner │    │ Classifier │   │  Project  │   │ Quarantine│   │  Op Log /  │
   │(parallel│    │ (3-tier    │   │  Analyzer │   │  Store    │   │  Journal   │
   │  walk)  │    │  engine)   │   │(git,type, │   │(move+TTL, │   │(durable,   │
   │         │    │            │   │ activity) │   │  undo)    │   │ auditable) │
   └─────────┘    └────────────┘   └───────────┘   └───────────┘   └────────────┘
        │                                                                 │
        └──────────────────── incremental scan cache ────────────────────┘
```

Everything below the Safety Gate is deterministic and testable without an LLM.

---

## 2. Feature → CS concept map (interview talking points)

| Product feature | Underlying CS depth you can discuss |
|-----------------|-------------------------------------|
| Fast concurrent scanner | Parallelism, thread pools / async I/O, **work-stealing** over a directory tree, bounded concurrency, backpressure |
| "Top N largest" report | **Heaps / priority queues**, streaming selection without sorting everything |
| Path matching for rules | **Tries / prefix trees**, glob compilation, radix trees |
| Correct size accounting | OS internals: **inodes, hard links, symlinks, sparse files, apparent vs. allocated size, APFS clones/dedup** |
| Incremental re-scan | **mtime-based invalidation**, Merkle-ish subtree hashing, cache design (like a build system) |
| Quarantine + undo | **Transactions, atomicity, idempotency, rollback**, write-ahead journaling |
| "What regenerates what" | **Dependency graphs**, topological reasoning about rebuild steps |
| Never delete WIP | **git plumbing** (`status --porcelain`, ahead/behind), integrating an external state machine |
| Background daemon | **Long-running processes, IPC, filesystem watchers (FSEvents/inotify), scheduling** |
| AI grounded planning | **LLM tool-use / function calling, guardrails, prompt-grounding on deterministic facts** |

Pick any row in an interview and you can talk for ten minutes. That's the goal.

---

## 3. The scanner — the systems showpiece

The naive version is a recursive `os.walk`. The *impressive* version:

- **Parallel directory walk** with a bounded worker pool. Directories are units of work
  pushed onto a shared queue; workers pop, `stat` entries, enqueue subdirectories.
  Discuss **work-stealing** to keep workers busy on unbalanced trees (one giant
  `node_modules` shouldn't starve the others).
- **Bounded concurrency + backpressure** so we don't exhaust file descriptors on a tree
  with millions of files.
- **Correct byte accounting.** Talk about the difference between "apparent size" and
  "blocks allocated", **hard links counted once** (track seen inodes in a set), symlinks
  not followed to avoid cycles and double counting, sparse files.
- **Early pruning.** Once a directory is classified as a reclaimable unit
  (`node_modules`), don't descend into it — sum its size and stop. This is where most of
  the speed comes from, and it's a nice algorithmic point.
- **Incremental scans.** Cache per-directory `(mtime, size, hash-of-children)`. On
  re-scan, skip subtrees whose mtime is unchanged — the same idea as an incremental
  build system's dirty-tracking. This is a strong, senior-sounding design detail.

**Benchmark story for the resume:** "scans a 2M-file, 300GB home dir in X seconds,
Y× faster than `du`, by parallelizing the walk and pruning reclaimable subtrees."

---

## 4. The classifier — a small, testable rules engine

- A declarative ruleset (data, not code) mapping path patterns + project context → tier
  + reproduction command. Compiled into a matcher (trie/glob) for speed.
- **Fail-safe evaluation order:** protections and irreplaceable rules are checked first
  and win. Unknown ⇒ irreplaceable. This ordering is itself a design talking point
  (safety as a lattice / priority of rules).
- Extensible via a user config file (V3) without touching engine code — a clean
  separation of policy from mechanism.

---

## 5. Project analyzer — integrating external state

- **Root detection:** walk up from files to find markers (`.git`, `package.json`,
  `Cargo.toml`, `pyproject.toml`, `go.mod`).
- **Git state:** shell out to `git status --porcelain`, `git rev-list @{u}..` (ahead)
  to detect uncommitted / unpushed work. Any dirty repo ⇒ hard protect. Good story about
  integrating a foreign state machine safely and defensively (what if there's no
  upstream? detached HEAD? submodules?).
- **Activity signal:** last access / modify time across the project → dormant vs. hot.
  Discuss `atime` unreliability (many systems mount `noatime`) and falling back to mtime
  — a nice "real-world messiness" detail.

---

## 6. Quarantine + undo — transactions on a filesystem

This is where you show you think like a systems engineer, not a scripter.

- **Never `rm` on apply.** Move the target into a quarantine store
  (`~/.reclaim/quarantine/<op-id>/…`) preserving enough metadata to restore it exactly.
- **Atomicity:** an operation either fully quarantines or rolls back. Use a
  **write-ahead journal**: record intent → perform moves → mark complete. On crash,
  replay/rollback from the journal. (Classic DB durability applied to files.)
- **Same-filesystem moves are O(1) renames**; cross-filesystem needs copy+verify+delete —
  handle both, and discuss the tradeoff.
- **TTL purge:** a background sweep permanently removes quarantined items past their TTL,
  actually reclaiming the blocks.
- **Undo** = reverse-replay the journal for an op-id.

Interview gold: *"how do you make deleting files reversible and crash-safe?"* — you have
a real, considered answer (journal + quarantine + atomic moves + rollback).

---

## 7. Tech stack (decision + rationale)

**Decision: Python for v1.** Chosen deliberately, not as a fallback — see the reasoning
in §7.1. The engine is architected so its hot path can later move to a compiled language
without a full rewrite (§7.2). It targets **macOS + Linux + Windows from day 1** with one
codebase; OS differences live in a `platform/` module — see
[`07-form-factor-and-cost.md`](./07-form-factor-and-cost.md).

| Concern | Choice | Why |
|---------|--------|-----|
| Core engine | **Python 3.12+** | The stack the author knows deeply enough to *explain*, which is the actual interview asset. Fast to ship the full product (engine + AI + UX), not just a scanner. |
| Scanner | `os.scandir` + `concurrent.futures.ThreadPoolExecutor` | Scanning is **I/O-bound**, so the GIL isn't the bottleneck — syscalls release it. `scandir` reuses `dirent` data to avoid extra `stat()` calls. Gets us near-`du` speed in pure Python. |
| AI layer | **Claude API** (tool-use / function calling) | Grounded agentic planning; see [`05-ai-agent-design.md`](./05-ai-agent-design.md). Keep it a thin, well-guarded layer over the engine. Python has first-class SDK support. |
| CLI / TUI | `typer` (CLI), `rich` + `textual` (TUI) | Production-grade dev-tool UX with minimal effort. |
| Storage | SQLite (`sqlite3`, stdlib) + flat journal files | Op-log, incremental-scan cache, quarantine index. |
| Packaging | `pipx` install, `pyproject.toml` | Single-command install for a portfolio tool. |

### 7.1 Why Python is the *right* call here (not a compromise)
- **A finished product beats an abandoned one.** A complete, polished tool the author
  fully understands outscores a half-built Rust scanner in every interview.
- **The interview value is the design, not the language.** Concurrency model, safety
  invariants, and AI grounding are language-agnostic. Being able to *explain* them deeply
  is the signal — and that requires a stack you own.
- **"Python is slow" barely applies to I/O-bound scanning.** The bottleneck is disk +
  syscalls, not the interpreter. This nuance is itself a strong interview talking point.
- **The AI layer is easiest and richest in Python.** Fast iteration on the part that
  differentiates the product.

**Interview-ready answer to "why Python for a systems tool?"**
> "Scanning is I/O-bound, so the interpreter isn't the bottleneck — I profiled it.
> `os.scandir` + threads release the GIL on syscalls, so it's comparable to `du`. And I
> kept the engine modular behind a clean boundary, so if profiling ever demanded it, only
> the hot path would move to a Rust extension (PyO3) — without touching the rest."

### 7.2 The portability boundary (keeping the rewrite door open)
The right way to "rewrite in Rust/Go later" is **not** a full rewrite (a classic trap —
second-system syndrome). It's a *targeted* swap of the hot path, made cheap by
architecting for it now:

> Keep the **engine a pure library** with a stable data contract (fact sheets +
> classification records) between it and the interface (CLI/TUI) and the AI layer. If the
> boundary is clean, the language behind it is swappable.

Concretely, module boundaries to respect from day one:
`scanner  ·  classifier  ·  project-analyzer  ·  quarantine  ·  safety-gate  ·  ai`

With that separation, a future performance need becomes a weekend swap of `scanner` (e.g.
a Rust extension via **PyO3 / maturin**), or — if it becomes a startup — a full engine
port that reuses the same architecture. Design for the boundary; don't pay for the rewrite
until profiling proves you need it.

---

## 8. What to be able to whiteboard

If you can stand at a whiteboard and design *any two* of these, the project has done its
job:
1. A parallel, crash-safe filesystem scanner with correct byte accounting.
2. A reversible delete: quarantine + write-ahead journal + rollback.
3. An incremental re-scan cache (dirty-subtree tracking).
4. A grounded LLM agent that physically *cannot* execute an unsafe action.
