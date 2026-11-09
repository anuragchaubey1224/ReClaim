"""Local browser interface for Reclaim.

The web server is deliberately local-only and dependency-free. It exposes the same
deterministic engine used by the CLI; the browser is only another thin interface.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from reclaim.core.classifier import classify_scan
from reclaim.core.config import build_ruleset, load_config
from reclaim.core.model import Plan
from reclaim.core.preferences import PreferenceStore
from reclaim.core.quarantine import QuarantineStore
from reclaim.core.scanner import Scanner
from reclaim.safety.gate import SafetyGate


_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reclaim</title>
<style>
:root { color-scheme: dark; font-family: system-ui, sans-serif; }
body { max-width: 1180px; margin: 0 auto; padding: 24px; background: #111827; color: #e5e7eb; }
button, input { font: inherit; border-radius: 6px; border: 1px solid #4b5563; padding: 8px 12px; }
button { cursor: pointer; background: #2563eb; color: white; border: 0; }
button.secondary { background: #374151; } button.danger { background: #b91c1c; }
header, .toolbar, .cards { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
header { justify-content: space-between; } input { min-width: 360px; background: #1f2937; color: white; }
.cards { margin: 20px 0; } .card { background: #1f2937; padding: 14px 18px; border-radius: 8px; min-width: 150px; }
.card strong { display: block; font-size: 1.35rem; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; background: #1f2937; }
th, td { text-align: left; padding: 10px; border-bottom: 1px solid #374151; }
th { color: #9ca3af; } .muted { color: #9ca3af; } .green { color: #4ade80; } .yellow { color: #facc15; }
#status { min-height: 24px; margin: 14px 0; } .chat { margin-top: 24px; background: #1f2937; padding: 16px; border-radius: 8px; }
</style>
</head>
<body>
<header><div><h1>Reclaim</h1><div class="muted">Local safety-first disk cleanup</div></div>
<div><button class="secondary" onclick="scan()">Refresh scan</button>
<button class="secondary" onclick="undo()">Undo latest</button></div></header>
<div class="toolbar"><input id="root" value="" placeholder="Directory to scan">
<button class="secondary" onclick="chooseFolder()">Choose folder</button>
<button onclick="scan()">Scan</button><button onclick="plan()">Preview plan</button>
<button class="danger" onclick="applyPlan()">Apply selected</button></div>
<div id="status"></div><div class="cards" id="summary"></div>
<table><thead><tr><th><input type="checkbox" onchange="toggleAll(this)"></th><th>Kind</th><th>Path</th><th>Size</th><th>Tier</th><th>Why</th></tr></thead>
<tbody id="items"></tbody></table>
<div class="chat"><strong>Chat</strong><p class="muted">The safe browser controls are enabled now. AI chat will use the same grounded proposal flow in a follow-up web integration.</p></div>
<script>
let data = {items: []};
const $ = id => document.getElementById(id);
function message(text, error=false) { $("status").textContent = text; $("status").style.color = error ? "#f87171" : "#86efac"; }
async function request(path, options={}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed");
  return body;
}
function selected() { return [...document.querySelectorAll("input[data-path]:checked")].map(x => x.dataset.path); }
function toggleAll(box) { document.querySelectorAll("input[data-path]").forEach(x => x.checked = box.checked); }
function render() {
  $("summary").innerHTML = `<div class="card">Reclaimable<strong>${data.reclaimable_human}</strong></div>
    <div class="card">Units<strong>${data.items.length}</strong></div><div class="card">Files<strong>${data.file_count}</strong></div>`;
  $("items").innerHTML = data.items.map((x, i) => `<tr><td><input data-path="${escapeHtml(x.path)}" type="checkbox" checked></td>
    <td>${escapeHtml(x.kind)}</td><td title="${escapeHtml(x.path)}">${escapeHtml(x.display_path)}</td>
    <td>${escapeHtml(x.size_human)}</td><td class="${x.tier === "green" ? "green" : "yellow"}">${escapeHtml(x.tier)}</td>
    <td>${escapeHtml(x.reason || "")}</td></tr>`).join("");
}
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function scan() {
  try { const root = $("root").value || "."; message("Scanning…");
    data = await request("/api/scan?root=" + encodeURIComponent(root)); render();
    message("Scan complete."); $("root").value = data.root;
  } catch (e) { message(e.message, true); }
}
async function chooseFolder() {
  try {
    message("Opening folder picker…");
    const result = await request("/api/pick-folder", {method:"POST", body:"{}"});
    if (result.cancelled) { message("Folder selection cancelled."); return; }
    $("root").value = result.root;
    data = result.data; render();
    message("Scan complete.");
  } catch (e) { message(e.message, true); }
}
async function plan() {
  try { const result = await request("/api/plan", {method:"POST", body:JSON.stringify({paths:selected()})});
    message(`Plan: ${result.item_count} unit(s), ${result.total_human}. Nothing has been moved.`);
  } catch (e) { message(e.message, true); }
}
async function applyPlan() {
  const paths = selected(); if (!paths.length) return message("Select at least one unit.", true);
  if (!confirm("Move the selected units to quarantine? You can undo this later.")) return;
  try { const result = await request("/api/apply", {method:"POST", body:JSON.stringify({paths})});
    message(`Reclaimed ${result.total_human}. Operation: ${result.op_id}`); await scan();
  } catch (e) { message(e.message, true); }
}
async function undo() {
  if (!confirm("Undo the latest reclaim operation?")) return;
  try { const result = await request("/api/undo", {method:"POST", body:"{}"}); message(result.message); await scan(); }
  catch (e) { message(e.message, true); }
}
scan();
</script>
</body></html>"""


