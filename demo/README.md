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

`reclaim.gif` **is** committed — GitHub renders the README from the repository, so an
uncommitted artifact would show up as a broken image. Re-running the tape overwrites it in
place; commit the result alongside whatever CLI change made the output move.

> Teardown runs after a bare `Hide` with no matching `Show`. `Hide` only stops the recording —
> the typed command still lands in the terminal — so a `Show` afterwards would put the
> teardown `rm -rf` back on screen. That is a poor last frame for a tool whose promise is that
> it never `rm`s anything. The GIF ends on `restored 3 unit(s)`.
