# Reclaim

> **AI-guided disk-reclamation engine for developers.**
> Local-first · cross-platform · reversible · faster than `du`.

Reclaim safely finds and removes *regenerable* developer clutter — `node_modules`, `.venv`,
build caches, Docker layers, `__pycache__` — while never touching irreplaceable work, with
full explainability and one-command undo.

> ✅ **Status: Phase 1 complete (the deterministic engine, MVP).** A stranger can safely
> reclaim space with it today, **no AI involved** — scan, classify into 🟢/🟡/🔴 with
> git-state and dormancy, plan against a goal, and reclaim it reversibly. The AI agent
> (`reclaim chat`) is Phase 2.

## Highlights

- **Fast, byte-accurate scanner** — parallel `os.scandir` walk with opaque-blob pruning;
  **0-byte diff vs `du` on 290K files, ~1.57× faster**.
- **Fail-safe 3-tier classifier** — 🟢 regenerable / 🟡 costly / 🔴 protected. Unknown ⇒
  protected. A project with **uncommitted or unpushed git work is never touched**.
- **Reversible, crash-safe removal** — nothing is ever `rm`'d. Items move to a quarantine
  store guarded by a **write-ahead journal**; one-command **undo** restores byte-identically;
  a crash mid-apply is rolled back at next startup.
- **70 tests** encode the safety invariants (never touches 🔴, undo restores exactly, atomic
  rollback, TOCTOU re-check at apply time).

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
# 1. see what's reclaimable (read-only) — tiers + per-project fact sheets
reclaim status ~/dev

# 2. preview a plan for a goal (never mutates)
reclaim plan ~/dev --free 20G

# 3. reclaim it — scan → plan → safety gate → confirm → quarantine (undoable)
reclaim apply ~/dev --free 20G

# 4. inspect / undo / permanently free
reclaim ls
reclaim undo <op-id>        # or just `reclaim undo` for the latest
reclaim purge --older-than 7
```

Useful flags: `--include-costly` (also take 🟡), `--dormant-only`, `--kind node_modules`,
`--min-size 100M`, `--yes` (skip the prompt). The quarantine store lives at `$RECLAIM_HOME`
(default `~/.reclaim`).

## Documentation

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — the full system architecture, workflows, and design decisions.
- [`docs/`](./docs/) — product vision, differentiation, roadmap, benchmarks.

## License

MIT
