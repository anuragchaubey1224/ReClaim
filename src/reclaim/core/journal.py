"""Write-ahead journal (L2) — makes a reclaim transaction crash-safe (owns I4).

An append-only JSONL file, one record per line, **fsync'd on every append** so intent is
durable on disk *before* the corresponding filesystem move happens (classic write-ahead
logging, ARCHITECTURE.md §7.4/AD4). The journal is **authoritative and self-sufficient**:
recovery needs nothing but the journal — it survives loss of any SQLite index (AD9).

Record shapes:
  {"state": "planned",  "ts": …, "op_id": …, "items": [{source,kind,size,files}, …]}
  {"event": "moved",    "ts": …, "index": i, "source": …, "dest": …}
  {"state": "committed","ts": …}
  {"event": "restored", "ts": …, "index": i}
  {"state": "restored" | "aborted" | "purged", "ts": …}

The state machine (§7.4) is enforced by the caller (QuarantineStore); this module just
records transitions durably and reads them back for recovery.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from reclaim.core.model import OpState


class Journal:
    """Durable append-only op-log. One instance per transaction (one file)."""

    def __init__(self, path: Path, *, clock: Callable[[], float] | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or time.time

    # -- writing (each append is durable before returning) ---------------------

    def _append(self, record: dict) -> None:
        line = json.dumps({**record, "ts": self._clock()}, separators=(",", ":"))
        # Open with O_APPEND semantics; flush + fsync so the record hits disk before the
        # move it authorizes. A crash after this line but before the move is recoverable.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass                     # best-effort on filesystems that reject fsync

    def state(self, state: OpState, **extra) -> None:
        self._append({"state": state.value, **extra})

    def event(self, name: str, **extra) -> None:
        self._append({"event": name, **extra})

    # -- reading (recovery + inspection) ---------------------------------------

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # A torn final line (crash mid-write) is ignored — the record it would
                    # have authorized simply never happened, which is the safe reading.
                    continue
        return out

    def last_state(self) -> OpState | None:
        """The most recent state transition, or None if the journal is empty/absent."""
        last: OpState | None = None
        for rec in self.records():
            s = rec.get("state")
            if s is not None:
                last = OpState(s)
        return last

    def events(self, name: str) -> list[dict]:
        return [r for r in self.records() if r.get("event") == name]

    def planned_items(self) -> list[dict]:
        for rec in self.records():
            if rec.get("state") == OpState.PLANNED.value:
                return rec.get("items", [])
        return []
