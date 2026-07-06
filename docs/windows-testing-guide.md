# Reclaim — Windows Testing Guide

> **For:** a friend/tester on **Windows 10 or 11** who wants to try Reclaim safely,
> step by step, without any prior knowledge of the project.
>
> **Time:** ~10–15 minutes. **Risk:** effectively zero — this guide has you test on a
> **throwaway sandbox folder**, and Reclaim itself never hard-deletes anything (every
> "reclaim" is a reversible move you can `undo`).

---

## 0. The one thing to know before you start

Reclaim **never `rm`s your files.** When it "reclaims" a folder, it *moves* it into a
quarantine store (`~/.reclaim` by default) guarded by a write-ahead journal. `undo`
restores it byte-for-byte. It only becomes a real deletion when you explicitly run
`purge`. It also **refuses to touch any project that has uncommitted or unpushed git
work.** So you cannot lose anything by following these steps.

---

## 1. Prerequisites

You need two things installed and on your `PATH`:

| Tool | Version | Check with | Get it |
|------|---------|-----------|--------|
| **Python** | 3.10 or newer | `python --version` | <https://www.python.org/downloads/> — **tick "Add Python to PATH"** in the installer |
| **Git** | any recent | `git --version` | <https://git-scm.com/download/win> |

> ⚠️ **Git is required.** Without it, Reclaim plays it safe and marks *everything* as
> protected — the tool will look like it "does nothing." That's the fail-safe working,
> not a bug, but it makes for a boring test. Install git first.

Open a terminal. **Windows Terminal** or **PowerShell** is recommended (they render the
🟢/🟡/🔴 tier icons correctly). The old `cmd.exe` works too but may show boxes instead of
emoji — cosmetic only.

Verify both tools:

```powershell
python --version   # should print Python 3.10.x or higher
git --version      # should print git version 2.x
```

---

## 2. Get the code

Either **clone** it (if you were given the repo URL):

```powershell
cd $env:USERPROFILE
git clone <REPO_URL> Reclaim
cd Reclaim
```

…or unzip the folder you were sent and `cd` into it.

---

## 3. Create and activate a virtual environment

A venv keeps Reclaim's dependencies isolated from your system Python.

```powershell
python -m venv .venv
```

**Activate it — PowerShell:**

```powershell
.\.venv\Scripts\Activate.ps1
```

> 🛑 **If PowerShell says "running scripts is disabled on this system":** this is
> Windows' default execution policy. Fix it for your user once, then re-run activate:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

**Activate it — classic `cmd.exe` instead:**

```bat
.venv\Scripts\activate.bat
```

After activation your prompt shows `(.venv)` at the front. All the commands below assume
this is active.

---

## 4. Install Reclaim

```powershell
pip install -e ".[dev]"
```

This installs the engine plus the test tools. It does **not** need any AI/API key — the
AI chat feature is optional and separate.

---

## 5. Sanity check — run the test suite

```powershell
pytest
```

**Expected:** a row of green dots ending in something like `145 passed, 1 skipped` —
**0 failed** is the only thing that matters.

✅ If this passes, the whole engine works correctly on your machine. If anything fails
here, stop and copy the output back — that's the most important signal.

---

## 6. Make a safe sandbox to test on

So you never touch your real projects, create a throwaway folder with a fake project that
has a chunky `node_modules` inside it. Paste this whole block into **PowerShell**:

```powershell
# a throwaway test area under your user folder
$sb = "$env:USERPROFILE\reclaim-test"
Remove-Item -Recurse -Force $sb -ErrorAction SilentlyContinue

# a fake node project: package.json marks it a project, but it's NOT under git,
# so Reclaim treats its node_modules as safely reclaimable.
New-Item -ItemType Directory -Force "$sb\demo-project\node_modules\big-pkg" | Out-Null
New-Item -ItemType Directory -Force "$sb\demo-project\src" | Out-Null
New-Item -ItemType Directory -Force "$sb\demo-project\__pycache__" | Out-Null
'{ "name": "demo" }'   | Out-File -Encoding utf8 "$sb\demo-project\package.json"
"console.log('hi')"    | Out-File -Encoding utf8 "$sb\demo-project\src\index.js"

# a ~50 MB junk file inside node_modules so there's real space to reclaim
fsutil file createnew "$sb\demo-project\node_modules\big-pkg\blob.bin" 50000000

# keep Reclaim's quarantine store inside the sandbox too, so cleanup is one delete
# and everything stays on the same drive (the fast, atomic path).
$env:RECLAIM_HOME = "$sb\.reclaim"

Write-Host "Sandbox ready at $sb" -ForegroundColor Green
```

