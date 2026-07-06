# Reclaim

> **AI-guided disk-reclamation engine for developers.**
> Local-first · cross-platform · reversible · faster than `du`.

Reclaim safely finds and removes *regenerable* developer clutter — `node_modules`, `.venv`,
build caches, Docker layers, `__pycache__` — while never touching irreplaceable work, with
full explainability and one-command undo.

> 🚧 **Status: Phase 0 complete (spike & foundations).** A working parallel scanner exists —
> **byte-exact vs `du` (0-byte diff on 290K files) and ~1.57× faster**. The deterministic
> classifier/analyzer, safety/undo, and AI agent come in later phases. This README is a
> placeholder and will be expanded with usage, screenshots, and benchmarks.

## Documentation

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — the full system architecture, workflows, and design decisions.
- [`docs/`](./docs/) — product vision, differentiation, roadmap.

## Quickstart (Phase 0 spike)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# polished CLI
reclaim scan ~/Desktop/PROJECTS

# or the dependency-free runner
python -m reclaim.core.scanner ~/Desktop/PROJECTS
```

## License

MIT