def _human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class _WebState:
    def __init__(self, root: Path, store: QuarantineStore, prefs: PreferenceStore,
                 ruleset) -> None:
        self.root = root
        self.store = store
        self.prefs = prefs
        self.ruleset = ruleset
        self.result = None
        self.lock = threading.Lock()

    def scan(self, root: Path | None = None):
        target = root or self.root
        if not target.exists() or not target.is_dir():
            raise ValueError(f"scan path is not a directory: {target}")
        raw = Scanner(ruleset=self.ruleset).scan(target)
        result = classify_scan(raw, preferences=self.prefs, ruleset=self.ruleset)
        with self.lock:
            self.root, self.result = target, result
        return result

    def candidates_for(self, paths: list[str]):
        with self.lock:
            result = self.result
        if result is None:
            result = self.scan()
        wanted = {Path(p).expanduser().resolve() for p in paths}
        return [c for c in result.candidates if c.path.resolve() in wanted and c.is_reclaimable]


def _pick_directory() -> Path | None:
    """Open a native local folder picker without exposing arbitrary command execution."""
    if sys.platform == "darwin":
        script = (
            'try\n'
            'set chosenFolder to choose folder with prompt "Choose a folder for Reclaim to scan"\n'
            'POSIX path of chosenFolder\n'
            'on error number -128\n'
            'return ""\n'
            'end try'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "macOS folder picker could not be opened; enter the folder path manually"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown macOS dialog error"
            raise RuntimeError(f"folder picker failed: {detail}")
        selected = result.stdout.strip()
        return Path(selected).expanduser() if selected else None

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Choose a folder for Reclaim to scan")
        root.destroy()
        return Path(selected).expanduser() if selected else None
    except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError(
            "native folder picker is unavailable; enter the folder path manually"
        ) from exc


def _item(c) -> dict[str, Any]:
    return {
        "path": str(c.path), "display_path": str(c.path),
        "kind": c.kind, "size_bytes": c.size_allocated,
        "size_human": _human_bytes(c.size_allocated),
        "tier": c.tier.value, "reason": c.reason,
    }


def _payload(state: _WebState, result) -> dict[str, Any]:
    items = [_item(c) for c in result.candidates if c.is_reclaimable]
    return {"root": str(state.root), "reclaimable_bytes": result.reclaimable_allocated,
            "reclaimable_human": _human_bytes(result.reclaimable_allocated),
            "file_count": result.file_count, "items": items}


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: HTTPStatus, body: dict[str, Any] | str,
              content_type: str = "application/json") -> None:
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    @property
    def state(self) -> _WebState:
        return self.server.state

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(HTTPStatus.OK, _HTML, "text/html")
            return
        if parsed.path == "/api/scan":
            try:
                root = Path(parse_qs(parsed.query).get("root", [str(self.state.root)])[0]).expanduser()
                result = self.state.scan(root)
                self._send(HTTPStatus.OK, _payload(self.state, result))
            except (OSError, ValueError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            body = self._json_body()
            if self.path == "/api/scan":
                result = self.state.scan(Path(str(body.get("root", self.state.root))).expanduser())
                self._send(HTTPStatus.OK, _payload(self.state, result))
            elif self.path == "/api/pick-folder":
                selected = _pick_directory()
                if selected is None:
                    self._send(HTTPStatus.OK, {"cancelled": True})
                    return
                result = self.state.scan(selected)
                self._send(HTTPStatus.OK, {"cancelled": False, "root": str(selected),
                                           "data": _payload(self.state, result)})
            elif self.path == "/api/plan":
                candidates = self.state.candidates_for(self._paths(body))
                plan = Plan.from_candidates(candidates)
                self._send(HTTPStatus.OK, {"item_count": len(plan.operations),
                                           "total_bytes": plan.total_bytes,
                                           "total_human": _human_bytes(plan.total_bytes)})
            elif self.path == "/api/apply":
                candidates = self.state.candidates_for(self._paths(body))
                plan = Plan.from_candidates(candidates)
                gate = SafetyGate(preferences=self.state.prefs, ruleset=self.state.ruleset).validate(plan)
                if gate.rejected:
                    reasons = "; ".join(r.reason for r in gate.rejected)
                    self._send(HTTPStatus.CONFLICT, {"error": f"Safety Gate rejected items: {reasons}"})
                    return
                tx = self.state.store.apply(gate.approved)
                self._send(HTTPStatus.OK, {"op_id": tx.op_id, "total_bytes": tx.freed_bytes,
                                           "total_human": _human_bytes(tx.freed_bytes)})
            elif self.path == "/api/undo":
                ops = [x for x in self.state.store.list_ops() if x.state.value == "committed"]
                if not ops:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "nothing to undo"})
                    return
                restored = self.state.store.undo(ops[-1].op_id)
                self._send(HTTPStatus.OK, {"message": f"Restored {len(restored.restored)} unit(s)."})
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (OSError, ValueError, RuntimeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    @staticmethod
    def _paths(body: dict[str, Any]) -> list[str]:
        paths = body.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError("paths must be a list of strings")
        return paths


class _Server(ThreadingHTTPServer):
    def __init__(self, address, state: _WebState):
        super().__init__(address, _Handler)
        self.state = state


def serve(root: Path, home: Path, port: int = 0, open_browser: bool = True) -> None:
    """Run the local web UI until Ctrl-C."""
    config = load_config(home / "config.toml")
    ruleset = build_ruleset(config)
    state = _WebState(root, QuarantineStore(home=home), PreferenceStore(home / "preferences.json"),
                      ruleset)
    state.scan()
    server = _Server(("127.0.0.1", port), state)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Reclaim web UI: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
