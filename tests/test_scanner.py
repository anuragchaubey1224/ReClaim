"""Correctness tests for the Phase 0 scanner.

These encode the load-bearing behaviors: reclaimable units are found and sized, opaque-blob
pruning does not descend, symlinks are never followed, and reclaimable ⊆ total.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from reclaim.core.scanner import Scanner


def _write(p: Path, size: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def test_finds_and_labels_reclaimable_units(tmp_path: Path) -> None:
    _write(tmp_path / "proj" / "src" / "main.py", 100)
    _write(tmp_path / "proj" / "node_modules" / "a" / "index.js", 2000)
    _write(tmp_path / "proj" / "node_modules" / "b" / "index.js", 3000)
    _write(tmp_path / "proj" / ".venv" / "lib" / "pkg.py", 5000)

    res = Scanner(workers=4).scan(tmp_path)

    kinds = {c.kind for c in res.candidates}
    assert "node_modules" in kinds
    assert ".venv" in kinds

    nm = next(c for c in res.candidates if c.kind == "node_modules")
    assert nm.file_count == 2                     # both packages summed as one blob
    assert nm.regen_command == "npm install"


def test_reclaimable_is_subset_of_total(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "main.py", 1000)
    _write(tmp_path / "node_modules" / "x.js", 4000)
    res = Scanner(workers=2).scan(tmp_path)
    assert res.reclaimable_allocated <= res.total_allocated
    assert res.reclaimable_allocated > 0


def test_pruning_does_not_descend_into_blob(tmp_path: Path) -> None:
    # A reclaimable dir nested inside node_modules must NOT become its own candidate,
    # because the scanner stops descending once it recognizes the outer blob.
    _write(tmp_path / "node_modules" / ".cache" / "x", 100)
    _write(tmp_path / "node_modules" / "pkg" / "__pycache__" / "y.pyc", 100)

    res = Scanner(workers=2).scan(tmp_path)

    assert len(res.candidates) == 1
    assert res.candidates[0].kind == "node_modules"
    assert res.candidates[0].file_count == 2


@pytest.mark.skipif(sys.platform == "win32",
                    reason="symlink creation may require privileges on Windows")
def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    _write(tmp_path / "real" / "f", 1000)
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)

    res = Scanner(workers=2).scan(tmp_path)

    assert res.file_count == 1                     # 'real/f' once; symlink skipped


def test_context_sensitive_units_off_by_default(tmp_path: Path) -> None:
    _write(tmp_path / "build" / "out.o", 1000)

    off = Scanner(workers=1).scan(tmp_path)
    assert not any(c.kind == "build" for c in off.candidates)

    on = Scanner(workers=1, include_context_sensitive=True).scan(tmp_path)
    assert any(c.kind == "build" for c in on.candidates)
