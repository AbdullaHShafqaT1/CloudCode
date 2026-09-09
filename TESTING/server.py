"""
TESTING/server.py — NodeLog-Integrated Testing UI Server
=========================================================

Self-contained HTTP server for manual + automated testing of the
LegacyBridge + NodeInsight integration, with NodeLog telemetry wired in.

Every bridge request and health-check is instrumented with NodeLog.emit()
so all activity flows through the in-memory event buffer and can be
inspected via the /api/nodelog/* endpoints or the NodeLog UI page.

Endpoints
---------
GET  /                        → serve index.html  (main dashboard)
GET  /nodelog                 → serve nodelog_ui.html  (NodeLog dashboard)
POST /api/reader              → BridgeAdapter.reader_call() → JSON + NodeLog logged
POST /api/health              → HTTP connectivity check  → JSON + NodeLog logged
GET  /api/validate            → workspace validation      → JSON + NodeLog logged
GET  /api/nodelog/events      → query NodeLog buffer (?level=, ?type=, ?source=, ?limit=)
POST /api/nodelog/emit        → emit a test event via JSON body
GET  /api/nodelog/stats       → buffer size, suppressed count, event level breakdown
DELETE /api/nodelog/clear     → clear the in-memory ring buffer (resets seq_id)

Usage
-----
    python TESTING/server.py          # default port 7491
    python TESTING/server.py 8080     # custom port

Run from the CloudCode/ project root.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrap — make all sibling packages importable
# ---------------------------------------------------------------------------
_TESTING_DIR    = Path(__file__).resolve().parent          # CloudCode/TESTING/
_ROOT           = _TESTING_DIR.parent                       # CloudCode/
_LEGACYBRIDGE   = _ROOT / "LegacyBridge"
_LEGACYNODE_PKG = _LEGACYBRIDGE / "legacynode"
_NODEINSIGHT    = _ROOT / "NodeInsight"
_NODELOG        = _ROOT / "NodeLog"

for _p in [str(_LEGACYBRIDGE), str(_LEGACYNODE_PKG), str(_NODEINSIGHT), str(_NODELOG)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Import NodeLog (required — server cannot start without it)
# ---------------------------------------------------------------------------
try:
    from nodelog import NodeLog, EventType, StatusLevel
    _NODELOG_AVAILABLE = True
    _NODELOG_ERROR = ""
except ImportError as _e:
    _NODELOG_AVAILABLE = False
    _NODELOG_ERROR = str(_e)
    # Provide a no-op stub so the rest of the module can still run
    class _NoOpNodeLog:  # type: ignore
        def emit(self, **kwargs): return None
        def query(self, **kwargs): return []
        def suppressed_count(self): return 0
    NodeLog = _NoOpNodeLog  # type: ignore
    EventType = type("EventType", (), {"SYSTEM": "SYSTEM", "TOOL_EXECUTION": "TOOL_EXECUTION",
                                        "PHASE_COMPLETION": "PHASE_COMPLETION", "HEARTBEAT": "HEARTBEAT",
                                        "USER_MESSAGE": "USER_MESSAGE", "TELEMETRY": "TELEMETRY"})  # type: ignore
    StatusLevel = type("StatusLevel", (), {"DEBUG": "DEBUG", "INFO": "INFO", "WARN": "WARN",
                                            "ERROR": "ERROR", "SUCCESS": "SUCCESS", "CRITICAL": "CRITICAL"})  # type: ignore

# ---------------------------------------------------------------------------
# Import BridgeAdapter (optional — reader endpoints degrade gracefully)
# ---------------------------------------------------------------------------
try:
    from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult
    _BRIDGE_AVAILABLE = True
    _BRIDGE_IMPORT_ERROR = ""
except ImportError as _e:
    _BRIDGE_AVAILABLE = False
    _BRIDGE_IMPORT_ERROR = str(_e)

# ---------------------------------------------------------------------------
# Global NodeLog instance  (single shared instance for this server process)
# ---------------------------------------------------------------------------
_LOG_DIR = str(_TESTING_DIR / "nodelog_data")
os.makedirs(_LOG_DIR, exist_ok=True)

nodelog: NodeLog = NodeLog(  # type: ignore
    log_dir=_LOG_DIR,
    maxlen=2000,
    min_level="DEBUG",
    enable_sanitization=True,
    enable_console=False,
    auto_flush_disk=False,
)

# ---------------------------------------------------------------------------
# Helpers: emit convenience wrappers
# ---------------------------------------------------------------------------

def _emit(
    source: str,
    event_type: str,
    level: str,
    message: str,
    payload: Optional[Dict[str, Any]] = None,
    phase: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Emit a NodeLog event, silently ignoring failures."""
    try:
        nodelog.emit(
            source=source,
            event_type=event_type,
            status_level=level,
            message=message,
            payload=payload,
            phase=phase,
            trace_id=trace_id,
        )
    except Exception:
        pass


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
# Business logic: reader call
# ---------------------------------------------------------------------------

