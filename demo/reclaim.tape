# Reproducible terminal demo for Reclaim, rendered with charmbracelet/vhs.
#
#   vhs demo/reclaim.tape        # → writes demo/reclaim.gif
#
# It builds a throwaway project tree and points the quarantine store at a temp dir
# (RECLAIM_HOME), so it never touches your real files. Requires `reclaim` on PATH
# (`pipx install .` or activate the venv) and `vhs` (https://github.com/charmbracelet/vhs).

Output demo/reclaim.gif

Set Shell "bash"
Set FontSize 18
Set Width 1180
Set Height 860
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

# --- hidden setup: isolated store + a fake project tree with reclaimable clutter ---
Hide
Type "export RECLAIM_HOME=$(mktemp -d)" Enter
Type "export PS1='$ '" Enter
Type "clear" Enter
Type "D=$(mktemp -d)/dev" Enter
Type "mkdir -p $D/webapp/node_modules/react/lib $D/webapp/src $D/api/.venv/lib $D/rustcli/target/debug" Enter
Type "head -c 4600000 /dev/zero > $D/webapp/node_modules/react/lib/react.js" Enter
Type "head -c 3300000 /dev/zero > $D/api/.venv/lib/big.py" Enter
Type "head -c 2200000 /dev/zero > $D/rustcli/target/debug/bin" Enter
Type "echo app > $D/webapp/src/index.js" Enter
Type "clear" Enter
Show

# --- 1. see what's reclaimable (read-only) ---
Type "reclaim status $D" Sleep 500ms Enter
Sleep 3s

# --- 2. preview a plan (never mutates) ---
Type "reclaim plan $D --include-costly" Sleep 500ms Enter
Sleep 3s

# --- 3. reclaim it: scan → gate → quarantine (undoable) ---
Type "reclaim apply $D --include-costly --yes" Sleep 500ms Enter
Sleep 3s

# --- 4. it's all reversible ---
Type "reclaim ls" Sleep 500ms Enter
Sleep 2s
Type "reclaim undo" Sleep 500ms Enter
Sleep 3s

# --- cleanup ---
Hide
Type "rm -rf $RECLAIM_HOME $(dirname $D)" Enter
Show
Sleep 1s
