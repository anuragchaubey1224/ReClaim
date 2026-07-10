# 07 · Form Factor, Platforms & Cost

Three product-shape decisions, now locked. This is what the user actually installs, where
it runs, and what it costs to operate.

---

## 1. Form factor — an installable CLI (with a library engine inside)

**Decision:** Reclaim ships as an **installable command-line tool**, not a desktop GUI app.

```bash
pipx install reclaim-disk # one-command install (the name `reclaim` was taken on PyPI)
reclaim scan              # use it
reclaim chat              # the AI interface
```

**Why CLI, not a desktop app:** the target user is a developer who lives in the terminal.
A CLI is the native, expected, trusted form (like `git`, `docker`, `kondo`, `npkill`). A
desktop GUI (Electron/Tauri) would add heavy packaging complexity, weaken the systems
story, and land where developers don't want this tool anyway.

### It's a CLI *and* a library — by design
| Layer | What it is | User-facing? |
|-------|------------|--------------|
| **Engine** | A clean, importable Python **library/package** (`scanner`, `classifier`, `quarantine`, `safety-gate`…) | No — internal architecture |
| **Product** | The **CLI** that wraps the engine | **Yes** — this is what you install |
| **TUI** _(optional, Phase 3)_ | Interactive terminal dashboard via `textual` / `rich` | Optional nice-to-have |

Keeping the engine a standalone library is what makes the **portability boundary**
(see [`04-technical-depth.md`](./04-technical-depth.md) §7.2) real: the CLI, a future TUI,
or even a future GUI are all just thin front-ends over the same engine.

---

## 2. Platforms — macOS + Linux + Windows, from day 1

**Decision:** cross-platform from the start, **one shared Python codebase** — no separate
stack per OS.

The bulk of the logic is OS-agnostic. The handful of real differences are isolated in a
single **`platform/` abstraction module**, so the rest of the engine never branches on OS:

| Concern | macOS / Linux | Windows | How it's handled |
|---------|---------------|---------|------------------|
| Path separators | `/` | `\` | `pathlib` — automatic ✅ |
| Cache/artifact locations | `~/.cache/pip`, `~/Library/Caches`, `~/.npm` | `%LocalAppData%\pip\Cache`, `%AppData%\npm-cache` | Per-OS **platform profiles** (data, not code) |
| Hard-link dedup | `st_ino` / `st_dev` reliable | NTFS file-index, needs care | OS-specific handling in `platform/` |
| Long paths | fine | 260-char default limit | `\\?\` prefix / graceful error handling |
| Case sensitivity | case-sensitive (Linux), preserving (mac) | case-insensitive | Normalize in path matching |
| Cross-device moves (quarantine) | rename within FS = O(1) | across `C:`/`D:` = copy+verify+delete | Already in the design; detect same-FS vs cross-FS |
| File watcher (daemon, Phase 3) | FSEvents / inotify | ReadDirectoryChangesW | `watchdog` library abstracts all three |

> Note: Reclaim uses its **own quarantine store**, not the OS trash/Recycle Bin, so
> trash semantics don't need per-OS integration — a nice simplification.

### Testing discipline (the one honest caveat)
Architecture supporting three platforms is cheap; *verifying* them is the real cost.
Rule: **mark a platform "supported" only after it's actually tested there.**

**GitHub Actions CI carries this.** Public repos get macOS, Windows, and Linux runners at no
cost. Per the rule above, the CI **gate** is the matrix we've actually made green — **macOS +
Linux** × Python 3.10/3.12. Windows runs (the `platform/` layer supports it) but its
byte-accounting is approximate and unverified, so it's **best-effort, not a gate**; promoting
it to a supported OS is future work once a Windows leg is genuinely green.

```yaml
# .github/workflows/ci.yml (sketch)
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest]   # Windows is best-effort, not a gate
```

**Development order:** build and test on macOS first (the author's machine), keep the
`platform/` seam clean, and let CI catch Linux regressions from day 1.

---

## 3. Cost model — ₹0 to operate

**Decision:** the tool is 100% local. There is nothing to deploy and no server to run.

| Cost type | Amount | Why |
|-----------|--------|-----|
| Hosting / deployment | **₹0** | Nothing to deploy — it's a downloadable CLI. No backend. |
| Distribution | **₹0** | PyPI + `pipx`, and GitHub — both free |
| AI inference | **₹0 to the author** | BYOK or local model — see below |

### The AI layer: BYOK + Ollama fallback
The only component that *could* cost money is the LLM call in `reclaim chat`. Both
supported paths cost the author nothing:

| Mode | Who pays | Author cost | Notes |
|------|----------|-------------|-------|
| **BYOK** (default) — user supplies their own Claude API key | User | **₹0** | Standard for open-source AI dev tools. No backend, no liability. |
| **Ollama** (local fallback) — run a local model | Nobody (local) | **₹0** | Fully offline, maximum privacy; quality is lower but fine for planning/explaining. |

A proxy through the author's own key (author pays, needs a tiny backend) is explicitly
**out of scope** — it's the only thing that would introduce a cost and a server.

### Privacy (a genuine selling point)
Because everything runs locally:
- The file listing **never leaves the machine**.
- The AI receives only **minimal, abstracted facts** (path, size, tier, git-state) — never
  file contents. This ties to the grounding design in
  [`05-ai-agent-design.md`](./05-ai-agent-design.md).
- With Ollama, **nothing leaves the machine at all** — a true offline mode.

> Resume framing: "local-first, zero-backend, BYOK — no hosting cost and no user data
> leaves the device."

---

## Locked decisions summary
- **Form:** installable **CLI** over a reusable **library engine**; optional TUI later. Not a desktop app.
- **Platforms:** **macOS + Linux** CI-verified (one codebase, differences isolated in `platform/`); develop macOS-first. Windows is best-effort/experimental — it runs but is not a CI gate.
- **Cost:** **₹0** hosting/deploy; AI via **BYOK (default) + Ollama (local fallback)**; no backend, local-first, privacy-preserving.