> 💡 `$env:RECLAIM_HOME` only lasts for this terminal window. That's intentional — it
> keeps the whole test self-contained. Everything Reclaim does will live under
> `%USERPROFILE%\reclaim-test`, which you can delete in one go at the end.

---

## 7. The guided walkthrough

Run these in order. After each, compare against **"Expected."**

### 7.1 `status` — read-only inventory (touches nothing)

```powershell
reclaim status "$sb\demo-project"
```

**Expected:** a report showing ~50 MB reclaimable, a 🟢 **Regenerable** row for
`node_modules`, and a project table listing `demo-project` (type `node`, git `—`/no-git).
Nothing on disk changes.

### 7.2 `plan` — preview what *would* happen (still touches nothing)

```powershell
reclaim plan "$sb\demo-project"
```

**Expected:** a plan table — "reclaim ~50 MB across 1 unit(s)" with the `node_modules`
path and its rebuild command (`npm install`). No files move.

### 7.3 `apply` — actually reclaim (reversible)

```powershell
reclaim apply "$sb\demo-project"
```

**Expected:** it prints the plan and asks **"Proceed? [y/N]"**. Type `y` and press Enter.
It then prints `✓ reclaimed ~50 MB … — op 20xxxxxx-xxxxxx-xxxxxx` and tells you how to undo.

> Prefer no prompt? Add `--yes` (or `-y`) to skip the confirmation.

### 7.4 Confirm the folder actually moved

```powershell
Test-Path "$sb\demo-project\node_modules"
```

**Expected:** `False` — `node_modules` is gone from the project (it's now safely in
quarantine, not deleted).

### 7.5 `ls` — see the quarantined operation

```powershell
reclaim ls
```

**Expected:** one row: an op-id, state `committed`, freed `~50 MB`, `1` unit, age `0.0d`.

### 7.6 `undo` — restore it, byte-for-byte

```powershell
reclaim undo
```

**Expected:** `✓ restored 1 unit(s) …`. (`undo` with no id restores the most recent op.)

### 7.7 Confirm it came back

```powershell
Test-Path "$sb\demo-project\node_modules\big-pkg\blob.bin"
(Get-Item "$sb\demo-project\node_modules\big-pkg\blob.bin").Length
```

**Expected:** `True`, and the length is `50000000` — restored exactly as it was.

### 7.8 (Optional) `purge` — the only real deletion

Reclaim again, then permanently free it (this step is **not** undoable):

```powershell
reclaim apply "$sb\demo-project" --yes
reclaim purge --older-than 0     # 0 = purge everything, ignoring the age limit
reclaim ls                       # should now be empty
```

**Expected:** `purge` reports the op freed; `ls` shows the quarantine is empty.

---

## 8. (Optional) Try the safety guarantee yourself

Prove that Reclaim refuses to touch work-in-progress. Turn the demo into a git repo with
uncommitted changes, then scan it:

```powershell
cd "$sb\demo-project"
git init -q
reclaim status .
```

**Expected:** the `node_modules` now shows up under 🔴 **Protected** — "enclosing project
has uncommitted/unpushed work." A `plan` or `apply` here reclaims **nothing**. That's the
core safety promise in action.

---

## 9. Testing on your real projects (the actual goal)

The sandbox above proves the mechanism works on *your* machine. Do that first — it's your
insurance. Once it passes, you **can** point Reclaim at real project folders. The undo
safety net is genuine: until you `purge`, every reclaim is a reversible move, not a delete.

Follow this protocol and nothing gets lost.

### Golden rules

1. **Never run `purge`, and never delete the `%USERPROFILE%\.reclaim` folder while
   testing.** That store is *what makes `undo` possible.* As long as it's intact, every
   reclaim is reversible. `purge` is the **only** command that truly deletes.
