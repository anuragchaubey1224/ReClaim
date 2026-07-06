"""Parallel filesystem scanner (L2) — the performance core.

Design (ARCHITECTURE.md §6.2, §7.1, §8):
  * Threaded walk. Filesystem syscalls (readdir/stat) release the GIL, so a thread pool
    genuinely parallelizes them. No asyncio (fake async for the filesystem), no
    multiprocessing (pickling cost outweighs the gain).
  * os.scandir. Reuses the dir entry's type from readdir, avoiding a stat just to learn
    file-vs-dir, and caches the stat once fetched.
  * Opaque-blob pruning. The moment a directory is a known reclaimable unit (node_modules,
    .venv, target, …) we sum it as ONE blob and stop descending — we never build per-file
    records or classify the millions of tiny files inside it. This keeps memory flat and
    is a core part of the speed story.
  * Correct byte accounting. Allocated size (blocks), hard links counted once, symlinks
    never followed (no cycles, no double counting).
  * Lock-free hot path. Each worker accumulates into its own struct; results are merged
    exactly once at the end.

Concurrency & termination use a `queue.Queue`: its internal unfinished-task counter IS the
"outstanding work" signal. `queue.join()` blocks until every directory has been processed,
which sidesteps the classic "empty queue == done" race (the queue can be momentarily empty
while a worker is still about to enqueue subdirectories).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from queue import Queue

from reclaim.core.model import Candidate, ScanResult
from reclaim.core.rules import is_reclaimable_unit
from reclaim.platform import base as platform_base

_SENTINEL = None  # pushed after join() to release idle workers


class _Acc:
    """Per-worker accumulator. No lock is touched while walking."""

    __slots__ = ("allocated", "apparent", "files", "dirs", "errors",
                 "hardlinks", "candidates")

    def __init__(self) -> None:
        self.allocated = 0
        self.apparent = 0
        self.files = 0
        self.dirs = 0
        self.errors = 0
        # (dev, ino) -> allocated bytes, for files with nlink > 1. Deduped at merge time
        # so a hard-linked file seen by two different workers is only counted once.
        self.hardlinks: dict[tuple[int, int], int] = {}
        self.candidates: list[Candidate] = []


class Scanner:
    def __init__(
        self,
        platform: platform_base.Platform | None = None,
        workers: int | None = None,
        include_context_sensitive: bool = False,
    ) -> None:
        self.platform = platform or platform_base.detect()
        cpu = os.cpu_count() or 4
        # I/O-bound: oversubscribe cores because workers spend most time in syscalls.
        self.workers = workers or min(32, cpu * 4)
        self.include_context_sensitive = include_context_sensitive

    def scan(self, *roots: os.PathLike[str] | str) -> ScanResult:
        root_paths = tuple(Path(r).expanduser() for r in roots)
        queue: Queue = Queue()
        for r in root_paths:
            queue.put(str(r))

        accs = [_Acc() for _ in range(self.workers)]
        start = time.perf_counter()

        threads = [
            threading.Thread(target=self._worker, args=(queue, accs[i]), daemon=True)
            for i in range(self.workers)
        ]
        for t in threads:
            t.start()

        queue.join()                 # wait until every directory has been processed
        for _ in threads:
            queue.put(_SENTINEL)     # release the idle workers
        for t in threads:
            t.join()

        elapsed = time.perf_counter() - start
        return self._merge(root_paths, accs, elapsed)

    # -- worker loop -----------------------------------------------------------

    def _worker(self, queue: Queue, acc: _Acc) -> None:
        while True:
            path = queue.get()
            if path is _SENTINEL:
                break                # no task_done: join() has already returned
            try:
                self._walk_dir(path, queue, acc)
            except OSError:
                acc.errors += 1
            finally:
                queue.task_done()

    def _walk_dir(self, path: str, queue: Queue, acc: _Acc) -> None:
        acc.dirs += 1
        # Directories occupy blocks too; count them so our total matches `du`.
        try:
            dst = os.stat(path, follow_symlinks=False)
            acc.allocated += self.platform.block_size(dst)
            acc.apparent += dst.st_size
        except OSError:
            acc.errors += 1
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue     # never follow: avoids cycles + double counting
                    if entry.is_dir(follow_symlinks=False):
                        rule = is_reclaimable_unit(
                            entry.name, self.include_context_sensitive
                        )
                        if rule is not None:
                            self._absorb_blob(entry.path, rule, acc)
                        else:
                            queue.put(entry.path)
                    else:
                        self._account_file(entry.stat(follow_symlinks=False), acc)
                except OSError:
                    acc.errors += 1

    def _account_file(self, st: os.stat_result, acc: _Acc) -> None:
        acc.files += 1
        acc.apparent += st.st_size
        alloc = self.platform.block_size(st)
        key = self.platform.hardlink_key(st)
        if key is None:
            acc.allocated += alloc               # common case: no dedup needed
        else:
            acc.hardlinks[key] = alloc           # dedup at merge time

    def _absorb_blob(self, root: str, rule, acc: _Acc) -> None:
        """Sum a reclaimable unit as one opaque blob: no per-file records, no descent
        into it for classification. Hard links are deduped within the blob."""
        alloc = apparent = files = 0
        seen: set[tuple[int, int]] = set()
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                dst = os.stat(d, follow_symlinks=False)
                alloc += self.platform.block_size(dst)
                apparent += dst.st_size
            except OSError:
                acc.errors += 1
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            else:
                                st = entry.stat(follow_symlinks=False)
                                files += 1
                                apparent += st.st_size
                                a = self.platform.block_size(st)
                                key = self.platform.hardlink_key(st)
                                if key is None:
                                    alloc += a
                                elif key not in seen:
                                    seen.add(key)
                                    alloc += a
                        except OSError:
                            acc.errors += 1
            except OSError:
                acc.errors += 1

        acc.allocated += alloc
        acc.apparent += apparent
        acc.files += files
        acc.candidates.append(
            Candidate(
                path=Path(root),
                kind=rule.label,
                size_allocated=alloc,
                size_apparent=apparent,
                file_count=files,
                tier=rule.tier,             # provisional base tier; classifier refines it
                regen_command=rule.regen_command,
            )
        )

    # -- merge -----------------------------------------------------------------

    def _merge(
        self, roots: tuple[Path, ...], accs: list[_Acc], elapsed: float
    ) -> ScanResult:
        allocated = sum(a.allocated for a in accs)
        apparent = sum(a.apparent for a in accs)
        files = sum(a.files for a in accs)
        dirs = sum(a.dirs for a in accs)
        errors = sum(a.errors for a in accs)

        candidates: list[Candidate] = []
        for a in accs:
            candidates.extend(a.candidates)

        # Merge hard-linked inodes across all workers so each is counted exactly once.
        merged_links: dict[tuple[int, int], int] = {}
        for a in accs:
            merged_links.update(a.hardlinks)
        allocated += sum(merged_links.values())

        return ScanResult(
            roots=roots,
            total_allocated=allocated,
            total_apparent=apparent,
            file_count=files,
            dir_count=dirs,
            error_count=errors,
            elapsed_seconds=elapsed,
            candidates=tuple(candidates),
        )


# --------------------------------------------------------------------------- #
# Dependency-free runner:  python -m reclaim.core.scanner <path> [-w N]
# Lets us benchmark against `du` without installing typer/rich.
# --------------------------------------------------------------------------- #

def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Reclaim scanner (Phase 0 spike)")
    p.add_argument("path", nargs="?", default="~")
    p.add_argument("-w", "--workers", type=int, default=None)
    p.add_argument("--context-sensitive", action="store_true",
                   help="also flag dist/ build/ out/ (may include real user dirs)")
    args = p.parse_args(argv)

    scanner = Scanner(workers=args.workers,
                      include_context_sensitive=args.context_sensitive)
    res = scanner.scan(args.path)

    print(f"scanned   {res.file_count:,} files in {res.dir_count:,} dirs "
          f"({res.error_count} skipped) in {res.elapsed_seconds:.2f}s "
          f"[{scanner.workers} workers · {scanner.platform.name}]")
    print(f"on disk   {_human(res.total_allocated)} allocated "
          f"({_human(res.total_apparent)} apparent)")
    print(f"reclaim   {_human(res.reclaimable_allocated)} across "
          f"{len(res.candidates)} units\n")

    by_kind = sorted(res.by_kind().items(), key=lambda kv: kv[1][0], reverse=True)
    for kind, (size, count) in by_kind:
        print(f"  {_human(size):>10}  {kind}  ({count})")

    print("\ntop units:")
    for c in res.top(10):
        print(f"  {_human(c.size_allocated):>10}  {c.path}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
