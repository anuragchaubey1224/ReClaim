# Changelog

All notable changes to Reclaim are recorded here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com); versions follow [SemVer](https://semver.org).

## [0.1.2] — 2026-07-10

The first release actually published to PyPI. `0.1.1` was tagged, but its release build failed
on the Windows CI leg, so nothing was uploaded — this release fixes that by scoping the
platform claims to what is genuinely verified.

### Changed

- **Windows is now best-effort, not a supported/CI-gated platform.** The Windows CI leg had
  been failing (the `platform/` layer runs there, but its byte-accounting is approximate and
  untested), while the "3-OS CI" and Windows-parity language implied verification that never
  existed. CI now gates on **macOS + Linux** only; the platform badge, the PyPI OS classifier,
  and the docs are corrected to match. Windows still runs and `docs/windows-testing-guide.md`
  remains, framed as best-effort. Promoting Windows back to a gate is future work, once a
  Windows leg is genuinely green.

## [0.1.1] — 2026-07-10

Tagged but never published: its release build failed on Windows CI (superseded by `0.1.2`).

### Changed

- **The distribution is now named `reclaim-disk`.** `reclaim` and `reclaim-cli` are both taken
  on PyPI by unrelated projects, so `pip install reclaim` would have installed someone else's
  package. The **import package (`import reclaim`) and the console script (`reclaim …`) are
  unchanged** — only the install name moves. The AI extra is `reclaim-disk[ai]`, and the
  provider's "install the SDK" hint was corrected to match; it had been telling users to run a
  command that installs a different project.
- **README links are absolute.** PyPI renders the README outside the repository, so every
  relative `./docs/…` link and the demo GIF would have 404'd on the project page.

### Added

- **Release automation** (`.github/workflows/publish.yml`): publishing a GitHub Release runs
  the macOS + Linux test matrix, builds, `twine check --strict`s, and uploads to PyPI via **Trusted
  Publishing** (OIDC) — no API token is stored in the repository.

## [0.1.0] — 2026-07-07

Tagged, never published to PyPI (superseded by `0.1.1`, which renamed the distribution).
The deterministic engine, the AI agent, and the user config file are all in place; a stranger
can safely reclaim disk space with it today, with one-command undo.

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

### Fixed

- **A mistyped scan target is an error, not an empty report.** `reclaim scan ~/dve` used to
  walk nothing and print "0.0 B reclaimable" — indistinguishable from a clean disk. Scan
  targets are now validated (exit 2 on a missing path or a non-directory). `trends` and
  `history` keep accepting any path: there, it is a key into recorded snapshots, not a tree
  to walk, so a deleted or unmounted root still has a readable trend.
- **`reclaim chat` fails before it scans, not after.** The provider is now built and
  `preflight()`-ed up front, so a missing `anthropic` SDK, a missing API key, or a missing
  `--model` costs you an error in milliseconds instead of a full home-directory walk followed
  by a raw SDK exception mid-REPL. Ollama, needing neither key nor SDK, preflights clean.
- **The install hint no longer eats its own extra.** `pip install "reclaim[ai]"` rendered as
  `pip install "reclaim"` because `rich` parsed `[ai]` as a markup tag — the suggested command
  did not install the AI extra. Error text is escaped before rendering.

### Safety invariants (test-encoded)

Never reclaims 🔴 / uncommitted / unpushed work · `undo` restores byte-identically · atomic
all-or-nothing apply with crash rollback · apply-time re-validation · 243 hermetic tests on a
macOS + Linux × Python 3.10/3.12 CI matrix.

[0.1.2]: https://github.com/anuragchaubey1224/ReClaim/releases/tag/v0.1.2
[0.1.1]: https://github.com/anuragchaubey1224/ReClaim/releases/tag/v0.1.1
[0.1.0]: https://github.com/anuragchaubey1224/ReClaim/releases/tag/v0.1.0