def _do_reader_call(body: dict) -> dict:
    if not _BRIDGE_AVAILABLE:
        _emit("server", "SYSTEM", "ERROR",
              f"LegacyBridge import failed: {_BRIDGE_IMPORT_ERROR}",
              {"error": _BRIDGE_IMPORT_ERROR}, phase="reader_call")
        return _error_payload("LegacyBridge import failed", _BRIDGE_IMPORT_ERROR)

    workspace_root = body.get("workspace_root", "").strip()
    if not workspace_root:
        return _error_payload("workspace_root is required")

    action = body.get("action", "").strip()
    if not action:
        return _error_payload("action is required")

    _KWARG_KEYS = ("path", "query", "pattern", "start_line", "end_line",
                   "recursive", "line_numbers", "chunk_size", "offset_line",
                   "chunk_number", "chunk_index")
    kwargs: dict = {}
    for key in _KWARG_KEYS:
        val = body.get(key)
        if val is None or val == "":
            continue
        if key in ("start_line", "end_line", "chunk_size", "offset_line",
                   "chunk_number", "chunk_index"):
            try:
                kwargs[key] = int(val)
            except (TypeError, ValueError):
                pass
        elif key in ("recursive", "line_numbers"):
            kwargs[key] = bool(val)
        else:
            kwargs[key] = val

    trace_id = f"reader-{int(time.monotonic() * 1000) % 1_000_000:06d}"

    _emit("BridgeAdapter", "TOOL_EXECUTION", "INFO",
          f"reader_call: action={action!r} workspace={workspace_root!r}",
          {"action": action, "kwargs": {k: str(v) for k, v in kwargs.items()},
           "workspace_root": workspace_root},
          phase="request", trace_id=trace_id)

    t0 = time.monotonic()
    try:
        adapter = BridgeAdapter(mode=BridgeMode.LOCAL, workspace_root=workspace_root)
        result: BridgeResult = adapter.reader_call(action, **kwargs)
        latency = round((time.monotonic() - t0) * 1000, 1)
        payload_dict = result.to_dict()

        level = "SUCCESS" if result.ok else "ERROR"
        _emit("BridgeAdapter", "TOOL_EXECUTION", level,
              f"reader_call result: ok={result.ok} latency={latency}ms action={action!r}",
              {"ok": result.ok, "latency_ms": latency,
               "error": result.error, "action": action},
              phase="response", trace_id=trace_id)
        return payload_dict

    except Exception as exc:
        latency = round((time.monotonic() - t0) * 1000, 1)
        tb = traceback.format_exc()
        _emit("BridgeAdapter", "SYSTEM", "CRITICAL",
              f"Unhandled exception in reader_call: {exc}",
              {"exception": str(exc), "traceback": tb, "action": action},
              phase="error", trace_id=trace_id)
        return _error_payload(f"{type(exc).__name__}: {exc}", tb)


# ---------------------------------------------------------------------------
# Business logic: health check
# ---------------------------------------------------------------------------

def _do_health_check(body: dict) -> dict:
    url = body.get("url", "").strip()
    if not url:
        return _error_payload("url is required")

    trace_id = f"health-{int(time.monotonic() * 1000) % 1_000_000:06d}"
    _emit("HealthCheck", "HEARTBEAT", "INFO",
          f"Connectivity check → {url}",
          {"url": url}, phase="request", trace_id=trace_id)

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "LegacyBridge-TestUI/1.0")
        with urllib.request.urlopen(req, timeout=8) as resp:
            status_code = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body_preview = resp.read(256).decode("utf-8", errors="replace")
        latency = round((time.monotonic() - t0) * 1000, 1)
        _emit("HealthCheck", "HEARTBEAT", "SUCCESS",
              f"HTTP {status_code} from {url} in {latency}ms",
              {"url": url, "status_code": status_code, "latency_ms": latency},
              phase="response", trace_id=trace_id)
        return {
            "ok": True, "status_code": status_code, "content_type": content_type,
            "body_preview": body_preview, "latency_ms": latency, "url": url,
            "message": f"Connected — HTTP {status_code}",
        }
    except urllib.error.HTTPError as exc:
        latency = round((time.monotonic() - t0) * 1000, 1)
        _emit("HealthCheck", "HEARTBEAT", "WARN",
              f"HTTP {exc.code} from {url} in {latency}ms",
              {"url": url, "status_code": exc.code, "latency_ms": latency},
              phase="response", trace_id=trace_id)
        return {
            "ok": True, "status_code": exc.code, "latency_ms": latency,
            "url": url, "message": f"HTTP {exc.code}: {exc.reason}", "body_preview": "",
        }
    except urllib.error.URLError as exc:
        latency = round((time.monotonic() - t0) * 1000, 1)
        _emit("HealthCheck", "HEARTBEAT", "ERROR",
              f"Connection failed to {url}: {exc.reason}",
              {"url": url, "error": str(exc.reason), "latency_ms": latency},
              phase="response", trace_id=trace_id)
        return {
            "ok": False, "status_code": None, "latency_ms": latency,
            "url": url, "message": f"Connection failed: {exc.reason}", "body_preview": "",
        }
    except Exception as exc:
        latency = round((time.monotonic() - t0) * 1000, 1)
        _emit("HealthCheck", "HEARTBEAT", "ERROR",
              f"Error reaching {url}: {exc}",
              {"url": url, "error": str(exc), "latency_ms": latency},
              phase="response", trace_id=trace_id)
        return {
            "ok": False, "status_code": None, "latency_ms": latency,
            "url": url, "message": f"Error: {type(exc).__name__}: {exc}", "body_preview": "",
        }


