# 08 · Benchmarks & Results (living log)

A running, honest record of **measured** results and test coverage as the project is built.
Kept concrete and quantified so it can feed the README, and resume bullets, later.

**Test machine:** Apple M1 · 8 cores · 8 GB RAM · macOS 15.7.3 · Python 3.12.5 · SSD (APFS).
_Warm cache unless stated. Times are best-of-3 full-process wall time (`/usr/bin/time -p`)._

---

## Phase 0 — Scanner spike (deterministic, read-only)

### Test suite
`pytest` — **5/5 passing** (`tests/test_scanner.py`). What they lock in:

| Test | Guarantees |
|------|------------|
| `finds_and_labels_reclaimable_units` | Recognizes & labels `node_modules`, `.venv` with regen commands |
| `reclaimable_is_subset_of_total` | Reclaimable bytes ⊆ total bytes (accounting sanity) |
| `pruning_does_not_descend_into_blob` | Opaque-blob pruning: nested units don't double-count |
| `symlinks_are_not_followed` | No cycles / no double counting via symlinks |
| `context_sensitive_units_off_by_default` | `dist/ build/ out/` not flagged unless opted in (safety) |

### Scanner benchmark vs `du`
Target scanned: `~/Desktop/PROJECTS` — **290,654 files across 8,962 dirs**, 7.34 GB.
_(best-of-3, warm cache; 2026-07-06 re-measure that closes the Phase 0 exit criteria.)_

| Tool | Wall time | Speedup |
|------|-----------|---------|
| `du -sk` (single-threaded C) | 5.15 s | baseline |
| **reclaim scanner** (32 threads) | **3.27 s** | **≈1.57× faster** |

- **Byte-exact:** both report **7,877,898,240 bytes** allocated — a **0-byte difference**
  from `du` on 290K real files. Validates block accounting (`st_blocks × 512`), hard-link
  dedup, and symlink handling end-to-end.
- **Hard-link dedup (controlled test):** a file + a hard link to it + one independent file →
  scanner counts the shared inode **once** (307,200 B), matching `du` exactly.
- **Worker sweep:** plateaus at ~cpu×4 (32) threads on this 8-core machine.
- **Reclaimable found:** **4.38 GB** across **4,764 units** — top: `node_modules` 1.83 GB (8),
  `.venv` 1.62 GB (7), `__pycache__` 530 MB (4,740 dirs), `.next` 420 MB (2).

### Key engineering insight (the honest one)
Warm-cache scanning is **GIL-bound**: once syscall latency is hidden (data in RAM), the
per-entry Python work serializes, so 8 cores yield ~1.4×, not ~8×. The parallelism edge is
expected to **widen on cold cache / high-latency filesystems**, where there's real I/O
latency to hide. The path to the ≥3× target is the **Rust hot-path (PyO3)** — now
empirically justified, not assumed (ADR AD1).

### Correctness / safety notes
- Scanner is **100% read-only** — no `rm`/`unlink`/`rmtree`/`shutil.move`/`subprocess`
  anywhere in `src/` (verified by grep). It only calls `os.scandir` / `os.stat`.
- Cross-platform (macOS/Linux/Windows) via the `platform/` abstraction; CI runs a 3-OS matrix.

### Resume-bullet drafts (quantified — refine later)
- *Built a multithreaded filesystem scanner in Python (`os.scandir` + thread pool) that
  scans 290K files / 7.34 GB **~1.57× faster than `du`** with **byte-exact** on-disk
  accounting (0-byte diff from `du`: block sizes, hard-link dedup, symlink-safe).*
- *Designed "opaque-blob pruning" — recognizing reclaimable units (`node_modules`, `.venv`)
  and summing them without building per-file metadata — keeping memory flat across millions
  of files.*
- *Profiled the warm-cache bottleneck to the **GIL**, defining a clean data-contract
  boundary so the hot path can move to a Rust (PyO3) extension without a rewrite.*

---

<!-- Phase 1+ results will be appended below as they land. -->
