"""Quarantine + journal safety-invariant tests — the load-bearing guarantees for Phase 1b.

These prove the trust story: reclaiming is a reversible, crash-safe, atomic transaction.
  * undo restores byte-identically,
  * a mid-apply failure rolls back fully (nothing half-quarantined),
  * a crash (uncaught, no rollback) is repaired on next startup,
  * cross-filesystem moves copy-verify-delete (source safe until the copy is proven),
  * undo never clobbers an original path that is occupied again,
  * a red (irreplaceable) candidate can never even enter a Plan.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path

import pytest

from reclaim.core.model import Candidate, Operation, OpState, Plan, Tier
from reclaim.core.quarantine import ApplyError, QuarantineStore


# -- helpers ------------------------------------------------------------------

def _make_unit(root: Path, name: str = "node_modules") -> Path:
    """Create a realistic multi-file reclaimable unit and return its path."""
    unit = root / name
    (unit / "a").mkdir(parents=True)
    (unit / "a" / "index.js").write_text("hello " * 500)
    (unit / "b" / "deep").mkdir(parents=True)
    (unit / "b" / "deep" / "lib.js").write_text("world " * 800)
    return unit


def _tree_hash(path: Path) -> str:
    h = hashlib.sha256()
    for root, _dirs, files in os.walk(path):
        for f in sorted(files):
            fp = Path(root) / f
            h.update(fp.relative_to(path).as_posix().encode())
            h.update(fp.read_bytes())
    return h.hexdigest()


def _op(source: Path) -> Operation:
    return Operation(source=source, kind="node_modules", size_allocated=1234,
                     file_count=2, tier=Tier.REGENERABLE, regen_command="npm install")


def _store(tmp_path: Path, **kw) -> QuarantineStore:
    ids = (f"op-{i:04d}" for i in range(1, 10_000))
    return QuarantineStore(home=tmp_path / "home", id_gen=lambda: next(ids), **kw)


class _Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# -- the plan never carries a red item ----------------------------------------

def test_red_candidate_never_enters_plan() -> None:
    green = Candidate(Path("/x/nm"), "node_modules", 100, 100, 1, tier=Tier.REGENERABLE)
    red = Candidate(Path("/x/data"), "data", 999, 999, 1, tier=Tier.IRREPLACEABLE)
    plan = Plan.from_candidates([green, red])
    sources = {op.source for op in plan.operations}
    assert sources == {Path("/x/nm")}          # the red item is dropped


# -- apply / undo round-trip --------------------------------------------------

def test_apply_quarantines_and_frees(tmp_path: Path) -> None:
    unit = _make_unit(tmp_path)
    store = _store(tmp_path)

    tx = store.apply(Plan((_op(unit),)))

    assert tx.state is OpState.COMMITTED
    assert not unit.exists()                    # freed from its original location
    assert (store.ops_dir / tx.op_id / "store").exists()   # now in quarantine


def test_undo_restores_byte_identical(tmp_path: Path) -> None:
    unit = _make_unit(tmp_path)
    before = _tree_hash(unit)
    store = _store(tmp_path)

    tx = store.apply(Plan((_op(unit),)))
    result = store.undo(tx.op_id)

    assert unit.exists()
    assert _tree_hash(unit) == before           # exact restoration
    assert len(result.restored) == 1
    assert not result.skipped


def test_apply_empty_plan_is_noop(tmp_path: Path) -> None:
    tx = _store(tmp_path).apply(Plan(()))
    assert tx.is_noop
    assert tx.state is OpState.COMMITTED


# -- atomicity: a mid-apply failure rolls the whole op back -------------------

def test_apply_rolls_back_on_move_failure(tmp_path: Path) -> None:
    u1 = _make_unit(tmp_path / "p1")
    u2 = _make_unit(tmp_path / "p2")
    store = _store(tmp_path)

    real_move = store._move
    seen: list[int] = []

    def flaky(src: Path, dest: Path) -> None:
        seen.append(1)
        if len(seen) == 2:                      # second item fails
            raise OSError(errno.EACCES, "permission denied")
        real_move(src, dest)

    store._move = flaky  # type: ignore[method-assign]

    with pytest.raises(ApplyError):
        store.apply(Plan((_op(u1), _op(u2))))

    # Atomic: BOTH originals are back, nothing left quarantined.
    assert u1.exists() and u2.exists()
    op_id = "op-0001"
    from reclaim.core.journal import Journal
    assert Journal(store.ops_dir / op_id / "journal.jsonl").last_state() is OpState.ABORTED


# -- crash recovery: an uncaught interruption is repaired at startup ----------

def test_recover_rolls_back_interrupted_apply(tmp_path: Path) -> None:
    u1 = _make_unit(tmp_path / "p1")
    u2 = _make_unit(tmp_path / "p2")
    b1 = _tree_hash(u1)
    store = _store(tmp_path)

    real_move = store._move
    seen: list[int] = []

    def crash(src: Path, dest: Path) -> None:
        seen.append(1)
        if len(seen) == 2:
            raise RuntimeError("simulated power loss")   # NOT an OSError → uncaught by apply
        real_move(src, dest)

    store._move = crash  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        store.apply(Plan((_op(u1), _op(u2))))

    # Crash left item 0 quarantined and the journal mid-MOVING (no rollback happened).
    assert not u1.exists()

    # A fresh process starts and runs recovery.
    recovered = _store(tmp_path)
    actions = recovered.recover()

    assert actions and "rolled back" in actions[0]
    assert u1.exists() and _tree_hash(u1) == b1     # fully restored
    assert u2.exists()
    from reclaim.core.journal import Journal
    assert Journal(recovered.ops_dir / "op-0001" / "journal.jsonl").last_state() \
        is OpState.ABORTED


# -- cross-filesystem: copy → verify → delete ---------------------------------

def test_cross_fs_move_copies_verifies_deletes(tmp_path: Path, monkeypatch) -> None:
    unit = _make_unit(tmp_path)
    before = _tree_hash(unit)
    store = _store(tmp_path)

    def no_rename(*_a, **_k):
        raise OSError(errno.EXDEV, "cross-device link")     # force the copy path

    monkeypatch.setattr(os, "replace", no_rename)

    tx = store.apply(Plan((_op(unit),)))
    assert tx.state is OpState.COMMITTED
    assert not unit.exists()                                # source deleted after verify
    dest = Path(store.ops_dir / tx.op_id / "store" / "0" / "node_modules")
    assert dest.exists() and _tree_hash(dest) == before     # faithful copy

    store.undo(tx.op_id)
    assert unit.exists() and _tree_hash(unit) == before     # cross-fs restore is exact too


# -- undo never clobbers an occupied original path ----------------------------

def test_undo_does_not_clobber(tmp_path: Path) -> None:
    unit = _make_unit(tmp_path)
    store = _store(tmp_path)
    tx = store.apply(Plan((_op(unit),)))

    # The user (or a rebuild) recreated something at the original path.
    unit.mkdir(parents=True)
    (unit / "fresh.js").write_text("rebuilt")

    result = store.undo(tx.op_id)

    assert result.skipped and "occupied" in result.skipped[0][1]
    assert (unit / "fresh.js").read_text() == "rebuilt"     # untouched
    # The quarantined copy is left intact (still recoverable manually).
    assert (store.ops_dir / tx.op_id / "store").exists()


# -- TTL purge frees the blocks -----------------------------------------------

def test_purge_after_ttl(tmp_path: Path) -> None:
    unit = _make_unit(tmp_path)
    clock = _Clock()
    store = _store(tmp_path, clock=clock)
    tx = store.apply(Plan((_op(unit),)))

    clock.advance(8 * 86400)                    # 8 days later
    purged = store.purge(ttl_days=7)

    assert purged == [tx.op_id]
    assert not (store.ops_dir / tx.op_id / "store").exists()   # blocks truly freed
    from reclaim.core.journal import Journal
    assert Journal(store.ops_dir / tx.op_id / "journal.jsonl").last_state() \
        is OpState.PURGED


# -- purge survives read-only files (the Windows read-only attribute) ----------

def test_purge_survives_readonly_tree(tmp_path: Path) -> None:
    """Windows marks many npm/pip/.git files read-only, which makes a naive `rmtree` raise
    PermissionError — so a purge would crash or (with ignore_errors) silently leave the bytes
    on disk. Purge must still truly free the store. Reproduced on POSIX by making the
    quarantined tree non-writable (there deletion is gated by each parent dir's write bit)."""
    unit = _make_unit(tmp_path)
    clock = _Clock()
    store = _store(tmp_path, clock=clock)
    tx = store.apply(Plan((_op(unit),)))

    quarantined = store.ops_dir / tx.op_id / "store"
    for root, dirs, files in os.walk(quarantined):     # lock every file + dir in the tree
        for f in files:
            os.chmod(os.path.join(root, f), stat.S_IREAD)
        for d in dirs:
            os.chmod(os.path.join(root, d), 0o500)     # r-x: blocks deleting its children

    clock.advance(8 * 86400)
    purged = store.purge(ttl_days=7)

    assert purged == [tx.op_id]
    assert not quarantined.exists()      # freed despite the read-only tree (not just claimed)