# ---------------------------------------------------------------------------
# Business logic: validate workspace
# ---------------------------------------------------------------------------

def _do_validate_workspace(workspace_root: str) -> dict:
    if not _BRIDGE_AVAILABLE:
        return _error_payload("LegacyBridge import failed", _BRIDGE_IMPORT_ERROR)

    workspace_root = workspace_root.strip()
    if not workspace_root:
        return _error_payload("workspace_root is required")

    trace_id = f"ws-{int(time.monotonic() * 1000) % 1_000_000:06d}"
    _emit("WorkspaceValidator", "SYSTEM", "INFO",
          f"Validating workspace: {workspace_root}",
          {"workspace_root": workspace_root}, phase="validate", trace_id=trace_id)

    try:
        adapter = BridgeAdapter(mode=BridgeMode.LOCAL, workspace_root=workspace_root)
        result: BridgeResult = adapter.reader_call("list_dir", path=".")
        payload = result.to_dict()
        try:
            payload["resolved_root"] = str(Path(workspace_root).resolve())
        except Exception:
            payload["resolved_root"] = workspace_root

        level = "SUCCESS" if result.ok else "WARN"
        _emit("WorkspaceValidator", "SYSTEM", level,
              f"Workspace validation {'ok' if result.ok else 'failed'}: {workspace_root}",
              {"ok": result.ok, "resolved": payload.get("resolved_root")},
              phase="validate", trace_id=trace_id)
        return payload
    except Exception as exc:
        _emit("WorkspaceValidator", "SYSTEM", "ERROR",
              f"Workspace validation exception: {exc}",
              {"workspace_root": workspace_root, "error": str(exc)},
              phase="validate", trace_id=trace_id)
        return _error_payload(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Business logic: NodeLog API endpoints
# ---------------------------------------------------------------------------

def _nodelog_events(params: dict) -> dict:
    """Query the NodeLog in-memory buffer with optional filters."""
    level_filter = params.get("level", "").strip().upper()
    type_filter  = params.get("type", "").strip().upper()
    source_filter = params.get("source", "").strip()
    try:
        limit = max(1, min(int(params.get("limit", "200")), 2000))
    except (ValueError, TypeError):
        limit = 200
    try:
        offset = max(0, int(params.get("offset", "0")))
    except (ValueError, TypeError):
        offset = 0

    levels  = [level_filter]  if level_filter  else None
    types   = [type_filter]   if type_filter   else None
    sources = [source_filter] if source_filter else None

    try:
        records = nodelog.query(
            status_levels=levels,
            event_types=types,
            sources=sources,
            limit=limit,
            offset=offset,
        )
        return {
            "ok": True,
            "count": len(records),
            "offset": offset,
            "limit": limit,
            "events": [r.to_dict() for r in records],
        }
    except Exception as exc:
        return _error_payload(f"NodeLog query failed: {exc}")


def _nodelog_emit(body: dict) -> dict:
    """Emit a manual test event via the API."""
    source  = body.get("source", "ManualTestUI").strip() or "ManualTestUI"
    etype   = body.get("event_type", "SYSTEM").strip().upper() or "SYSTEM"
    level   = body.get("status_level", "INFO").strip().upper() or "INFO"
    message = body.get("message", "").strip()
    phase   = body.get("phase", None)
    trace_id = body.get("trace_id", None)

    raw_payload = body.get("payload", None)
    payload: Optional[Dict[str, Any]] = None
    if raw_payload:
        if isinstance(raw_payload, dict):
            payload = raw_payload
        elif isinstance(raw_payload, str):
            try:
                payload = json.loads(raw_payload)
            except Exception:
                payload = {"raw": raw_payload}

    if not message:
        return _error_payload("message is required")

    record = nodelog.emit(
        source=source,
        event_type=etype,
        status_level=level,
        message=message,
        payload=payload,
        phase=phase or None,
        trace_id=trace_id or None,
    )
    if record is None:
        return {"ok": False, "error": "Event was suppressed by min_level filter", "event": None}
    return {"ok": True, "event": record.to_dict()}


def _nodelog_stats() -> dict:
    """Return NodeLog statistics."""
    try:
        # Fetch all records to compute level distribution
        all_records = nodelog.query(limit=2000)
        level_dist: Dict[str, int] = {}
        type_dist:  Dict[str, int] = {}
        for r in all_records:
            level_dist[r.status_level] = level_dist.get(r.status_level, 0) + 1
            type_dist[r.event_type]    = type_dist.get(r.event_type, 0)    + 1

        suppressed = 0
        try:
            suppressed = nodelog.suppressed_count
        except Exception:
            pass

        return {
            "ok": True,
            "buffer_size": len(all_records),
            "buffer_maxlen": getattr(nodelog, "maxlen", 2000),
            "suppressed_count": suppressed,
            "nodelog_available": _NODELOG_AVAILABLE,
            "bridge_available": _BRIDGE_AVAILABLE,
            "level_distribution": level_dist,
            "type_distribution": type_dist,
        }
    except Exception as exc:
        return _error_payload(f"Stats failed: {exc}")


def _nodelog_clear() -> dict:
    """Clear the NodeLog in-memory ring buffer."""
    try:
        # Access the internal buffer deque and clear it
        with nodelog._lock:
            count_before = len(nodelog._buffer)
            nodelog._buffer.clear()
            nodelog._seq_id_counter = 0
            nodelog._suppressed_count = 0
        _emit("NodeLog", "SYSTEM", "INFO",
              f"Buffer cleared. Removed {count_before} events.",
              {"cleared_count": count_before})
        return {"ok": True, "cleared": count_before}
    except Exception as exc:
        return _error_payload(f"Clear failed: {exc}")


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:
        print(f"  [{self.log_date_time_string()}] {fmt % args}")

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/":
            self._serve_file(_TESTING_DIR / "index.html", "text/html")
        elif path == "/nodelog":
            self._serve_file(_TESTING_DIR / "nodelog_ui.html", "text/html")
        elif path == "/api/validate":
            workspace_root = params.get("workspace_root", "")
            self._send_json(_do_validate_workspace(workspace_root))
        elif path == "/api/nodelog/events":
            self._send_json(_nodelog_events(params))
        elif path == "/api/nodelog/stats":
            self._send_json(_nodelog_stats())
        else:
            self._send_json({"error": "Not found", "path": path}, status=404)

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
        elif path == "/api/nodelog/emit":
            self._send_json(_nodelog_emit(body))
        else:
            self._send_json({"error": "Not found", "path": path}, status=404)

    # ── DELETE ───────────────────────────────────────────────────────────────

    def do_DELETE(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/nodelog/clear":
            self._send_json(_nodelog_clear())
        else:
            self._send_json({"error": "Not found"}, status=404)

    # ── OPTIONS (CORS preflight) ──────────────────────────────────────────────

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _serve_file(self, file_path: Path, content_type: str) -> None:
        try:
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            msg = f"{file_path.name} not found.".encode()
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def _read_json_body(self) -> Optional[dict]:
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7491
    host = "127.0.0.1"

    # Emit startup event
    _emit("Server", "SYSTEM", "INFO",
          f"TESTING server starting on {host}:{port}",
          {"port": port, "nodelog": _NODELOG_AVAILABLE, "bridge": _BRIDGE_AVAILABLE,
           "log_dir": _LOG_DIR})

    print()
    print("=" * 65)
    print("  LegacyBridge + NodeInsight + NodeLog  —  TESTING Server")
    print("=" * 65)
    print(f"  URL        : http://{host}:{port}")
    print(f"  NodeLog UI : http://{host}:{port}/nodelog")
    print(f"  Root       : {_TESTING_DIR}")
    print(f"  NodeLog    : {'[OK]' if _NODELOG_AVAILABLE else '[MISSING] ' + _NODELOG_ERROR}")
    print(f"  Bridge     : {'[OK]' if _BRIDGE_AVAILABLE else '[MISSING] ' + _BRIDGE_IMPORT_ERROR}")
    print(f"  Log dir    : {_LOG_DIR}")
    print()
    print("  Open http://127.0.0.1:7491 in your browser.")
    print("  Press Ctrl+C to stop.")
    print("=" * 65)
    print()

    server = HTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
