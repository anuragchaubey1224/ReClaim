# ReClaim User Manual

ReClaim is a command-line tool for finding reclaimable developer files and safely moving selected
items to quarantine. It is distributed on PyPI as `reclaim-disk`; the command is `reclaim`.

## Requirements

- Python 3.10 or newer
- macOS or Linux (the project's CI-tested platforms)
- Windows is not currently CI-verified

## Option 1: Install directly from PyPI

We recommend `pipx`, which installs command-line apps in an isolated environment:

```bash
pipx install reclaim-disk
reclaim --help
```

If `pipx` is not installed, see the [pipx installation guide](https://pipx.pypa.io/stable/installation/).
Alternatively, install into a Python virtual environment:

```bash
python3 -m venv .venv-reclaim
source .venv-reclaim/bin/activate
python -m pip install reclaim-disk
reclaim --help
```

## Option 2: Developer install from source

Clone the repository, create a virtual environment, and install the project in editable mode:

```bash
git clone https://github.com/anuragchaubey1224/ReClaim.git
cd ReClaim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Editable mode runs the code from this checkout, so local code changes are picked up immediately.
Run the test suite with:

```bash
pytest
```

## First use

Start with a read-only scan, then inspect a plan before applying it:

```bash
reclaim status ~/dev
reclaim plan ~/dev --free 20G
reclaim apply ~/dev --free 20G
```

`apply` asks for confirmation by default and moves selected items to quarantine. Review the plan
before confirming. The `--include-costly` option also considers items that take longer to
regenerate.

## Undo and permanent deletion

```bash
reclaim ls
reclaim undo                 # restore the latest operation
reclaim undo <operation-id>  # restore a specific operation
```

Quarantined items can be permanently deleted after a retention period:

```bash
reclaim purge --older-than 7
```

`purge` is the permanent-delete command; do not run it if you may need to undo those items.
Quarantine data is stored under `~/.reclaim` by default. Set `RECLAIM_HOME` to use another
location.

## More commands

```bash
reclaim web ~/dev       # open the local browser interface
reclaim config --help   # configuration commands
reclaim chat --help     # optional AI assistant
```

The AI assistant is optional; core scanning and cleanup work without it. Install the AI extra with
`pipx install "reclaim-disk[ai]"` or, from the source checkout,
`python -m pip install -e ".[dev,ai]"`. Provider configuration may also be required.

Use `reclaim <command> --help` for command-specific options. See the
[README](README.md) and [documentation](docs/README.md) for additional details.
