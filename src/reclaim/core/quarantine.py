"""Quarantine store (L2) — make removal crash-safe and reversible (owns I3, I4).

The engine NEVER hard-deletes on apply (I3). "Reclaiming" moves each item into a per-op
quarantine store under `~/.reclaim/ops/<op-id>/store/`, guarded by a write-ahead journal
(ARCHITECTURE.md §7.3–7.6, docs/04 §6):

  * Same-filesystem move  → `os.replace`, an atomic O(1) rename (no copy, no corruption).
  * Cross-filesystem move → copy → verify (file-count + bytes) → delete source. The source
    is removed only AFTER the copy is verified, so a crash can never lose data.

A transaction is **atomic**: if any item fails mid-apply, everything already moved is rolled
back and the op ends ABORTED — the user sees all-or-nothing (§7.5). `undo` reverse-replays a
committed op, and never clobbers an original path that is occupied again (§7.6). `recover`
runs at startup to finish or roll back any transaction interrupted by a crash.

Everything is injectable (`home`, `clock`, `id_gen`) so tests run in a tmp dir with a
deterministic clock and ids — no real `~/.reclaim`, no wall-clock.
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from reclaim.core.journal import Journal
from reclaim.core.model import OpState, Plan


class ApplyError(RuntimeError):
    """A reclaim transaction failed mid-apply and was rolled back (op is ABORTED)."""

    def __init__(self, op_id: str, detail: str) -> None:
        super().__init__(f"apply {op_id} failed and was rolled back: {detail}")
        self.op_id = op_id


class UndoError(RuntimeError):
    """An op could not be undone (wrong state / missing)."""

    def __init__(self, op_id: str, detail: str) -> None:
        super().__init__(f"cannot undo {op_id}: {detail}")
        self.op_id = op_id


@dataclass(frozen=True, slots=True)
class MovedItem:
    source: Path        # original location (where it will be restored to)
    dest: Path          # location inside the quarantine store
    kind: str
    size_allocated: int
    file_count: int


@dataclass(frozen=True, slots=True)
class Transaction:
    op_id: str
    state: OpState
    items: tuple[MovedItem, ...]
    freed_bytes: int

    @property
    def is_noop(self) -> bool:
        return not self.op_id


@dataclass(frozen=True, slots=True)
class OpSummary:
    op_id: str
    state: OpState
    freed_bytes: int
    item_count: int
    created_at: float
    age_days: float


@dataclass(frozen=True, slots=True)
class RestoreResult:
    op_id: str
    restored: tuple[Path, ...]
    skipped: tuple[tuple[Path, str], ...]     # (original_path, reason)


def _tree_signature(path: Path) -> tuple[int, int]:
    """(file_count, total_apparent_bytes) — a cheap integrity fingerprint for cross-FS
    copy verification. Avoids hashing millions of files while still catching truncation."""
    if path.is_symlink():
        return (1, 0)
    if path.is_file():
        return (1, path.stat().st_size)
    count = total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                st = os.stat(os.path.join(root, name), follow_symlinks=False)
                count += 1
                total += st.st_size
            except OSError:
                continue
    return (count, total)


class QuarantineStore:
    def __init__(
        self,
        home: Path | str | None = None,
        *,
        clock: Callable[[], float] | None = None,
        id_gen: Callable[[], str] | None = None,
    ) -> None:
        self.home = Path(home) if home is not None else (Path.home() / ".reclaim")
        self.ops_dir = self.home / "ops"
        self._clock = clock or time.time
        self._id_gen = id_gen or self._default_id

    def _default_id(self) -> str:
        # Sortable + unique: lexical order == chronological order.
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        return f"{stamp}-{secrets.token_hex(3)}"

    def _journal(self, opdir: Path) -> Journal:
        return Journal(opdir / "journal.jsonl", clock=self._clock)

    # -- move primitive (same-FS atomic rename, else copy→verify→delete) --------

    def _move(self, src: Path, dest: Path) -> None:
        """Move `src` to `dest` losslessly. Same-FS is an atomic rename; cross-FS copies,
        verifies, then deletes the source (source survives until the copy is proven good)."""
        try:
            os.replace(src, dest)
            return
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise                       # not a cross-device case — a real error

        # Cross-filesystem: copy → verify → delete.
        before = _tree_signature(src)
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)
        if _tree_signature(dest) != before:
            _remove(dest)                   # bad copy — discard it, keep the source intact
            raise OSError(f"cross-fs copy verification failed for {src}")
        _remove(src)                        # verified — now safe to delete the source

    # -- apply -----------------------------------------------------------------

    def apply(self, plan: Plan) -> Transaction:
        """Quarantine every operation in `plan` as one atomic, journaled transaction."""
        if plan.is_empty:
            return Transaction(op_id="", state=OpState.COMMITTED, items=(), freed_bytes=0)

        op_id = self._id_gen()
        opdir = self.ops_dir / op_id
        store = opdir / "store"
        store.mkdir(parents=True, exist_ok=False)
        journal = self._journal(opdir)

        items_meta = [
            {"source": str(op.source), "kind": op.kind,
             "size": op.size_allocated, "files": op.file_count}
            for op in plan.operations
        ]
        journal.state(OpState.PLANNED, op_id=op_id, items=items_meta)
        journal.state(OpState.PREPARING)
        journal.state(OpState.MOVING)

        moved: list[MovedItem] = []
        try:
            for i, op in enumerate(plan.operations):
                slot = store / str(i)
                slot.mkdir()
                dest = slot / op.source.name
                self._move(op.source, dest)     # journal AFTER the move records what to undo
                journal.event("moved", index=i, source=str(op.source), dest=str(dest))
                moved.append(MovedItem(op.source, dest, op.kind,
                                       op.size_allocated, op.file_count))
        except OSError as e:
            self._rollback(moved, journal)
            raise ApplyError(op_id, str(e)) from e

        journal.state(OpState.COMMITTED)
        freed = sum(m.size_allocated for m in moved)
        self._write_manifest(opdir, op_id, moved, freed)
        return Transaction(op_id, OpState.COMMITTED, tuple(moved), freed)

    def _rollback(self, moved: list[MovedItem], journal: Journal) -> None:
        """Reverse every completed move (atomic-abort). Best-effort per item; recovery will
        retry anything left behind."""
        for m in reversed(moved):
            try:
                if not m.source.exists():
                    self._move(m.dest, m.source)
            except OSError:
                continue
        journal.state(OpState.ABORTED)

    # -- undo ------------------------------------------------------------------

    def undo(self, op_id: str) -> RestoreResult:
        opdir = self.ops_dir / op_id
        journal = self._journal(opdir)
        state = journal.last_state()
        if state is None:
            raise UndoError(op_id, "no such operation")
        if state is OpState.RESTORED:
            return RestoreResult(op_id, (), ())     # already undone — idempotent no-op
        if state is not OpState.COMMITTED:
            raise UndoError(op_id, f"state is {state.value}, only COMMITTED is undoable")
        return self._restore(op_id, opdir, journal)

    def _restore(self, op_id: str, opdir: Path, journal: Journal) -> RestoreResult:
        """Move each item back to its original path. Never clobbers an occupied path (§7.6).
        Idempotent: already-restored items are skipped, so a re-run completes the job."""
        moved = {e["index"]: e for e in journal.events("moved")}
        done = {e["index"] for e in journal.events("restored")}
        restored: list[Path] = []
        skipped: list[tuple[Path, str]] = []
        for idx in sorted(moved):
            if idx in done:
                continue
            src = Path(moved[idx]["source"])
            dest = Path(moved[idx]["dest"])
            if src.exists():
                skipped.append((src, "original path is occupied"))
                journal.event("restored", index=idx, skipped=True)
                continue
            if not dest.exists():
                skipped.append((src, "quarantined item missing"))
                journal.event("restored", index=idx, skipped=True)
                continue
            self._move(dest, src)
            journal.event("restored", index=idx)
            restored.append(src)
        journal.state(OpState.RESTORED)
        # Only reclaim the (now-empty) store when everything was moved back. If any item was
        # skipped, its quarantined copy must stay put — still recoverable.
        if not skipped:
            shutil.rmtree(opdir / "store", ignore_errors=True)
        return RestoreResult(op_id, tuple(restored), tuple(skipped))

    # -- crash recovery (run at startup, §7.5) ---------------------------------

    def recover(self) -> list[str]:
        """Finish or roll back any transaction interrupted by a crash. Idempotent."""
        actions: list[str] = []
        if not self.ops_dir.exists():
            return actions
        for opdir in sorted(self.ops_dir.iterdir()):
            if not (opdir / "journal.jsonl").exists():
                continue
            journal = self._journal(opdir)
            state = journal.last_state()
            if state is None:
                continue
            if state in (OpState.PLANNED, OpState.PREPARING, OpState.MOVING):
                # Interrupted apply: reverse whatever was moved → ABORTED (nothing lost).
                for ev in reversed(journal.events("moved")):
                    src, dest = Path(ev["source"]), Path(ev["dest"])
                    if not src.exists() and dest.exists():
                        try:
                            self._move(dest, src)
                        except OSError:
                            continue
                journal.state(OpState.ABORTED)
                actions.append(f"{opdir.name}: rolled back interrupted apply")
            elif state is OpState.COMMITTED and journal.events("restored"):
                # Interrupted undo: finish restoring the remaining items.
                self._restore(opdir.name, opdir, journal)
                actions.append(f"{opdir.name}: completed interrupted undo")
        return actions

    # -- inspection & TTL purge ------------------------------------------------

    def list_ops(self) -> list[OpSummary]:
        out: list[OpSummary] = []
        if not self.ops_dir.exists():
            return out
        now = self._clock()
        for opdir in sorted(self.ops_dir.iterdir()):
            journal = self._journal(opdir)
            recs = journal.records()
            if not recs:
                continue
            state = journal.last_state() or OpState.PLANNED
            items = journal.planned_items()
            created = recs[0].get("ts", now)
            freed = sum(it.get("size", 0) for it in items)
            out.append(OpSummary(
                op_id=opdir.name, state=state, freed_bytes=freed,
                item_count=len(items), created_at=created,
                age_days=max(0.0, (now - created) / 86400),
            ))
        return out

    def purge(self, ttl_days: float) -> list[str]:
        """Permanently delete quarantined data for committed ops older than `ttl_days`.
        This is when the blocks are truly freed. Idempotent."""
        purged: list[str] = []
        for s in self.list_ops():
            if s.state is OpState.COMMITTED and s.age_days >= ttl_days:
                opdir = self.ops_dir / s.op_id
                shutil.rmtree(opdir / "store", ignore_errors=True)
                self._journal(opdir).state(OpState.PURGED)
                purged.append(s.op_id)
        return purged

    # -- manifest (human-readable convenience; journal remains authoritative) ---

    def _write_manifest(self, opdir: Path, op_id: str,
                        moved: list[MovedItem], freed: int) -> None:
        manifest = {
            "op_id": op_id,
            "created_at": self._clock(),
            "freed_bytes": freed,
            "items": [
                {"source": str(m.source), "dest": str(m.dest), "kind": m.kind,
                 "size": m.size_allocated, "files": m.file_count}
                for m in moved
            ],
        }
        try:
            (opdir / "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8")
        except OSError:
            pass    # non-fatal: the journal is the source of truth, not this file


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
