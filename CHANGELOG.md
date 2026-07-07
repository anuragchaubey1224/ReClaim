# Changelog

All notable changes to Reclaim are recorded here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com); versions follow [SemVer](https://semver.org).

## [0.1.0] — 2026-07-07

First packaged release. The deterministic engine, the AI agent, and the user config file are
all in place; a stranger can safely reclaim disk space with it today, with one-command undo.

### Added

- **Phase 0 — parallel scanner.** Threaded `os.scandir` walk with opaque-blob pruning;
  byte-exact vs `du` (0-byte diff on 290K files) and ~1.57× faster. Hard-link dedup,
  symlink-safe, block-accurate sizing.
- **Phase 1 — the reversible engine (MVP).**
  - Fail-safe 3-tier classifier (🟢 regenerable / 🟡 costly / 🔴 protected); unknown ⇒
    protected; a project with uncommitted or unpushed git work is never touched.
  - Reversible removal: nothing is ever `rm`'d — items move to a quarantine store guarded by
    a write-ahead journal; `undo` restores byte-identically; a crash mid-apply is rolled back
    at next startup; cross-filesystem moves copy-verify-delete.
  - Safety Gate: every removal re-validates at apply time (TOCTOU defense).
  - CLI: `scan` · `status` · `plan` · `apply` · `undo` · `ls` · `purge`.
- **Phase 2 — the grounded AI agent.** `reclaim chat`: the model can only read facts and
  *propose* a plan; every removal still passes the Safety Gate and your confirmation.
  Preference memory (`reclaim protect`) enforced by the engine, not the model. Bring your own
  provider — Claude (default), OpenRouter, OpenAI, any OpenAI-compatible endpoint, or a
  fully-local Ollama. The engine works fully with the agent absent.
- **Phase 3a — user config file.** Optional `~/.reclaim/config.toml`: custom reclaimable units
  and custom protections, folded into a `Ruleset` threaded through scan → classify → gate.
  Protections always win; broken config degrades to the built-ins with a warning. New
  `reclaim config` / `reclaim config --init`.
- **Phase 3b — trends & history.** Every read-only scan appends a compact snapshot to
  `~/.reclaim/history.jsonl` (append-only, keyed by scanned root, fail-safe). `reclaim trends
  [--since 7d|2w|3m]` reports the per-kind change in reclaimable clutter since a baseline;
  `reclaim history` lists the raw snapshots. Opt out with `RECLAIM_NO_HISTORY`.
- **Phase 3c — disk watcher.** `reclaim watch` monitors free space and reclaimable-clutter
  growth per root and warns *before* the wall — with the amount you'd get back and the command
  to do it. `--once` (for cron/launchd/Task Scheduler) or a foreground loop; native desktop
  notifications with a `watch.log` fallback; thresholds via flags or a `[watch]` config section.
- **Phase 3d — live dashboard.** `reclaim dashboard` — a one-screen view composing reclaimable
  space by tier, a disk bar, top units, projects (git/dormancy), and the trend. One-shot, or
  `--refresh 5s` for a full-screen live loop. Built on `rich` alone (no heavyweight TUI dep).
- **Phase 3e — packaging.** `pipx`-installable, MIT `LICENSE`, packaging metadata, a
  reproducible terminal demo, and a real README.

### Safety invariants (test-encoded)

Never reclaims 🔴 / uncommitted / unpushed work · `undo` restores byte-identically · atomic
all-or-nothing apply with crash rollback · apply-time re-validation · 229 hermetic tests on a
3-OS × Python 3.10/3.12 CI matrix.

[0.1.0]: https://github.com/anuragchaubey1224/ReClaim/releases/tag/v0.1.0
