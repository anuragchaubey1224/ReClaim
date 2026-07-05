"""POSIX platform (macOS + Linux)."""

from __future__ import annotations

import os


class PosixPlatform:
    name = "posix"

    def block_size(self, st: os.stat_result) -> int:
        # st_blocks is in 512-byte units (POSIX). This reflects true on-disk allocation:
        # block rounding, sparse files, and filesystem compression all come out correct —
        # matching what `du` reports and what actually frees when removed.
        blocks = getattr(st, "st_blocks", None)
        if blocks is not None:
            return blocks * 512
        return st.st_size  # defensive; should not happen on POSIX

    def hardlink_key(self, st: os.stat_result) -> tuple[int, int] | None:
        if st.st_nlink > 1:
            return (st.st_dev, st.st_ino)
        return None
