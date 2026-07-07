"""Live dashboard (L5, Phase 3d) — the whole picture at a glance, in one screen.

`build_dashboard()` is a **pure function**: given a scan result, disk usage, and an optional
trend, it composes rich renderables (panels, tables, a disk bar) into one dashboard — no I/O,
no scanning, no printing. That keeps it unit-testable (render to a string and assert on it)
and lets the CLI drive it either as a one-shot snapshot or inside a `rich.Live` refresh loop.

It's a *composition* layer: it reuses everything the engine already computes (tiers from the
classifier, projects from the analyzer, deltas from the Phase 3b trend, free space from the
Phase 3c monitor). Deliberately built on `rich` alone — no heavyweight TUI dependency — in
keeping with the project's minimal-deps, fully-tested discipline.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from reclaim.core.history import Trend
from reclaim.core.model import ScanResult, Tier
from reclaim.core.monitor import DiskUsage, resolve_min_free
from reclaim.humanize import human_bytes, human_delta

_TIER_META: dict[Tier, tuple[str, str, str]] = {
    Tier.REGENERABLE: ("🟢", "Regenerable", "green"),
    Tier.REGENERABLE_COSTLY: ("🟡", "Regenerable-costly", "yellow"),
    Tier.IRREPLACEABLE: ("🔴", "Protected", "red"),
}


def _short_path(p: Path, width: int = 40) -> str:
    """Home-relative, middle-elided path keeping the meaningful tail visible."""
    try:
        s = "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        s = str(p)
    if len(s) <= width:
        return s
    parts = Path(s).parts
    if len(parts) <= 3:
        return "…" + s[-(width - 1):]
    return f"{parts[0].rstrip('/')}/…/{parts[-2]}/{parts[-1]}"


def _disk_bar(disk: DiskUsage, min_free_bytes: int, cells: int = 22) -> Text:
    """A proportional used/free bar, coloured red when free space is at/under the threshold."""
    used_frac = disk.used / disk.total if disk.total else 0.0
    filled = max(0, min(cells, round(used_frac * cells)))
    low = disk.free <= min_free_bytes
    bar = Text()
    bar.append("█" * filled, style="red" if low else "green")
    bar.append("░" * (cells - filled), style="dim")
    pct = disk.free_fraction * 100
    bar.append(f"  {human_bytes(disk.free)} free", style="red" if low else "default")
    bar.append(f" / {human_bytes(disk.total)}  ({pct:.0f}% free)", style="dim")
    return bar


def _summary_panel(res: ScanResult) -> Panel:
    by_tier = res.by_tier()
    table = Table(show_header=False, box=None, pad_edge=False, expand=True)
    table.add_column(justify="left")
    table.add_column(justify="right")
    table.add_column(justify="left", style="dim")
    for tier in (Tier.REGENERABLE, Tier.REGENERABLE_COSTLY, Tier.IRREPLACEABLE):
        emoji, label, style = _TIER_META[tier]
        size, count = by_tier[tier]
        shown = human_bytes(size) if (size or tier is not Tier.IRREPLACEABLE) else "—"
        table.add_row(f"{emoji} [{style}]{label}[/]", shown, f"{count} unit(s)")
    header = Text.assemble(
        ("Reclaimable  ", "bold"),
        (human_bytes(res.reclaimable_allocated), "bold green"),
        (f"  of {human_bytes(res.total_allocated)} on disk", "dim"),
    )
    return Panel(Group(header, table), title="Summary", border_style="cyan", padding=(1, 2))


def _disk_panel(root: Path, disk: DiskUsage, min_free_bytes: int,
                reclaimable: int) -> Panel:
    low = disk.free <= min_free_bytes
    lines: list[RenderableType] = [_disk_bar(disk, min_free_bytes)]
    if low:
        msg = Text(f"⚠ below your {human_bytes(min_free_bytes)} threshold", style="bold red")
        if reclaimable:
            msg.append(f" — {human_bytes(reclaimable)} reclaimable now", style="red")
        lines.append(msg)
    else:
        lines.append(Text(f"✓ above your {human_bytes(min_free_bytes)} threshold", style="green"))
    return Panel(Group(*lines), title=f"Disk · {_short_path(root)}",
                 border_style="red" if low else "cyan", padding=(1, 2))


def _top_units_panel(res: ScanResult, n: int = 10) -> Panel:
    table = Table(show_header=True, header_style="dim", box=None, pad_edge=False, expand=True)
    table.add_column("size", justify="right", style="green", no_wrap=True)
    table.add_column("unit", no_wrap=True)
    table.add_column("path", no_wrap=True, overflow="ellipsis")
    top = res.top(n, reclaimable_only=True)
    if not top:
        return Panel(Text("nothing reclaimable here.", style="dim"),
                     title="Top units", border_style="cyan", padding=(1, 2))
    for c in top:
        emoji = _TIER_META[c.tier][0]
        table.add_row(human_bytes(c.size_allocated), f"{emoji} {c.kind}",
                      _short_path(c.path))
    return Panel(table, title=f"Top units (of {len(res.candidates)})",
                 border_style="cyan", padding=(1, 2))


def _projects_panel(res: ScanResult, limit: int = 8) -> Panel:
    by_root: dict[Path, int] = {}
    for c in res.candidates:
        if c.is_reclaimable and c.project_root is not None:
            by_root[c.project_root] = by_root.get(c.project_root, 0) + c.size_allocated
    if not res.projects:
        return Panel(Text("no projects detected.", style="dim"),
                     title="Projects", border_style="cyan", padding=(1, 2))
    ordered = sorted(res.projects, key=lambda p: by_root.get(p.root, 0), reverse=True)
    table = Table(show_header=True, header_style="dim", box=None, pad_edge=False, expand=True)
    table.add_column("project", no_wrap=True)
    table.add_column("git")
    table.add_column("reclaimable", justify="right")
    for p in ordered[:limit]:
        git_style = "red" if p.git.is_wip else "green"
        recl = by_root.get(p.root, 0)
        table.add_row(p.root.name or str(p.root),
                      f"[{git_style}]{p.git.status.value}[/]",
                      human_bytes(recl) if recl else "[dim]—[/]")
    return Panel(table, title=f"Projects ({len(res.projects)})",
                 border_style="cyan", padding=(1, 2))


def _trend_panel(trend: Trend | None) -> Panel:
    if trend is None:
        body: RenderableType = Text(
            "not enough history yet — scans build it over time.", style="dim")
        return Panel(body, title="Trend", border_style="cyan", padding=(1, 2))
    delta = trend.reclaimable_delta
    verb, style = (("grew", "yellow") if delta > 0 else
                   ("shrank", "green") if delta < 0 else ("unchanged", "dim"))
    header = Text.assemble(
        ("reclaimable ", "default"), (f"{verb} {human_delta(delta)}", style),
        (f"  ·  now {human_bytes(trend.latest.reclaimable_allocated)}", "dim"),
    )
    table = Table(show_header=False, box=None, pad_edge=False, expand=True)
    table.add_column("kind", no_wrap=True)
    table.add_column("change", justify="right")
    for d in trend.kinds[:6]:
        d_style = "yellow" if d.delta > 0 else "green"
        table.add_row(d.kind, f"[{d_style}]{human_delta(d.delta)}[/]")
    body = Group(header, table) if trend.kinds else header
    return Panel(body, title="Trend", border_style="cyan", padding=(1, 2))


def _two_columns(left: RenderableType, right: RenderableType) -> Table:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return grid


def build_dashboard(
    root: Path,
    res: ScanResult,
    disk: DiskUsage,
    trend: Trend | None,
    *,
    min_free: str = "10%",
) -> RenderableType:
    """Compose the full dashboard for one root. Pure — no I/O, no scanning, no printing."""
    try:
        min_free_bytes = resolve_min_free(min_free, disk.total)
    except ValueError:
        min_free_bytes = resolve_min_free("10%", disk.total)

    title = Text.assemble(
        ("Reclaim", "bold cyan"), ("  ·  ", "dim"), (_short_path(root), "bold"),
        (f"  ·  {res.file_count:,} files scanned in {res.elapsed_seconds:.2f}s", "dim"),
    )
    return Group(
        title,
        Text(),
        _two_columns(_summary_panel(res),
                     _disk_panel(root, disk, min_free_bytes, res.reclaimable_allocated)),
        _top_units_panel(res),
        _two_columns(_projects_panel(res), _trend_panel(trend)),
    )
