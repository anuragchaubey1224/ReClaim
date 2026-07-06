"""User preference memory (L2) — persisted protection rules the engine enforces.

The AI's `save_preference` (docs/05) and the CLI's `reclaim protect` both write here; the
classifier and Safety Gate both read here. A preference is a **path glob the user never wants
touched** ("never touch ~/work/**") — it can only ever move a candidate toward 🔴, never make
something reclaimable, so it is monotonically safe to add (even from the AI).

Enforcement is by the deterministic engine, not the model (invariant I5/I7): once saved, a
matching path is hard-protected at scan/classify time *and* re-checked at apply time, whether
or not the AI is involved. The store is a small JSON file under `$RECLAIM_HOME`.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class Preference:
    """One protection rule: a path glob the user never wants reclaimed."""

    pattern: str
    note: str = ""
    created: str = ""

    def matches(self, path: Path | str) -> bool:
        """True if `path` falls under this rule.

        `~` is expanded. A glob (`~/work/**`, `*/secret`) matches via fnmatch — whose `*`
        spans `/`, so `~/work/**` covers the whole subtree. A bare directory (`~/work`) also
        protects itself and everything beneath it."""
        target = str(path)
        pat = os.path.expanduser(self.pattern)
        if fnmatch.fnmatch(target, pat):
            return True
        base = pat.rstrip("/")
        for suffix in ("/**", "/*"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return bool(base) and (target == base or target.startswith(base + os.sep))


class PreferenceStore:
    """Load/add/remove/match protection rules, persisted as JSON.

    `clock` is injectable so `created` timestamps are deterministic in tests. All reads hit
    disk so a rule saved mid-session (or by another process) is seen immediately by the gate
    (defense in depth)."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = Path(path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def all(self) -> tuple[Preference, ...]:
        try:
            raw = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return ()
        return tuple(
            Preference(p["pattern"], p.get("note", ""), p.get("created", ""))
            for p in raw.get("preferences", [])
            if isinstance(p, dict) and p.get("pattern")
        )

    def matches(self, path: Path | str) -> Preference | None:
        """The first saved rule that protects `path`, or None."""
        for pref in self.all():
            if pref.matches(path):
                return pref
        return None

    def add(self, pattern: str, note: str = "") -> Preference:
        """Add (or update the note of) a protection rule and persist. Returns it."""
        pattern = pattern.strip()
        if not pattern:
            raise ValueError("preference pattern cannot be empty")
        pref = Preference(pattern, note.strip(), self._clock().isoformat())
        kept = [p for p in self.all() if p.pattern != pattern]
        self._write([*kept, pref])
        return pref

    def remove(self, pattern: str) -> bool:
        """Remove a rule by exact pattern. Returns True if one was removed."""
        current = self.all()
        kept = [p for p in current if p.pattern != pattern]
        if len(kept) == len(current):
            return False
        self._write(kept)
        return True

    def _write(self, prefs: list[Preference]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"preferences": [
            {"pattern": p.pattern, "note": p.note, "created": p.created} for p in prefs
        ]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, self._path)
