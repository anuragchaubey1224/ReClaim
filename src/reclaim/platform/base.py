"""Platform abstraction interface (L1).

Only the byte-accounting + hardlink primitives the scanner needs live here for Phase 0.
Cache-dir profiles, long-path handling, and file-watchers arrive with later phases
(ARCHITECTURE.md §6.1). Every OS difference is isolated behind this Protocol so nothing
else in the codebase branches on the operating system.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class Platform(Protocol):
    name: str

    def block_size(self, st: os.stat_result) -> int:
        """Bytes actually occupied on disk for this entry — what reclaiming frees."""
        ...

    def hardlink_key(self, st: os.stat_result) -> tuple[int, int] | None:
        """A (dev, ino) key if this file may be hard-linked (nlink > 1), else None.

        Returning None for the common nlink == 1 case lets the scanner skip dedup
        bookkeeping on the hot path.
        """
        ...


def detect() -> Platform:
    """Return the Platform implementation for the current OS."""
    if os.name == "nt":
        from reclaim.platform.windows import WindowsPlatform

        return WindowsPlatform()
    from reclaim.platform.posix import PosixPlatform

    return PosixPlatform()
