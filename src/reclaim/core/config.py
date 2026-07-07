"""User configuration (L2) — declarative extensions to the built-in ruleset (Phase 3a).

An *optional* TOML file at `$RECLAIM_HOME/config.toml`. Reclaim runs fully without it; a
missing or broken config degrades to the built-in rules, never to unsafe behavior. The file
can only do two things, both pure data folded into a `Ruleset` (see `core/rules.py`):

  * add custom **reclaimable units** — directory basenames Reclaim may treat as regenerable,
    each with a rebuild command and a base tier ("green" cheap / "yellow" costly);
  * add custom **protections** — directory basenames and file-basename globs that are always
    🔴 and must never be reclaimed.

Config is parsed once at CLI startup and the resulting `Ruleset` is threaded through
scan → classify → gate, so a user rule is enforced everywhere the built-ins are, including the
apply-time Safety Gate (defense in depth).

Fail-safe by design:
  * a missing file → empty config (built-ins only);
  * invalid TOML or an unreadable file → empty config **+ a warning** (never a crash);
  * a malformed entry (missing name/command, unknown tier, wrong type) → that entry is
    skipped with a warning, the rest still load;
  * a custom unit whose name is a protected directory (built-in or user) is dropped with a
    warning — protections always win (I2), so config can never unlock a guarded name.

TOML is read with stdlib `tomllib` (3.11+), falling back to the `tomli` backport on 3.10.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI leg
    import tomli as tomllib

from reclaim.core.model import Tier
from reclaim.core.rules import PROTECT_DIR_NAMES, UnitRule

# Accepted spellings for a unit's base tier in the config file → canonical Tier. Only the two
# reclaimable tiers are configurable; 🔴 is never a *unit* tier (it comes from protections).
_TIER_ALIASES: dict[str, Tier] = {
    "green": Tier.REGENERABLE,
    "regenerable": Tier.REGENERABLE,
    "cheap": Tier.REGENERABLE,
    "yellow": Tier.REGENERABLE_COSTLY,
    "costly": Tier.REGENERABLE_COSTLY,
    "regenerable_costly": Tier.REGENERABLE_COSTLY,
}


@dataclass(frozen=True, slots=True)
class WatchConfig:
    """Persistent settings for the `reclaim watch` monitor (Phase 3c), all optional.

    `None` means "unset" so a CLI flag can override the config; the CLI supplies the defaults."""

    roots: tuple[str, ...] = ()
    min_free: str | None = None       # "10G" or "10%"
    interval: str | None = None       # "6h", "30m"
    growth: str | None = None         # "2G": warn if reclaimable grows by this since last check

    @property
    def is_empty(self) -> bool:
        return not (self.roots or self.min_free or self.interval or self.growth)


@dataclass(frozen=True, slots=True)
class ReclaimConfig:
    """Parsed user config: custom units + protections + watch settings, plus parse warnings.

    `warnings` are human-readable strings the CLI surfaces so a typo is visible rather than
    silently ignored. An empty config (no file, or a file with nothing usable) is the common
    case and is not an error."""

    units: tuple[tuple[str, UnitRule], ...] = ()      # (basename, rule), first-wins on dupes
    protect_dirs: frozenset[str] = field(default=frozenset())
    protect_file_globs: tuple[str, ...] = ()
    watch: WatchConfig = field(default_factory=WatchConfig)
    warnings: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when nothing that affects the *ruleset* is set (watch settings don't count —
        they don't change classification). Used to short-circuit `build_ruleset`."""
        return not (self.units or self.protect_dirs or self.protect_file_globs)


EMPTY = ReclaimConfig()