2. **Look before you leap.** Always run `status` then `plan` (both **read-only**, they
   never touch disk) and actually read the plan. Only `apply` on paths you recognize.
3. **Don't use `--yes` on real files.** Let `apply` print the plan and confirm each time —
   type `y` only if the table looks right.

### Recommended flow

Use a **normal terminal** (do *not* set `$env:RECLAIM_HOME` here — you want the real
default store at `%USERPROFILE%\.reclaim`):

```powershell
# 1. See what's reclaimable — READ ONLY, changes nothing
reclaim status "$env:USERPROFILE\dev"

# 2. Preview a plan — READ ONLY, never mutates
reclaim plan "$env:USERPROFILE\dev" --kind node_modules

# 3. Reclaim, reviewing the plan first (note: NO --yes)
reclaim apply "$env:USERPROFILE\dev" --kind node_modules
#    → read the table, type y only if it looks right

# 4. Something unexpected? Undo it instantly:
reclaim undo
```

Keep the first real run tight and predictable with flags:

| Flag | Effect |
|------|--------|
| `--kind node_modules` | only `node_modules` (skip caches, venvs, build dirs) |
| `--min-size 100M` | only large units, ignore small fry |
| `--dormant-only` | only projects untouched for a while |
| point at one project folder | e.g. `...\dev\some-app` instead of the whole home dir |

### What is already protected for you (you don't have to worry about these)

- **Any project with uncommitted or unpushed git work** → automatically skipped (🔴).
- **Secrets & data** — `.env`, `*.key`, `*.pem`, `*.sqlite`, `data/`, `backups/`, … → skipped.
- **Anything unknown** — if a folder isn't on the known-regenerable list, it's left alone.

### The one honest caveat about "we'll just revert it"

`undo` restores *from the quarantine store.* So the revert guarantee holds **as long as you
haven't `purge`d and haven't deleted `~/.reclaim`.** One nuance: if after reclaiming a
`node_modules` you already ran `npm install` and recreated it, `undo` will **skip** that
item (it refuses to overwrite the new copy) — which is harmless, because the folder was
regenerated anyway. That's the entire point: these folders are rebuildable, so "revert"
means either the original comes back or a fresh copy already exists.

---

## 10. Clean up

One command removes everything this guide created:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\reclaim-test"
```

(Your real projects were never touched.)

---

## 11. Windows-specific notes & gotchas

| Situation | What happens | What to do |
|-----------|--------------|------------|
| **A folder is open** in Explorer / VS Code / a running `node` or dev server | Windows locks files in use, so the move may fail. Reclaim **rolls back safely** — nothing is lost, but that unit won't be reclaimed. | Close editors/terminals/servers pointing into the target folder, then retry. |
| **Project and store on different drives** (e.g. project on `D:`, `~/.reclaim` on `C:`) | Reclaim falls back from an instant rename to copy → verify → delete. Slower, but still safe. | For the smoothest test, keep the sandbox on `C:` as in step 6. |
| **`cmd.exe` shows boxes** instead of 🟢🟡🔴 | Old console can't render emoji. | Cosmetic only — use **Windows Terminal** or **PowerShell** for the icons. |
| **`git` not found** | Every project is marked protected; nothing reclaims. | Install Git (step 1) and reopen the terminal. |
| **`reclaim` not recognized** | The venv isn't active. | Re-run the activate command from step 3; look for `(.venv)` in the prompt. |

---

## 12. What to report back

Please send back:

1. **Test suite result** — the last line of `pytest` (e.g. `136 passed`), or the full
   output if anything failed.
2. **`python --version`** and **`git --version`** on your machine.
3. Whether the **walkthrough (steps 7.1–7.7)** each matched the "Expected" — and a
   copy-paste of any command where it didn't.
4. Any **error text** (copy the whole thing), and what you were doing when it appeared.
5. Anything that **looked confusing or ugly** in the output — wording, layout, emoji,
   colors.

A quick template you can fill in:

```
OS:            Windows 10 / 11
Python:        3.xx
Git:           2.xx
pytest:        ___ passed / failed
Walkthrough:   all matched? yes / no (which step differed: ___)
Errors seen:   none / (paste)
UX notes:      ___
```

Thanks for testing! 🙏
