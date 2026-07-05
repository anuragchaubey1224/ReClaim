# 03 · Product Design

This is the soul of the product: the mental model, the classification engine, the
safety guarantees, the core user flow, and the feature set (with a realistic MVP line).

---

## 1. The core philosophy: "Reclaim, don't delete"

We never frame this as *deletion*. We frame it as **reclaiming space that the machine
can rebuild**. This isn't just marketing — it's the design constraint that makes every
other decision safe:

> **Invariant:** Reclaim only ever removes files it can name a reproduction path for
> (a command, a rebuild, a re-pull). If it can't prove regenerability, it doesn't touch it.

This flips the emotional model from *"am I about to lose something?"* to *"I'm freeing
space I can always get back."*

---

## 2. The classification engine (the brain)

Every candidate on disk is sorted into one of three tiers. This is deterministic and
rule-driven — the AI never overrides it downward toward "less safe".

### Tier 🟢 Regenerable — safe to reclaim
Rebuildable by a known, cheap command.
- `node_modules/` → `npm install` / `pnpm install` / `yarn`
- `.venv/`, `venv/`, `env/` → `pip install -r requirements.txt` / `poetry install`
- `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- Build outputs: `target/` (Rust), `build/`, `dist/`, `.next/`, `.nuxt/`, `out/`, `.svelte-kit/`
- Package-manager caches: `~/.cache/pip`, `~/.npm`, `~/.gradle/caches`, `~/.cargo/registry/cache`

### Tier 🟡 Regenerable-but-costly — reclaim with a nudge
Rebuildable, but re-acquiring it costs real time / bandwidth / money.
- Docker images & dangling layers (re-pull can be gigabytes)
- HuggingFace / model caches (`~/.cache/huggingface`), datasets
- Conda environments, large ML wheels
- Xcode `DerivedData`, simulator caches

These are shown with an explicit "this will take a while to rebuild" warning.

### Tier 🔴 Irreplaceable — never touch
Not rebuildable, or contains work / secrets / data.
- Source code, config, `.env` and any secret-bearing file
- **Uncommitted or unpushed git changes** (detected via git status)
- Local databases (`*.sqlite`, `*.db`, `data/`, `dumps/`)
- Anything the user has explicitly protected (allowlist / pinned paths)
- Anything Reclaim cannot confidently classify → **defaults to this tier** (fail-safe)

> **Fail-safe default:** unknown ⇒ irreplaceable. Ambiguity always resolves toward safety.

### What "understanding a project" means
For each detected project root, Reclaim computes a small fact sheet:
```
project: ~/dev/old-scraper
  type: python (poetry)
  git: clean, last commit 4 months ago, pushed
  activity: last file access 118 days ago  → DORMANT
  reclaimable: .venv (1.1 GB), __pycache__ (40 MB)
  protected:   data/ (contains .sqlite), .env
```
Activity + git-state turn a blunt rule ("delete .venv") into a *judgement*
("this dormant, clean, pushed project's .venv is very safe to reclaim").

---

## 3. The safety model (why users will trust it)

Trust is the entire moat. Four layers:

1. **Dry-run by default.** Nothing is ever removed without an explicit confirmation of a
   concrete plan. `--yes` exists but is never the default.
2. **Quarantine, not delete.** "Reclaiming" moves items into a managed quarantine store
   with a TTL (e.g. 7 days). Space is freed immediately from the user's perspective, but
   the data is recoverable until the TTL expires and it's purged.
3. **One-command undo.** `reclaim undo <id>` restores the last (or a chosen) operation
   from quarantine. Every operation has a durable, inspectable log.
4. **Hard protections that cannot be overridden by the AI.** Irreplaceable-tier rules,
   the user's allowlist, and the "uncommitted git changes" check are enforced in the
   deterministic engine *below* the AI. The AI can propose; it can never bypass a guard.

> Design rule: **the AI is a planner, not an executor with root.** Every action it
> proposes passes back through the same deterministic safety gate a human command would.

---

## 4. The core user flow

```
$ reclaim scan
  → fast concurrent scan of ~/dev (and known cache dirs)
  → builds project fact sheets + classifies everything

$ reclaim status
  ┌─ Reclaimable: 34.2 GB across 41 projects ─────────────┐
  │ 🟢 Regenerable        28.1 GB   (safe)                 │
  │ 🟡 Regenerable-costly  6.1 GB   (Docker, model caches) │
  │ 🔴 Protected           —        (2 projects w/ WIP)    │
  └───────────────────────────────────────────────────────┘

$ reclaim chat            # the AI agent
  You: free up around 20 gigs but don't touch anything I'm working on
  Reclaim: Here's a plan reclaiming 21.3 GB from 6 dormant projects…
           [shows plan] [explains why each is safe] [flags 0 risks]
           Apply? Everything is undoable for 7 days.

$ reclaim apply <plan-id>     # quarantines, frees space, logs op
$ reclaim undo  <op-id>       # restores from quarantine
```

Two surfaces, one engine:
- **Deterministic CLI** (`scan`, `status`, `apply`, `undo`) — works with zero AI.
- **AI chat** (`reclaim chat`) — natural-language planning on top of the same engine.

---

## 5. Feature set

### MVP (build this first — it must be genuinely good on its own)
- [ ] **Concurrent scanner** — fast parallel filesystem walk over target roots.
- [ ] **Rule-based classifier** — the three-tier model for the common artifacts above.
- [ ] **Project detection** — find project roots, detect type, read git state + activity.
- [ ] **`status` view** — grouped, sorted, human-readable reclaimable report.
- [ ] **Quarantine + undo** — safe move-to-trash with TTL and restore.
- [ ] **Plan → confirm → apply** loop in the CLI.

> The MVP must be a great tool **even with the AI turned off.** If it isn't, the AI is
> lipstick. This is the anti-"AI wrapper" discipline.

### V2 — the AI agent layer
- [ ] **`reclaim chat`** — natural-language goals → concrete, grounded plans.
- [ ] **Explanations** — "why is this safe?", "what happens if I remove this?"
- [ ] **Preference learning** — "never touch `~/work/**`" remembered across sessions.

### V3 — the ambient product
- [ ] **Background daemon** — watches disk growth, warns *before* you hit the wall.
- [ ] **Trends** — "your Docker usage grew 12 GB this month."
- [ ] **TUI dashboard** — a live, navigable view.
- [ ] **Config/rules file** — declarative custom classification + protections.

---

## 6. Explicit non-goals (scope discipline)

- No system files, OS caches, browser data, mail, photos.
- No "optimize your Mac" snake-oil.
- No cloud sync of your file listing in the MVP (privacy: all local).
- No auto-delete without confirmation, ever, in any version.
