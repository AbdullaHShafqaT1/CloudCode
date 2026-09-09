"""
test_ui_server.py — Local Testing UI Server
============================================

Standalone HTTP server for manual testing of the LegacyBridge + NodeInsight
integration.  Uses ONLY Python stdlib (http.server, json, urllib) — no Flask,
no new dependencies.

Endpoints
---------
GET  /              → serve test_ui.html
POST /api/reader    → BridgeAdapter.reader_call() → JSON BridgeResult
POST /api/health    → HTTP GET the given URL, return latency/status
GET  /api/validate  → verify workspace_root is readable via list_dir

Security
--------
* Server binds to 127.0.0.1 only (not network-exposed).
* workspace_root is passed verbatim to NodeInsightBridge; NodeInsight's
  _safe_path() remains the SOLE path-traversal authority.
* Path traversal attempts (../../etc/passwd) are rejected inside NodeInsight
  and propagated back as PathTraversal errors through BridgeResult.

Usage
-----
    python test_ui_server.py          # default port 7490
    python test_ui_server.py 8080     # custom port
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap — make LegacyBridge and NodeInsight importable
# ---------------------------------------------------------------------------
_THIS_DIR       = Path(__file__).resolve().parent          # CloudCode/
_LEGACYBRIDGE   = _THIS_DIR / "LegacyBridge"
_LEGACYNODE_PKG = _LEGACYBRIDGE / "legacynode"
_NODEINSIGHT    = _THIS_DIR / "NodeInsight"

for _p in [str(_LEGACYBRIDGE), str(_LEGACYNODE_PKG), str(_NODEINSIGHT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Import BridgeAdapter (existing, unchanged)
# ---------------------------------------------------------------------------
try:
    from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult
    _BRIDGE_AVAILABLE = True
except ImportError as _e:
    _BRIDGE_AVAILABLE = False
    _BRIDGE_IMPORT_ERROR = str(_e)

# ---------------------------------------------------------------------------
# HTML file location
# ---------------------------------------------------------------------------
_UI_HTML = _THIS_DIR / "test_ui.html"


# ---------------------------------------------------------------------------
# Helper: build a plain JSON error response dict
# ---------------------------------------------------------------------------

def _error_payload(message: str, detail: str = "") -> dict:
    return {
        "ok": False,
        "error": message,
        "detail": detail,
        "data": None,
        "latency_ms": 0.0,
        "timestamp": "",
    }


# ---------------------------------------------------------------------------
# Helper: call BridgeAdapter.reader_call() synchronously
# ---------------------------------------------------------------------------

def _do_reader_call(body: dict) -> dict:
    """
    Parse the request body and dispatch to BridgeAdapter.reader_call().
    Returns a plain dict suitable for JSON serialisation.
    """
    if not _BRIDGE_AVAILABLE:
        return _error_payload(
            "LegacyBridge import failed",
            _BRIDGE_IMPORT_ERROR,
        )

    workspace_root = body.get("workspace_root", "").strip()
    if not workspace_root:
        return _error_payload("workspace_root is required")

    action = body.get("action", "").strip()
    if not action:
        return _error_payload("action is required")

    # Collect action-specific kwargs — only pass keys that have a non-empty value
    _KWARG_KEYS = ("path", "query", "pattern", "start_line", "end_line",
                   "recursive", "line_numbers", "chunk_size", "offset_line",
                   "chunk_number", "chunk_index")
    kwargs: dict[str, Any] = {}
    for key in _KWARG_KEYS:
        val = body.get(key)
        if val is None or val == "":
            continue
        # Coerce numeric fields
        if key in ("start_line", "end_line", "chunk_size", "offset_line",
                   "chunk_number", "chunk_index"):
            try:
                kwargs[key] = int(val)
            except (TypeError, ValueError):
                pass
        elif key == "recursive":
            kwargs[key] = bool(val)
        elif key == "line_numbers":
            kwargs[key] = bool(val)
        else:
            kwargs[key] = val

    # Construct a fresh BridgeAdapter in LOCAL mode for every call.
    # The cloud URL is irrelevant to reader_call() — INTEGRATION.md §Switching
    # to Remote Mode explicitly states reader_call() always uses local NodeInsight.
    adapter = BridgeAdapter(
        mode=BridgeMode.LOCAL,
        workspace_root=workspace_root,
    )

    result: BridgeResult = adapter.reader_call(action, **kwargs)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Helper: test connectivity to a remote URL
# ---------------------------------------------------------------------------

def _do_health_check(body: dict) -> dict:
    url = body.get("url", "").strip()
    if not url:
        return _error_payload("url is required")

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "LegacyBridge-TestUI/1.0")
        with urllib.request.urlopen(req, timeout=8) as resp:
            status_code = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body_preview = resp.read(256).decode("utf-8", errors="replace")
        latency = round((time.monotonic() - t0) * 1000, 1)
        return {
            "ok": True,
            "status_code": status_code,
            "content_type": content_type,
            "body_preview": body_preview,
            "latency_ms": latency,
            "url": url,
            "message": f"Connected — HTTP {status_code}",
        }
    except urllib.error.HTTPError as exc:
        latency = round((time.monotonic() - t0) * 1000, 1)
        return {
            "ok": True,  # Server responded — connection itself succeeded
            "status_code": exc.code,
            "latency_ms": latency,
            "url": url,
            "message": f"HTTP {exc.code}: {exc.reason}",
            "body_preview": "",
        }
    except urllib.error.URLError as exc:
        latency = round((time.monotonic() - t0) * 1000, 1)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": latency,
            "url": url,
            "message": f"Connection failed: {exc.reason}",
            "body_preview": "",
        }
    except Exception as exc:
        latency = round((time.monotonic() - t0) * 1000, 1)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": latency,
            "url": url,
            "message": f"Error: {type(exc).__name__}: {exc}",
            "body_preview": "",
        }


# ---------------------------------------------------------------------------
# Helper: validate workspace root (calls list_dir on ".")
# ---------------------------------------------------------------------------

def _do_validate_workspace(workspace_root: str) -> dict:
    if not _BRIDGE_AVAILABLE:
        return _error_payload("LegacyBridge import failed", _BRIDGE_IMPORT_ERROR)

    workspace_root = workspace_root.strip()
    if not workspace_root:
        return _error_payload("workspace_root is required")

    adapter = BridgeAdapter(mode=BridgeMode.LOCAL, workspace_root=workspace_root)
    result: BridgeResult = adapter.reader_call("list_dir", path=".")
    payload = result.to_dict()
    # Add a resolved path for display
    try:
        payload["resolved_root"] = str(Path(workspace_root).resolve())
    except Exception:
        payload["resolved_root"] = workspace_root
    return payload


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:  # type: ignore[override]
        """Override to emit compact log lines."""
        print(f"  [{self.log_date_time_string()}] {fmt % args}")

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/":
            self._serve_html()
        elif path == "/api/validate":
            params = {}
            if "?" in self.path:
                qs = self.path.split("?", 1)[1]
                for part in qs.split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        params[urllib.parse.unquote_plus(k)] = urllib.parse.unquote_plus(v)
            workspace_root = params.get("workspace_root", "")
            self._send_json(_do_validate_workspace(workspace_root))
        else:
            self._send_json({"error": "Not found"}, status=404)

    # ── POST ─────────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        path = self.path.split("?")[0]

        body = self._read_json_body()
        if body is None:
            self._send_json(_error_payload("Invalid or missing JSON body"), status=400)
            return

        if path == "/api/reader":
            self._send_json(_do_reader_call(body))
        elif path == "/api/health":
            self._send_json(_do_health_check(body))
        else:
            self._send_json({"error": "Not found"}, status=404)

    # ── OPTIONS (CORS preflight for local dev) ────────────────────────────────

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _serve_html(self) -> None:
        try:
            html = _UI_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(html)
        except FileNotFoundError:
            msg = b"test_ui.html not found. Run test_ui_server.py from CloudCode/."
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            return json.loads(raw)
        except Exception:
            return None

    def _send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, default=str, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _add_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


# ---------------------------------------------------------------------------
# urllib.parse import (needed in GET handler)
# ---------------------------------------------------------------------------
import urllib.parse  # noqa: E402 (placed after Handler definition for clarity)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7490
    host = "127.0.0.1"

    print()
    print("=" * 60)
    print("  LegacyBridge + NodeInsight  —  Local Testing UI")
    print("=" * 60)
    print(f"  URL    : http://{host}:{port}")
    print(f"  Root   : {_THIS_DIR}")
    if _BRIDGE_AVAILABLE:
        print("  Bridge : LegacyBridge [OK]  NodeInsight [OK]")
    else:
        print(f"  Bridge : IMPORT ERROR -- {_BRIDGE_IMPORT_ERROR}")
    print()
    print("  Open the URL above in your browser.")
    print("  Press Ctrl+C to stop the server.")
    print("=" * 60)
    print()

    server = HTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
