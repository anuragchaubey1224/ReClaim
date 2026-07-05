"""Windows platform.

Windows has no `st_blocks`, so allocated size is approximated by rounding the apparent
size up to the filesystem cluster size. Phase 0 uses the common 4 KiB NTFS cluster; a
later phase can query the real per-volume value via GetDiskFreeSpaceW.
"""

from __future__ import annotations

import os

_DEFAULT_CLUSTER = 4096


class WindowsPlatform:
    name = "windows"

    def block_size(self, st: os.stat_result) -> int:
        size = st.st_size
        # round up to the next cluster boundary
        return ((size + _DEFAULT_CLUSTER - 1) // _DEFAULT_CLUSTER) * _DEFAULT_CLUSTER

    def hardlink_key(self, st: os.stat_result) -> tuple[int, int] | None:
        # Modern CPython populates st_ino / st_dev on Windows for NTFS.
        if getattr(st, "st_nlink", 1) > 1:
            return (st.st_dev, st.st_ino)
        return None