def load_config(path: Path) -> ReclaimConfig:
    """Load and validate the config at `path`. Never raises — problems become warnings."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return EMPTY
    except OSError as e:
        return ReclaimConfig(warnings=(f"could not read {path}: {e}",))
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as e:
        return ReclaimConfig(warnings=(f"{path} is not valid TOML: {e}",))
    return _parse(data)


def _parse(data: dict) -> ReclaimConfig:
    warnings: list[str] = []

    protect = data.get("protect", {})
    if not isinstance(protect, dict):
        warnings.append("[protect] must be a table — ignoring it")
        protect = {}
    protect_dirs = _str_list(protect.get("dirs"), "protect.dirs", warnings)
    protect_globs = _str_list(protect.get("files"), "protect.files", warnings)

    # Protections win: a name protected here (or by a built-in) can never be a reclaimable unit.
    protected_names = set(PROTECT_DIR_NAMES) | set(protect_dirs)

    units: list[tuple[str, UnitRule]] = []
    seen: set[str] = set()
    for entry in _table_array(data.get("units"), "units", warnings):
        parsed = _parse_unit(entry, warnings)
        if parsed is None:
            continue
        name, rule = parsed
        if name in protected_names:
            warnings.append(
                f"unit '{name}' is also a protected directory — ignoring the unit "
                "(protections always win)"
            )
            continue
        if name in seen:
            warnings.append(f"duplicate unit '{name}' — keeping the first")
            continue
        seen.add(name)
        units.append((name, rule))

    return ReclaimConfig(
        units=tuple(units),
        protect_dirs=frozenset(protect_dirs),
        protect_file_globs=tuple(protect_globs),
        watch=_parse_watch(data.get("watch"), warnings),
        warnings=tuple(warnings),
    )


def _parse_watch(value: object, warnings: list[str]) -> WatchConfig:
    """Parse the optional `[watch]` table. Unknown/typed-wrong values warn and fall back."""
    if value is None:
        return WatchConfig()
    if not isinstance(value, dict):
        warnings.append("[watch] must be a table — ignoring it")
        return WatchConfig()
    roots = _str_list(value.get("roots"), "watch.roots", warnings)
    return WatchConfig(
        roots=tuple(roots),
        min_free=_opt_str(value.get("min_free"), "watch.min_free", warnings),
        interval=_opt_str(value.get("interval"), "watch.interval", warnings),
        growth=_opt_str(value.get("growth"), "watch.growth", warnings),
    )


def _opt_str(value: object, where: str, warnings: list[str]) -> str | None:
    """A present-and-non-empty string, else None (warning if present but the wrong type)."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    warnings.append(f"{where} must be a non-empty string — ignoring it")
    return None


def _parse_unit(entry: object, warnings: list[str]) -> tuple[str, UnitRule] | None:
    """Validate one `[[units]]` table → (name, UnitRule), or None (with a warning) if bad."""
    if not isinstance(entry, dict):
        warnings.append("each [[units]] entry must be a table — skipping one")
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        warnings.append("a [[units]] entry is missing a non-empty 'name' — skipping it")
        return None
    name = name.strip()
    regen = entry.get("regen", entry.get("regen_command", ""))
    if not isinstance(regen, str) or not regen.strip():
        warnings.append(f"unit '{name}' is missing a 'regen' command — skipping it")
        return None
    tier_raw = entry.get("tier", "green")
    if not isinstance(tier_raw, str) or tier_raw.strip().lower() not in _TIER_ALIASES:
        warnings.append(
            f"unit '{name}' has an unknown tier {tier_raw!r} "
            "(use 'green' or 'yellow') — skipping it"
        )
        return None
    tier = _TIER_ALIASES[tier_raw.strip().lower()]
    label = entry.get("label")
    label = label.strip() if isinstance(label, str) and label.strip() else name
    return name, UnitRule(label=label, regen_command=regen.strip(), tier=tier)


def _table_array(value: object, where: str, warnings: list[str]) -> list:
    """Coerce a TOML array-of-tables into a list; warn on the wrong shape."""
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"[{where}] must be an array of tables — ignoring it")
        return []
    return value


def _str_list(value: object, where: str, warnings: list[str]) -> list[str]:
    """Coerce a TOML value into a list of non-empty strings; warn on bad shapes/items."""
    if value is None:
        return []
    if isinstance(value, str):          # tolerate a bare string for a single entry
        value = [value]
    if not isinstance(value, list):
        warnings.append(f"{where} must be a list of strings — ignoring it")
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        else:
            warnings.append(f"{where} contains a non-string entry {item!r} — skipping it")
    return out


def build_ruleset(config: ReclaimConfig, base=None):
    """Fold a parsed config into a `Ruleset` (built-ins by default). Pure, no I/O."""
    from reclaim.core.rules import DEFAULT_RULESET

    base = base if base is not None else DEFAULT_RULESET
    if config.is_empty:
        return base
    return base.extended(
        units=config.units,
        protect_dirs=config.protect_dirs,
        protect_file_globs=config.protect_file_globs,
    )
