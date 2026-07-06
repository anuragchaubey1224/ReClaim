"""CLI (L5) — a thin adapter over the engine. No business logic lives here.

Phase 1a surface: `scan` (fast summary + tiers) and `status` (full tiered report with
per-project fact sheets). Both are 100% read-only — nothing is ever removed here. The
plan/apply/undo loop arrives in Phase 1c.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from reclaim.core.classifier import classify_scan
from reclaim.core.model import ScanResult, Tier
from reclaim.core.scanner import Scanner

app = typer.Typer(add_completion=False, help="Reclaim — disk-reclamation engine")
console = Console()

# Tier presentation: (emoji, label, rich style).
_TIER_META: dict[Tier, tuple[str, str, str]] = {
    Tier.REGENERABLE: ("🟢", "Regenerable", "green"),
    Tier.REGENERABLE_COSTLY: ("🟡", "Regenerable-costly", "yellow"),
    Tier.IRREPLACEABLE: ("🔴", "Protected", "red"),
}


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _scan_and_classify(path: Path, workers: Optional[int],
                       context_sensitive: bool) -> tuple[ScanResult, Scanner]:
    scanner = Scanner(workers=workers, include_context_sensitive=context_sensitive)
    with console.status(f"scanning [bold]{path}[/] …", spinner="dots"):
        raw = scanner.scan(path)
    with console.status("classifying projects (git state, activity) …", spinner="dots"):
        res = classify_scan(raw)
    return res, scanner


def _print_scan_line(res: ScanResult, scanner: Scanner) -> None:
    console.print(
        f"scanned [bold]{res.file_count:,}[/] files in {res.dir_count:,} dirs "
        f"({res.error_count} skipped) in [bold]{res.elapsed_seconds:.2f}s[/] "
        f"[dim]{scanner.workers} workers · {scanner.platform.name}[/]"
    )


def _print_tier_summary(res: ScanResult) -> None:
    by_tier = res.by_tier()
    n_projects = len(res.projects)
    n_protected = sum(1 for p in res.projects if p.is_protected)
    reclaimable = res.reclaimable_allocated

    console.print(
        f"\nReclaimable: [bold green]{_human(reclaimable)}[/] "
        f"of {_human(res.total_allocated)} on disk "
        f"[dim]· {n_projects} project(s)[/]\n"
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
            shown = _human(size) if size else "—"
        else:
            note = f"{count} unit(s)"
            shown = _human(size)
        table.add_row(f"{emoji} [{style}]{label}[/]", shown, note)
    console.print(table)


def _print_projects(res: ScanResult, limit: int = 12) -> None:
    if not res.projects:
        return
    # Reclaimable bytes attributable to each project root (green/yellow only).
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
        table.add_row(
            p.root.name or str(p.root),
            p.project_type,
            f"[{git_style}]{p.git.status.value}[/]",
            activity,
            _human(recl) if recl else "[dim]—[/]",
        )
    console.print(table)


@app.command()
def scan(
    path: Path = typer.Argument(Path.home(), help="Directory to scan"),
    workers: Optional[int] = typer.Option(None, "--workers", "-w",
                                          help="Worker threads (default: auto)"),
    context_sensitive: bool = typer.Option(
        False, "--context-sensitive",
        help="Also flag dist/ build/ out/ (may include real user dirs)"),
) -> None:
    """Scan a directory and report reclaimable space, grouped by tier."""
    res, scanner = _scan_and_classify(path, workers, context_sensitive)
    _print_scan_line(res, scanner)
    _print_tier_summary(res)

    table = Table(title="\nReclaimable by kind", title_justify="left",
                  header_style="dim", box=None, pad_edge=False)
    table.add_column("size", justify="right", style="green")
    table.add_column("kind")
    table.add_column("count", justify="right", style="dim")
    reclaimable_kinds = {}
    for c in res.candidates:
        if c.is_reclaimable:
            s, n = reclaimable_kinds.get(c.kind, (0, 0))
            reclaimable_kinds[c.kind] = (s + c.size_allocated, n + 1)
    for kind, (size, count) in sorted(
        reclaimable_kinds.items(), key=lambda kv: kv[1][0], reverse=True
    ):
        table.add_row(_human(size), kind, str(count))
    if reclaimable_kinds:
        console.print(table)


@app.command()
def status(
    path: Path = typer.Argument(Path.home(), help="Directory to scan"),
    workers: Optional[int] = typer.Option(None, "--workers", "-w",
                                          help="Worker threads (default: auto)"),
    context_sensitive: bool = typer.Option(
        False, "--context-sensitive",
        help="Also flag dist/ build/ out/ (may include real user dirs)"),
) -> None:
    """Full reclaimable report: tiers + per-project fact sheets (read-only)."""
    res, scanner = _scan_and_classify(path, workers, context_sensitive)
    _print_scan_line(res, scanner)
    _print_tier_summary(res)
    _print_projects(res)

    top = res.top(10, reclaimable_only=True)
    if top:
        console.print("\n[bold]Largest reclaimable units[/]")
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(justify="right", style="green")
        table.add_column()
        table.add_column(justify="left", style="dim")
        for c in top:
            emoji = _TIER_META[c.tier][0]
            table.add_row(_human(c.size_allocated), f"{emoji} {c.path}",
                          c.regen_command or "")
        console.print(table)


if __name__ == "__main__":
    app()
