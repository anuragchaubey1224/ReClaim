# Reclaim

> **AI-guided disk-reclamation engine for developers.**
> Local-first · cross-platform · reversible · faster than `du`.

[![CI](https://github.com/anuragchaubey1224/ReClaim/actions/workflows/ci.yml/badge.svg)](https://github.com/anuragchaubey1224/ReClaim/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platforms](https://img.shields.io/badge/platform-macOS%20%C2%B7%20Linux%20%C2%B7%20Windows-lightgrey)
![Tests](https://img.shields.io/badge/tests-219%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

Reclaim safely finds and removes *regenerable* developer clutter — `node_modules`, `.venv`,
build caches, `target/`, `__pycache__`, `.next` — while **never touching irreplaceable work**,
with full explainability and **one-command undo**.

> **The wedge:** every byte we remove is regenerable. We never delete your work — we reclaim
> only what your machine can rebuild. Nothing is ever `rm`'d; it's moved to a journaled
> quarantine you can undo byte-for-byte.

---

## Demo

A walkthrough of the core loop on a throwaway sandbox (`status → plan → apply → undo`):

```console
$ reclaim status ~/dev
scanned 4 files in 5 dirs (0 skipped) in 0.01s · 32 workers · posix

Reclaimable: 9.6 MB of 9.6 MB on disk · 0 project(s)

🟢 Regenerable         9.6 MB  3 unit(s)
🟡 Regenerable-costly   0.0 B  0 unit(s)
🔴 Protected                —  0 project(s) with uncommitted/unpushed work

$ reclaim plan ~/dev --include-costly
Plan — reclaim 9.6 MB across 3 unit(s), 3 files

  size  unit              path                    rebuild
4.4 MB  🟢 node_modules   /…/webapp/node_modules  npm install
3.1 MB  🟢 .venv          /…/api/.venv            pip install -r requirements.txt
2.1 MB  🟢 target (rust)  /…/rustcli/target       cargo build

$ reclaim apply ~/dev --include-costly --yes
✓ reclaimed 9.6 MB across 3 unit(s) — op 20260707-144439-443c92
undo anytime with reclaim undo 20260707-144439-443c92

$ reclaim undo
✓ restored 3 unit(s) op 20260707-144439-443c92
```

An animated version is generated reproducibly from a checked-in script — see
[`demo/`](./demo/) (`vhs demo/reclaim.tape`).

## Install

Reclaim is a single CLI. The recommended install is [`pipx`](https://pipx.pypa.io) (isolated,
on your PATH):

```bash
# from the published repo
pipx install "git+https://github.com/anuragchaubey1224/ReClaim"

# or from a local clone
pipx install .
```

With [`uv`](https://docs.astral.sh/uv/): `uv tool install .` · Or plain `pip install .`.

**Optional AI co-pilot** (`reclaim chat`) — the engine works fully without it:

```bash
pipx install "reclaim[ai] @ git+https://github.com/anuragchaubey1224/ReClaim"
```

<details>
<summary>From source (for development)</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                       # 219 tests
```
</details>

## Quickstart

```bash
# 1. see what's reclaimable (read-only) — tiers + per-project fact sheets
reclaim status ~/dev

# 2. preview a plan for a goal (never mutates)
reclaim plan ~/dev --free 20G

# 3. reclaim it — scan → plan → safety gate → confirm → quarantine (undoable)
reclaim apply ~/dev --free 20G

# 4. inspect / undo / permanently free
reclaim ls
reclaim undo <op-id>          # or just `reclaim undo` for the latest
reclaim purge --older-than 7  # the only true delete, past a TTL
```

Useful flags: `--include-costly` (also take 🟡), `--dormant-only`, `--kind node_modules`,
`--min-size 100M`, `--yes` (skip the prompt). The quarantine store lives at `$RECLAIM_HOME`
(default `~/.reclaim`).

## Commands

| Command | What it does |
|---------|--------------|
| `reclaim scan` / `status` | Read-only inventory: reclaimable space by tier (+ per-project fact sheets). |
| `reclaim plan` | Preview the plan for a goal. Never mutates. |
| `reclaim apply` | Reclaim: scan → plan → **Safety Gate** → confirm → quarantine. Undoable. |
| `reclaim undo [op]` | Restore a quarantined operation (latest by default), byte-identically. |
| `reclaim ls` / `purge` | Inspect the quarantine store / permanently free items past a TTL. |
| `reclaim chat` | Grounded AI agent — plan & explain cleanups in natural language (BYOK). |
| `reclaim trends` / `history` | How reclaimable clutter changed over time, per kind (from your scans). |
| `reclaim watch` | Background monitor — warn before you run out of disk; `--once` for cron/launchd. |
| `reclaim protect` / `unprotect` / `prefs` | Never-touch path rules, enforced by the engine. |
| `reclaim config` / `config --init` | Custom reclaimable units + protections ([config ref](./docs/config-reference.md)). |

## What makes it different

- **Fast, byte-accurate scanner.** Parallel `os.scandir` walk with opaque-blob pruning:
  **0-byte diff vs `du` on 290K files, ~1.57× faster** — with hard-link dedup and symlink
  safety. It sums a `node_modules` as one blob instead of walking its million tiny files, so
  memory stays flat. ([benchmarks](./docs/08-benchmarks-and-results.md))
- **Fail-safe classification.** A 3-tier safety lattice — 🟢 regenerable / 🟡 costly / 🔴
  protected. **Unknown ⇒ protected.** A project with **uncommitted or unpushed git work is
  never touched**; secrets, databases, and data dirs are hard-protected.
- **Reversible, crash-safe removal.** Nothing is ever `rm`'d. Items move to a quarantine store
  guarded by a **write-ahead journal**; `undo` restores **byte-identically**; a crash
  mid-apply is rolled back at next startup; cross-filesystem moves copy-verify-delete.
- **A grounded AI agent — not a chat wrapper.** The model can only *read facts* and *propose*
  a plan; every removal still passes the same deterministic Safety Gate and your confirmation.
  It is architecturally incapable of an unsafe action. **Bring your own provider** — Claude
  (default), OpenRouter, OpenAI, any OpenAI-compatible endpoint, or a fully-local Ollama.
- **Yours to tune.** An optional `~/.reclaim/config.toml` teaches Reclaim custom reclaimable
  units and custom protections — and protections always win.

## How it works

```
scan ──▶ classify ──▶ plan ──▶ Safety Gate ──▶ quarantine (journaled) ──▶ undo / purge
 │           │          │          │                    │
 os.scandir  3-tier    goal→      re-checks git &    write-ahead journal;
 + pruning   lattice   ranked     protections at     crash-recovered at
             (I2)      units      apply time (I6)    startup; byte-exact undo
```

Every mutation funnels through one **Safety Gate** that re-reads the world at apply time
(TOCTOU defense) — a stale plan or a hallucinating agent cannot widen its privileges past it.
The load-bearing safety invariants are **test-encoded**:

- never reclaims 🔴 / uncommitted / unpushed work;
- `undo` restores byte-identically;
- apply is atomic — a mid-apply failure rolls back fully, a crash is repaired at startup;
- unknown ⇒ protected;
- user protections win over everything and are re-checked at apply time.

**219 hermetic tests** (injected clock/id-gen/git/LLM-client; isolated store) run on a
**3-OS × Python 3.10/3.12** CI matrix.

## Documentation

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — the full system architecture, workflows, and the
  load-bearing design decisions.
- [`docs/`](./docs/) — [vision](./docs/01-vision.md) · [differentiation](./docs/02-differentiation.md)
  · [product design](./docs/03-product-design.md) · [technical depth](./docs/04-technical-depth.md)
  · [AI agent design](./docs/05-ai-agent-design.md) · [roadmap](./docs/06-roadmap.md)
  · [benchmarks](./docs/08-benchmarks-and-results.md).
- [`docs/config-reference.md`](./docs/config-reference.md) — the optional config file.
- [`docs/windows-testing-guide.md`](./docs/windows-testing-guide.md) — step-by-step for Windows.
- [`CHANGELOG.md`](./CHANGELOG.md).

## Status

The deterministic engine (Phases 0–1), the grounded AI agent (Phase 2), and most of the ambient
layer — **config file, trends/history, and the disk watcher** (Phases 3a/3b/3c) — all ship
today. Last on the roadmap: a TUI dashboard. See the [roadmap](./docs/06-roadmap.md).

## License

[MIT](./LICENSE) © 2026 Anurag Chaubey
