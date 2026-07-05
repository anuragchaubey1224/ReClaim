"""CLI (L5) — a thin adapter over the engine. No business logic lives here."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from reclaim.core.scanner import Scanner

app = typer.Typer(add_completion=False, help="Reclaim — disk-reclamation engine (spike)")
console = Console()


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@app.command()
def scan(
    path: Path = typer.Argument(Path.home(), help="Directory to scan"),
    workers: Optional[int] = typer.Option(None, "--workers", "-w",
                                          help="Worker threads (default: auto)"),
    context_sensitive: bool = typer.Option(
        False, "--context-sensitive",
        help="Also flag dist/ build/ out/ (may include real user dirs)"),
) -> None:
    """Scan a directory and report reclaimable space."""
    scanner = Scanner(workers=workers, include_context_sensitive=context_sensitive)
    with console.status(f"scanning [bold]{path}[/] …", spinner="dots"):
        res = scanner.scan(path)

    console.print(
        f"scanned [bold]{res.file_count:,}[/] files in {res.dir_count:,} dirs "
        f"({res.error_count} skipped) in [bold]{res.elapsed_seconds:.2f}s[/] "
        f"[dim]{scanner.workers} workers · {scanner.platform.name}[/]"
    )
    console.print(
        f"reclaimable: [bold green]{_human(res.reclaimable_allocated)}[/] "
        f"of {_human(res.total_allocated)} on disk\n"
    )

    if not res.candidates:
        console.print("[dim]no reclaimable units found[/]")
        return

    table = Table(title="Reclaimable by kind", title_justify="left")
    table.add_column("size", justify="right", style="green")
    table.add_column("kind")
    table.add_column("count", justify="right", style="dim")
    for kind, (size, count) in sorted(
        res.by_kind().items(), key=lambda kv: kv[1][0], reverse=True
    ):
        table.add_row(_human(size), kind, str(count))
    console.print(table)


if __name__ == "__main__":
    app()
