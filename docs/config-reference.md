# Config reference — `config.toml`

Reclaim reads an **optional** TOML config that *extends* its built-in rules. You never need
it — the engine ships with sensible defaults and works with no config at all. The config lets
you teach Reclaim about **custom reclaimable units** (build dirs it doesn't know yet),
**custom protections** (paths it must never touch), and **[watch settings](#watch-settings)**
(defaults for the `reclaim watch` disk monitor), tuned to your own machine.

- **Location:** `$RECLAIM_HOME/config.toml` — by default `~/.reclaim/config.toml`
  (`%USERPROFILE%\.reclaim\config.toml` on Windows).
- **Format:** [TOML](https://toml.io). Read with stdlib `tomllib` on Python 3.11+, or the
  `tomli` backport on 3.10 (installed automatically).

## Quick start

```console
$ reclaim config --init      # writes a commented starter file (won't overwrite an existing one)
$ reclaim config             # show what Reclaim parsed from it (and any warnings)
```

Then edit the file and re-run `reclaim config` to confirm it reads back the way you expect.

## What it can do

### Custom reclaimable units

A **unit** is a directory basename Reclaim may treat as regenerable clutter (like the built-in
`node_modules`, `.venv`, `target`). Add your own with a rebuild command and a base tier:

```toml
[[units]]
name  = "_build"          # directory basename to recognize (e.g. Elixir/Erlang)
regen = "mix compile"     # how you'd rebuild it — shown so you know the cost of removing it
tier  = "yellow"          # "green" = cheap to rebuild · "yellow" = costly. Default: "green"

[[units]]
name  = ".gradle-cache"
regen = "gradle build"
tier  = "green"
label = "gradle cache"    # optional human label; defaults to name
```

| Field   | Required | Meaning |
|---------|----------|---------|
| `name`  | ✅ | Directory basename to recognize as a reclaimable unit. |
| `regen` | ✅ | The command that rebuilds it (alias: `regen_command`). |
| `tier`  |   | `green` (cheap) or `yellow` (costly). Default `green`. |
| `label` |   | Display label. Defaults to `name`. |

A custom unit behaves exactly like a built-in one: the scanner treats it as one opaque blob
(fast), and the classifier still applies the full **safety lattice** — so a custom unit inside
a git repo with uncommitted or unpushed work is *still* protected (🔴), never reclaimed.

### Custom protections

Names and file globs that are **always** irreplaceable (🔴) and must never be reclaimed. These
extend the built-in secret/data protections (`.env`, `*.pem`, `*.sqlite`, `data/`, …).

```toml
[protect]
dirs  = ["research-data", "recordings"]   # directory basenames to always protect
files = ["*.pcap", "*.hdf5"]              # file-basename globs to always protect
```

`dirs` matches a directory by its basename anywhere it appears; `files` matches a file by its
basename against a glob. (For protecting one **specific path** — "never touch `~/work/**`" —
use `reclaim protect <glob>` instead; that's the [preference memory](./05-ai-agent-design.md),
a separate, path-specific mechanism.)

### Watch settings

Defaults for the `reclaim watch` disk monitor (Phase 3c). Every field is optional, and any CLI
flag (`--min-free`, `--interval`, `--warn-growth`, or a path argument) overrides the config.

```toml
[watch]
roots    = ["~/dev", "~/work"]   # paths to watch (default: your home)
min_free = "10G"                 # warn when free space drops below this ("10G" or "10%")
interval = "6h"                  # how often to check when running `reclaim watch`
growth   = "2G"                  # warn if reclaimable clutter grows by this since the last check
```

| Field | Meaning |
|-------|---------|
| `roots` | Directories to watch. Defaults to your home directory. |
| `min_free` | Low-space threshold — an absolute size (`10G`) or a percentage of the volume (`10%`). Below it warns; below half of it is critical. |
| `interval` | Check cadence for the foreground `reclaim watch` loop (e.g. `6h`, `30m`). Ignored for `--once`. |
| `growth` | Warn when reclaimable clutter grows by at least this much between checks. Omit to disable growth alerts. |

## Safety guarantees

Config can only ever **add protection**, never remove it. Two invariants hold by construction:

1. **Protections always win (I2).** If a name appears as both a unit and a protection — yours
   or a built-in — the protection wins and the unit is dropped (with a warning). You can't make
   `data/` reclaimable by declaring it a unit, and protecting `target` removes the built-in
   `target` unit.
2. **Enforced everywhere, including at apply time.** The config-derived rules flow through
   `scan → classify → Safety Gate`. A protection you add blocks removal not just when planning
   but again at the moment of removal (the same TOCTOU re-check that guards git state, I6).

## Fail-safe parsing

A broken config never crashes Reclaim and never silently changes behavior — every problem
becomes a visible **warning** (printed on stderr during a scan, or listed by `reclaim config`):

| Situation | Result |
|-----------|--------|
| No config file | Built-in rules only (the common case — not an error). |
| Invalid TOML / unreadable file | Ignored **+ warning**; built-ins used. |
| A `[[units]]` entry missing `name` or `regen`, or with an unknown `tier` | That entry skipped **+ warning**; the rest load. |
| A unit whose `name` is a protected directory | Unit dropped **+ warning** (protection kept). |
| A duplicate unit `name` | First kept **+ warning**. |
| A non-string item in `dirs`/`files` | That item skipped **+ warning**. |

## Full example

```toml
# ~/.reclaim/config.toml

[[units]]
name  = "_build"
regen = "mix compile"
tier  = "yellow"

[[units]]
name  = ".terraform"
regen = "terraform init"
tier  = "yellow"

[protect]
dirs  = ["research-data", "client-recordings"]
files = ["*.pcap", "*.parquet"]
```

```console
$ reclaim config
config file: /Users/you/.reclaim/config.toml (found)

Custom reclaimable units
name         tier       rebuild
_build       🟡 yellow  mix compile
.terraform   🟡 yellow  terraform init

Custom protections (always 🔴)
  dirs:  client-recordings, research-data
  files: *.pcap, *.parquet
```
