"""Layer-neutral formatting helpers shared across the engine and interfaces.

Pure, dependency-free (no typer/rich/OS), so any layer may import it without coupling.
"""

from __future__ import annotations


def human_bytes(n: float) -> str:
    """Render a byte count as a compact human string, e.g. 1536 → '1.5 KB'."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_delta(n: int) -> str:
    """Render a signed byte change, e.g. +1.5 GB · -800.0 MB · ±0 B (for trends)."""
    if n == 0:
        return "±0 B"
    return f"{'+' if n > 0 else '-'}{human_bytes(abs(n))}"
