# Demo

The animated demo in the root README is generated **reproducibly** from a checked-in script —
no hand-recorded GIF that drifts out of date.

## Regenerate the GIF

1. Install [`vhs`](https://github.com/charmbracelet/vhs) (`brew install vhs`).
2. Make `reclaim` available on your PATH — either `pipx install .` from the repo root, or
   activate the dev venv (`source .venv/bin/activate` after `pip install -e ".[dev]"`).
3. From the repo root:

   ```bash
   vhs demo/reclaim.tape        # writes demo/reclaim.gif
   ```

The tape ([`reclaim.tape`](./reclaim.tape)) builds a throwaway project tree in a temp dir and
points the quarantine store at another temp dir (`RECLAIM_HOME`), so **it never touches your
real files**. It walks the core loop: `status` → `plan` → `apply` → `ls` → `undo`.

> The rendered `reclaim.gif` is intentionally not committed (it's a build artifact). Generate
> it locally, or drop your own recording in as `demo/reclaim.gif` and it will appear in the
> README.
