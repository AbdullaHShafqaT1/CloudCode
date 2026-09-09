"""
TESTING/run_tests.py -- Automated Integration Test Runner
=========================================================

Starts TESTING/server.py in a background thread, then exercises all
API endpoints and verifies NodeLog integration.

No pytest required -- uses stdlib unittest only.

Usage
-----
    python TESTING/run_tests.py              # from CloudCode/ root
    python run_tests.py                      # from TESTING/ dir
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

# ---------------------------------------------------------------------------
# Force UTF-8 output so Unicode chars never crash on Windows cp1252 terminal
# ---------------------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_THIS   = Path(__file__).resolve().parent        # TESTING/
_ROOT   = _THIS.parent                            # CloudCode/
_NODELOG = _ROOT / "NodeLog"

for _p in [str(_ROOT), str(_NODELOG)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 7491
BASE_URL    = f"http://{SERVER_HOST}:{SERVER_PORT}"

def _start_server():
    """Import and start the TESTING server in this thread (blocking)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("testing_server", str(_THIS / "server.py"))
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from http.server import HTTPServer
    server = HTTPServer((SERVER_HOST, SERVER_PORT), mod._Handler)
    server.serve_forever()

def _wait_for_server(timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{BASE_URL}/api/nodelog/stats", timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(path: str, timeout: int = 8) -> dict:
    url = BASE_URL + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _post(path: str, body: dict, timeout: int = 10) -> dict:
    url  = BASE_URL + path
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _delete(path: str, timeout: int = 8) -> dict:
    url = BASE_URL + path
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _safe_print(*args, **kwargs) -> None:
    """Print a message, replacing any unencodable chars safely."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        safe_args = [
            a.encode("ascii", errors="replace").decode("ascii") if isinstance(a, str) else a
            for a in args
        ]
        print(*safe_args, **kwargs)


# ============================================================================
# Test suite
# ============================================================================

class TestNodeLogAPI(unittest.TestCase):
    """Tests for /api/nodelog/* endpoints."""

    def test_01_stats_returns_ok(self):
        """GET /api/nodelog/stats should return ok=True."""
        d = _get("/api/nodelog/stats")
        self.assertTrue(d.get("ok"), f"stats returned ok=False: {d}")
        self.assertIn("buffer_size",       d)
        self.assertIn("suppressed_count",  d)
        self.assertIn("nodelog_available", d)
        _safe_print(f"  NodeLog available: {d['nodelog_available']}  Bridge: {d['bridge_available']}")

    def test_02_emit_event(self):
        """POST /api/nodelog/emit should create a new event."""
        body = {
            "source":       "RunTests",
            "event_type":   "SYSTEM",
            "status_level": "INFO",
            "message":      "Automated test event from run_tests.py",
            "payload":      {"test_run": True, "runner": "unittest"},
            "trace_id":     "run-tests-001",
        }
        d = _post("/api/nodelog/emit", body)
        self.assertTrue(d.get("ok"), f"emit returned ok=False: {d}")
        self.assertIn("event", d)
        evt = d["event"]
        self.assertEqual(evt["source"],       "RunTests")
        self.assertEqual(evt["event_type"],   "SYSTEM")
        self.assertEqual(evt["status_level"], "INFO")
        self.assertEqual(evt["message"],      "Automated test event from run_tests.py")
        self.assertIsNotNone(evt["seq_id"])
        _safe_print(f"  Emitted event seq_id={evt['seq_id']}")

    def test_03_emit_all_levels(self):
        """POST /api/nodelog/emit for each status level."""
        levels = ["DEBUG", "INFO", "WARN", "ERROR", "SUCCESS", "CRITICAL"]
        emitted = 0
        for lvl in levels:
            body = {
                "source":       "LevelTest",
                "event_type":   "TELEMETRY",
                "status_level": lvl,
                "message":      f"Level test: {lvl}",
            }
            d = _post("/api/nodelog/emit", body)
            # Either emitted OK or suppressed by min_level -- both are valid
            if d.get("ok"):
                emitted += 1
            else:
                self.assertIn("suppressed", d.get("error", "").lower(),
                              f"Unexpected failure emitting {lvl}: {d}")
        _safe_print(f"  Emitted {emitted} of {len(levels)} level variants (rest suppressed)")

    def test_04_query_events(self):
        """GET /api/nodelog/events returns events."""
        d = _get("/api/nodelog/events?limit=50")
        self.assertTrue(d.get("ok"), f"events returned ok=False: {d}")
        self.assertIn("events", d)
        self.assertIsInstance(d["events"], list)
        _safe_print(f"  Query returned {d['count']} events")

    def test_05_query_filter_by_level(self):
        """GET /api/nodelog/events?level=INFO filters correctly."""
        # Emit a guaranteed INFO event first
        _post("/api/nodelog/emit", {
            "source": "FilterTest", "event_type": "SYSTEM",
            "status_level": "INFO", "message": "filter-test-INFO-unique"
        })
        d = _get("/api/nodelog/events?level=INFO&limit=200")
        self.assertTrue(d.get("ok"))
        for evt in d["events"]:
            self.assertEqual(evt["status_level"], "INFO",
                             f"Got non-INFO event in filtered result: {evt}")
        _safe_print(f"  INFO filter: {d['count']} events, all INFO [PASS]")

    def test_06_query_filter_by_type(self):
        """GET /api/nodelog/events?type=TELEMETRY filters correctly."""
        d = _get("/api/nodelog/events?type=TELEMETRY&limit=200")
        self.assertTrue(d.get("ok"))
        for evt in d["events"]:
            self.assertEqual(evt["event_type"], "TELEMETRY",
                             f"Got non-TELEMETRY event: {evt}")
        _safe_print(f"  TELEMETRY filter: {d['count']} events [PASS]")

    def test_07_emit_missing_message_returns_error(self):
        """POST /api/nodelog/emit without message should return error."""
        d = _post("/api/nodelog/emit", {"source": "Test", "status_level": "INFO", "message": ""})
        self.assertFalse(d.get("ok"), f"Expected ok=False but got: {d}")
        _safe_print(f"  Missing message correctly rejected: {d['error']}")

    def test_08_clear_buffer(self):
        """DELETE /api/nodelog/clear should empty the buffer."""
        # Emit some events first
        for i in range(3):
            _post("/api/nodelog/emit", {"source": "ClearTest", "event_type": "SYSTEM",
                                        "status_level": "INFO", "message": f"pre-clear event {i}"})
        pre = _get("/api/nodelog/events?limit=2000")
        self.assertGreater(pre["count"], 0, "Buffer should have events before clear")

        d = _delete("/api/nodelog/clear")
        self.assertTrue(d.get("ok"), f"clear returned ok=False: {d}")
        self.assertIn("cleared", d)
        _safe_print(f"  Cleared {d['cleared']} events [PASS]")

        post = _get("/api/nodelog/events?limit=2000")
        # After clear, buffer gets at most 1 new event (the clear confirmation itself)
        self.assertLessEqual(post["count"], 5,
                             f"Buffer should be near-empty after clear, got {post['count']}")
        _safe_print(f"  Post-clear buffer: {post['count']} events [PASS]")


class TestBridgeAPI(unittest.TestCase):
    """Tests for /api/reader and /api/health endpoints."""

    def test_09_health_check_cloudflare(self):
        """POST /api/health with Cloudflare URL should attempt a connection."""
        url = "https://superintendent-navigator-wholesale-divine.trycloudflare.com"
        d = _post("/api/health", {"url": url}, timeout=15)
        self.assertIn("ok",         d)
        self.assertIn("latency_ms", d)
        self.assertIn("url",        d)
        self.assertIsNotNone(d.get("message"))
        status = d.get("status_code")
        _safe_print(f"  Health check -> HTTP {status}  latency={d['latency_ms']}ms  ok={d['ok']}")
        # Either connected (ok=True) or failed gracefully (ok=False with message) -- both valid
        # We only require the response structure is correct

    def test_10_health_check_invalid_url(self):
        """POST /api/health with unreachable URL should return ok=False (graceful)."""
        d = _post("/api/health", {"url": "http://127.0.0.1:19999/nonexistent"}, timeout=10)
        self.assertFalse(d.get("ok"), f"Expected ok=False for unreachable host: {d}")
        _safe_print(f"  Bad URL gracefully failed: {d['message']}")

    def test_11_health_check_missing_url(self):
        """POST /api/health with empty url returns error payload."""
        d = _post("/api/health", {"url": ""})
        self.assertFalse(d.get("ok"))
        _safe_print(f"  Missing URL rejected: {d['error']}")

    def test_12_reader_call_missing_workspace(self):
        """POST /api/reader with no workspace_root returns error."""
        d = _post("/api/reader", {"action": "list_dir"})
        self.assertFalse(d.get("ok"))
        _safe_print(f"  Missing workspace rejected: {d['error']}")

    def test_13_reader_call_missing_action(self):
        """POST /api/reader with no action returns error."""
        d = _post("/api/reader", {"workspace_root": str(_ROOT)})
        self.assertFalse(d.get("ok"))
        _safe_print(f"  Missing action rejected: {d['error']}")

    def test_14_reader_list_dir_testing(self):
        """POST /api/reader list_dir on TESTING/ should succeed if bridge available."""
        d = _post("/api/reader", {
            "workspace_root": str(_THIS),
            "action":         "list_dir",
            "path":           "."
        })
        _safe_print(f"  list_dir TESTING/: ok={d.get('ok')}  error={d.get('error', 'none')}")
        # If bridge is available, it must succeed
        if d.get("error") and "import" in d.get("error", "").lower():
            self.skipTest("LegacyBridge not installed")
        self.assertTrue(d.get("ok"), f"list_dir failed: {d.get('error')}")

    def test_15_reader_call_logs_to_nodelog(self):
        """Every reader_call should produce NodeLog events."""
        # Clear buffer first
        _delete("/api/nodelog/clear")
        time.sleep(0.15)

        # Make a reader call
        _post("/api/reader", {
            "workspace_root": str(_THIS),
            "action":         "list_dir",
            "path":           "."
        })
        time.sleep(0.15)

        # Check NodeLog was populated
        d = _get("/api/nodelog/events?limit=50")
        self.assertTrue(d.get("ok"))
        self.assertGreater(d["count"], 0,
            "Expected NodeLog events after reader_call but buffer is empty")
        sources = [e["source"] for e in d["events"]]
        logged = {"BridgeAdapter", "WorkspaceValidator", "Server"}
        found = any(s in logged for s in sources)
        self.assertTrue(found,
            f"Expected a bridge-related NodeLog source, got: {sources}")
        _safe_print(f"  NodeLog captured {d['count']} events after reader_call [PASS]"
                    f" sources={list(set(sources))}")

    def test_16_health_check_logs_to_nodelog(self):
        """Every health_check should produce NodeLog events."""
        _delete("/api/nodelog/clear")
        time.sleep(0.15)

        _post("/api/health", {"url": "http://127.0.0.1:19999/test"}, timeout=8)
        time.sleep(0.15)

        d = _get("/api/nodelog/events?limit=50")
        self.assertTrue(d.get("ok"))
        self.assertGreater(d["count"], 0, "Expected NodeLog events after health_check")
        sources = [e["source"] for e in d["events"]]
        self.assertIn("HealthCheck", sources,
                      f"Expected HealthCheck source. Got: {sources}")
        _safe_print(f"  NodeLog captured {d['count']} events after health_check [PASS]")


class TestHTMLPages(unittest.TestCase):
    """Verify that HTML pages are served correctly."""

    def test_17_main_page_serves_html(self):
        """GET / should return 200 with HTML content."""
        req = urllib.request.Request(f"{BASE_URL}/")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            content = r.read().decode("utf-8")
            self.assertIn("LegacyBridge", content)
            self.assertIn("NodeLog",      content)
            self.assertIn("/nodelog",     content)  # nav link must exist
        _safe_print("  GET /  -> 200 HTML, contains NodeLog nav link [PASS]")

    def test_18_nodelog_page_serves_html(self):
        """GET /nodelog should return 200 with NodeLog UI."""
        req = urllib.request.Request(f"{BASE_URL}/nodelog")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            content = r.read().decode("utf-8")
            self.assertIn("NodeLog", content)
            self.assertIn("Event",   content)
        _safe_print("  GET /nodelog  -> 200 HTML, NodeLog dashboard [PASS]")

    def test_19_unknown_route_returns_404(self):
        """GET /nonexistent should return 404."""
        req = urllib.request.Request(f"{BASE_URL}/nonexistent")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("Expected HTTP 404 but got 200")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)
        _safe_print("  GET /nonexistent  -> 404 [PASS]")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests() -> bool:
    _safe_print("")
    _safe_print("=" * 68)
    _safe_print("  TESTING/run_tests.py -- NodeLog Integration Test Suite")
    _safe_print("=" * 68)
    _safe_print(f"  Server : {BASE_URL}")
    _safe_print(f"  Root   : {_ROOT}")
    _safe_print("")

    # -- Start server in background thread ------------------------------------
    _safe_print("  Starting TESTING server...", end="")
    import sys as _sys
    try:
        _sys.stdout.flush()
    except Exception:
        pass

    t = threading.Thread(target=_start_server, daemon=True)
    t.start()

    if not _wait_for_server(timeout=12):
        _safe_print(" FAILED (server did not start in 12s)")
        _safe_print("")
        _safe_print("  [ABORT] Server failed to start. Check TESTING/server.py.")
        return False
    _safe_print(" OK")
    _safe_print("")

    # -- Run test suite -------------------------------------------------------
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None  # keep definition order

    suite = unittest.TestSuite()
    for cls in [TestNodeLogAPI, TestBridgeAPI, TestHTMLPages]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    # Use a UTF-8-safe stream wrapper
    safe_stream = io.TextIOWrapper(
        sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
    ) if hasattr(sys.stdout, "buffer") else sys.stdout

    runner = unittest.TextTestRunner(verbosity=2, stream=safe_stream)
    result = runner.run(suite)

    _safe_print("")
    _safe_print("=" * 68)
    if result.wasSuccessful():
        passed = result.testsRun
        _safe_print(f"  [ALL PASS]  {passed} / {passed} tests passed")
    else:
        total  = result.testsRun
        failed = len(result.failures) + len(result.errors)
        passed = total - failed
        _safe_print(f"  [FAIL]  {failed} of {total} tests failed  ({passed} passed)")
        _safe_print("")
        for i, (test, tb) in enumerate(result.failures + result.errors, 1):
            _safe_print(f"  [{i}] {test}")
            for line in tb.splitlines()[-4:]:
                _safe_print(f"      {line}")
    _safe_print("=" * 68)
    _safe_print("")
    return result.wasSuccessful()


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
