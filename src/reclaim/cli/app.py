"""CLI (L5) — a thin adapter over the engine. No business logic lives here.

Command surface (Phase 1 complete):
  * scan / status   — read-only inventory: reclaimable space by tier + project fact sheets.
  * plan            — preview a reclaim plan for a goal (never mutates).
  * apply           — reclaim: scan → plan → Safety Gate → confirm → quarantine. Dry-run by
                      default; `--yes` skips the prompt. Everything is undoable.
  * undo            — restore a quarantined operation (latest by default).
  * ls / purge      — inspect the quarantine store / permanently free expired items.

The reclaim store lives at `$RECLAIM_HOME` (default `~/.reclaim`). Every store-touching
command first runs crash recovery (ARCHITECTURE.md §7.5). All safety logic is in L2/L3 — this
file only parses args, renders, and confirms.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from reclaim.core.classifier import classify_scan
from reclaim.core.model import OpState, ScanResult, Tier
from reclaim.core.planner import Planner, PlanGoal, PlanResult, parse_size
from reclaim.core.quarantine import QuarantineStore, UndoError
from reclaim.core.scanner import Scanner
from reclaim.humanize import human_bytes
from reclaim.safety.gate import GateResult, SafetyGate

app = typer.Typer(add_completion=False, help="Reclaim — disk-reclamation engine")
console = Console()
err = Console(stderr=True)

_TIER_META: dict[Tier, tuple[str, str, str]] = {
    Tier.REGENERABLE: ("🟢", "Regenerable", "green"),
    Tier.REGENERABLE_COSTLY: ("🟡", "Regenerable-costly", "yellow"),
    Tier.IRREPLACEABLE: ("🔴", "Protected", "red"),
}

# Options reused by scan/status/plan/apply.
_PATH_ARG = typer.Argument(None, help="Directory to scan (default: home)")
_WORKERS_OPT = typer.Option(None, "--workers", "-w", help="Worker threads (default: auto)")
_CS_OPT = typer.Option(False, "--context-sensitive",
                       help="Also flag dist/ build/ out/ (may include real user dirs)")


# --------------------------------------------------------------------------- #
# Store + shared helpers
# --------------------------------------------------------------------------- #

def _store() -> QuarantineStore:
    home = os.environ.get("RECLAIM_HOME")
    return QuarantineStore(home=Path(home) if home else None)


def _recover(store: QuarantineStore) -> None:
    """Repair any transaction interrupted by a prior crash, announcing what it did."""
    for action in store.recover():
        err.print(f"[yellow]recovered[/] {action}")


def _scan_and_classify(path: Path, workers: Optional[int],
                       context_sensitive: bool) -> tuple[ScanResult, Scanner]:
    scanner = Scanner(workers=workers, include_context_sensitive=context_sensitive)
    with console.status(f"scanning [bold]{path}[/] …", spinner="dots"):
        raw = scanner.scan(path)
    with console.status("classifying projects (git state, activity) …", spinner="dots"):
        res = classify_scan(raw)
    return res, scanner


def _resolve_path(path: Optional[Path]) -> Path:
    return (path or Path.home()).expanduser()


def _goal(free: Optional[str], include_costly: bool, dormant_only: bool,
          kind: Optional[List[str]], min_size: Optional[str],
          include_low_confidence: bool) -> PlanGoal:
    try:
        free_bytes = parse_size(free) if free else None
        min_bytes = parse_size(min_size) if min_size else 0
    except ValueError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(2) from e
    return PlanGoal(
        free_bytes=free_bytes,
        include_costly=include_costly,
        dormant_only=dormant_only,
        kinds=frozenset(kind) if kind else None,
        min_bytes=min_bytes,
        include_low_confidence=include_low_confidence,
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _print_scan_line(res: ScanResult, scanner: Scanner) -> None:
    console.print(
        f"scanned [bold]{res.file_count:,}[/] files in {res.dir_count:,} dirs "
        f"({res.error_count} skipped) in [bold]{res.elapsed_seconds:.2f}s[/] "
        f"[dim]{scanner.workers} workers · {scanner.platform.name}[/]"
    )


def _print_tier_summary(res: ScanResult) -> None:
    by_tier = res.by_tier()
    n_protected = sum(1 for p in res.projects if p.is_protected)
    console.print(
        f"\nReclaimable: [bold green]{human_bytes(res.reclaimable_allocated)}[/] "
        f"of {human_bytes(res.total_allocated)} on disk "
        f"[dim]· {len(res.projects)} project(s)[/]\n"
    )
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(justify="left")
    table.add_column(justify="right")
    table.add_column(justify="left", style="dim")
    for tier in (Tier.REGENERABLE, Tier.REGENERABLE_COSTLY, Tier.IRREPLACEABLE):
        emoji, label, style = _TIER_META[tier]
        size, count = by_tier[tier]
        if tier is Tier.IRREPLACEABLE:
            note = f"{n_protected} project(s) with uncommitted/unpushed work"
            shown = human_bytes(size) if size else "—"
        else:
            note = f"{count} unit(s)"
            shown = human_bytes(size)
        table.add_row(f"{emoji} [{style}]{label}[/]", shown, note)
    console.print(table)


def _print_projects(res: ScanResult, limit: int = 12) -> None:
    if not res.projects:
        return
    by_root: dict[Path, int] = {}
    for c in res.candidates:
        if c.is_reclaimable and c.project_root is not None:
            by_root[c.project_root] = by_root.get(c.project_root, 0) + c.size_allocated
    ordered = sorted(res.projects, key=lambda p: by_root.get(p.root, 0), reverse=True)
    console.print("\n[bold]Projects[/]")
    table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
    table.add_column("project")
    table.add_column("type", style="dim")
    table.add_column("git")
    table.add_column("activity", style="dim")
    table.add_column("reclaimable", justify="right")
    for p in ordered[:limit]:
        git_style = "red" if p.git.is_wip else "green"
        activity = (f"dormant {p.last_activity_days}d" if p.is_dormant
                    else (f"{p.last_activity_days}d" if p.last_activity_days is not None
                          else "—"))
        recl = by_root.get(p.root, 0)
        table.add_row(p.root.name or str(p.root), p.project_type,
                      f"[{git_style}]{p.git.status.value}[/]", activity,
                      human_bytes(recl) if recl else "[dim]—[/]")
    console.print(table)


def _render_plan(pr: PlanResult, gate: GateResult) -> None:
    """Show the concrete plan the Safety Gate approved, plus risks and any rejections."""
    approved = gate.approved
    if approved.is_empty:
        console.print("[dim]nothing to reclaim under this goal.[/]")
    else:
        console.print(
            f"\n[bold]Plan[/] — reclaim [bold green]{human_bytes(approved.total_bytes)}[/] "
            f"across [bold]{len(approved.operations)}[/] unit(s), "
            f"[dim]{approved.total_files:,} files[/]\n"
        )
        table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
        table.add_column("size", justify="right", style="green")
        table.add_column("unit")
        table.add_column("path")
        table.add_column("rebuild", style="dim")
        for op in sorted(approved.operations, key=lambda o: o.size_allocated, reverse=True):
            emoji = _TIER_META[op.tier][0]
            table.add_row(human_bytes(op.size_allocated), f"{emoji} {op.kind}",
                          str(op.source), op.regen_command or "")
        console.print(table)

    for risk in approved.risks:
        console.print(f"[yellow]⚠ {risk}[/]")

    if gate.has_rejections:
        console.print(f"\n[red]{len(gate.rejected)} item(s) blocked by the safety gate:[/]")
        for rej in gate.rejected[:10]:
            console.print(f"  [red]✗[/] {rej.operation.source} [dim]— {rej.reason}[/]")

    skipped = len(pr.excluded)
    if skipped:
        console.print(f"[dim]{skipped} candidate(s) not selected "
                      f"(costly / low-confidence / filtered / target met).[/]")


# --------------------------------------------------------------------------- #
# Read-only commands
# --------------------------------------------------------------------------- #

@app.command()
def scan(path: Optional[Path] = _PATH_ARG, workers: Optional[int] = _WORKERS_OPT,
         context_sensitive: bool = _CS_OPT) -> None:
    """Scan a directory and report reclaimable space, grouped by tier."""
    res, scanner = _scan_and_classify(_resolve_path(path), workers, context_sensitive)
    _print_scan_line(res, scanner)
    _print_tier_summary(res)


@app.command()
def status(path: Optional[Path] = _PATH_ARG, workers: Optional[int] = _WORKERS_OPT,
           context_sensitive: bool = _CS_OPT) -> None:
    """Full reclaimable report: tiers + per-project fact sheets (read-only)."""
    res, scanner = _scan_and_classify(_resolve_path(path), workers, context_sensitive)
    _print_scan_line(res, scanner)
    _print_tier_summary(res)
    _print_projects(res)


# --------------------------------------------------------------------------- #
# The reclaim loop
# --------------------------------------------------------------------------- #

def _plan_flow(path: Path, workers: Optional[int], context_sensitive: bool,
               goal: PlanGoal) -> tuple[PlanResult, GateResult, Scanner]:
    res, scanner = _scan_and_classify(path, workers, context_sensitive)
    _print_scan_line(res, scanner)
    pr = Planner().plan(res, goal)
    gate = SafetyGate().validate(pr.plan)
    _render_plan(pr, gate)
    return pr, gate, scanner


@app.command()
def plan(
    path: Optional[Path] = _PATH_ARG,
    free: Optional[str] = typer.Option(None, "--free", help="Target to free, e.g. 20G"),
    include_costly: bool = typer.Option(False, "--include-costly", "-c",
                                        help="Also include 🟡 costly-to-rebuild units"),
    dormant_only: bool = typer.Option(False, "--dormant-only",
                                      help="Only units in dormant projects"),
    kind: Optional[List[str]] = typer.Option(None, "--kind",
                                             help="Restrict to a kind (repeatable)"),
    min_size: Optional[str] = typer.Option(None, "--min-size", help="Ignore units below"),
    include_low_confidence: bool = typer.Option(False, "--include-low-confidence"),
    workers: Optional[int] = _WORKERS_OPT,
    context_sensitive: bool = _CS_OPT,
) -> None:
    """Preview a reclaim plan for a goal. Never mutates anything."""
    g = _goal(free, include_costly, dormant_only, kind, min_size, include_low_confidence)
    _, gate, _ = _plan_flow(_resolve_path(path), workers, context_sensitive, g)
    if not gate.approved.is_empty:
        console.print("\n[dim]run [bold]reclaim apply[/] with the same options to reclaim "
                      "this space (undoable).[/]")


@app.command()
def apply(
    path: Optional[Path] = _PATH_ARG,
    free: Optional[str] = typer.Option(None, "--free", help="Target to free, e.g. 20G"),
    include_costly: bool = typer.Option(False, "--include-costly", "-c"),
    dormant_only: bool = typer.Option(False, "--dormant-only"),
    kind: Optional[List[str]] = typer.Option(None, "--kind"),
    min_size: Optional[str] = typer.Option(None, "--min-size"),
    include_low_confidence: bool = typer.Option(False, "--include-low-confidence"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    workers: Optional[int] = _WORKERS_OPT,
    context_sensitive: bool = _CS_OPT,
) -> None:
    """Reclaim space: scan → plan → safety gate → confirm → quarantine (undoable)."""
    store = _store()
    _recover(store)
    g = _goal(free, include_costly, dormant_only, kind, min_size, include_low_confidence)
    _, gate, _ = _plan_flow(_resolve_path(path), workers, context_sensitive, g)

    if gate.approved.is_empty:
        raise typer.Exit(0)

    if not yes and not typer.confirm(
        f"\nReclaim {human_bytes(gate.approved.total_bytes)} across "
        f"{len(gate.approved.operations)} unit(s)? Everything is undoable."
    ):
        console.print("[dim]aborted — nothing was touched.[/]")
        raise typer.Exit(0)

    with console.status("quarantining …", spinner="dots"):
        tx = store.apply(gate.approved)
    console.print(
        f"\n[bold green]✓ reclaimed {human_bytes(tx.freed_bytes)}[/] "
        f"across {len(tx.items)} unit(s) — [dim]op {tx.op_id}[/]"
    )
    console.print(f"[dim]undo anytime with [bold]reclaim undo {tx.op_id}[/][/]")


@app.command()
def undo(op_id: Optional[str] = typer.Argument(None, help="Op to undo (default: latest)")) \
        -> None:
    """Restore a quarantined operation. Never overwrites files that reappeared."""
    store = _store()
    _recover(store)

    if op_id is None:
        committed = [s for s in store.list_ops() if s.state is OpState.COMMITTED]
        if not committed:
            console.print("[dim]nothing to undo.[/]")
            raise typer.Exit(0)
        op_id = committed[-1].op_id            # list_ops is chronological

    try:
        result = store.undo(op_id)
    except UndoError as e:
        err.print(f"[red]error:[/] {e}")
        raise typer.Exit(1) from e

    console.print(f"[bold green]✓ restored {len(result.restored)} unit(s)[/] "
                  f"[dim]op {op_id}[/]")
    for src, reason in result.skipped:
        console.print(f"  [yellow]skipped[/] {src} [dim]— {reason}[/]")


@app.command("ls")
def list_ops() -> None:
    """List quarantined operations (their id, state, freed space, and age)."""
    store = _store()
    _recover(store)
    ops = store.list_ops()
    if not ops:
        console.print("[dim]quarantine is empty.[/]")
        return
    table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
    table.add_column("op-id")
    table.add_column("state")
    table.add_column("freed", justify="right")
    table.add_column("units", justify="right", style="dim")
    table.add_column("age", justify="right", style="dim")
    for s in ops:
        style = {"committed": "green", "restored": "dim",
                 "purged": "dim", "aborted": "red"}.get(s.state.value, "white")
        table.add_row(s.op_id, f"[{style}]{s.state.value}[/]", human_bytes(s.freed_bytes),
                      str(s.item_count), f"{s.age_days:.1f}d")
    console.print(table)


@app.command()
def purge(older_than: float = typer.Option(7.0, "--older-than",
          help="Permanently delete committed ops older than N days")) -> None:
    """Permanently free quarantined items past their TTL (this is irreversible)."""
    store = _store()
    _recover(store)
    purged = store.purge(ttl_days=older_than)
    if not purged:
        console.print("[dim]nothing to purge.[/]")
        return
    console.print(f"[bold]purged {len(purged)} op(s)[/] older than {older_than:g}d "
                  f"[dim]— blocks freed permanently[/]")


if __name__ == "__main__":
    app()
