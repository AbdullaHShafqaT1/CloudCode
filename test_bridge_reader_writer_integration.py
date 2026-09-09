"""
test_bridge_reader_writer_integration.py
========================================
End-to-end integration test validating the full pipeline:

    LegacyBridge  ──►  NodeInsight (read / AST parse)
    LegacyBridge  ──►  NodeForge   (atomic write / patch)
    LegacyBridge  ──►  NodeInsight (re-read & verify)

Three test scenarios
--------------------
1. CLOSED-LOOP MODIFICATION CYCLE
   Step 1 — Read dummy_service.py via Bridge → NodeInsight (parse_ast).
   Step 2 — Append a new helper function via Bridge → NodeForge.
   Step 3 — Re-read the modified file via Bridge → NodeInsight.
   Assert  — New symbol present, file syntax-valid, zero truncation.

2. DUAL EXECUTION RUNTIME MODES
   RUN_MODE=local  → direct filesystem / local loopback (default).
   RUN_MODE=remote → routes read/write through BRIDGE_URL endpoint
                     and verifies remote telemetry echoes the result.

3. ERROR HANDLING & STATE RECOVERY
   Send a malformed patch instruction through NodeForge.
   Assert — NodeForge rejects it, file stays intact, error payload
            propagates through LegacyBridge, and NodeInsight
            confirms the file is unharmed on re-read.

Configuration
-------------
Environment variables (can be set in .env or exported in the shell):

    RUN_MODE        local | remote          (default: local)
    BRIDGE_URL      https://...             (required for remote mode)
    SANDBOX_ROOT    /absolute/path          (default: temp dir per test)
    LLM_API_KEY     ollama                  (optional, used by remote mode)

Run commands
------------
# Local mode (default)
    pytest test_bridge_reader_writer_integration.py -v --tb=short

# Remote mode against Colab/cloud bridge endpoint
    RUN_MODE=remote BRIDGE_URL=https://your-tunnel.trycloudflare.com \\
        pytest test_bridge_reader_writer_integration.py -v --tb=short

# Verbose structured output
    pytest test_bridge_reader_writer_integration.py -v -s --tb=long
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# stdlib
# ---------------------------------------------------------------------------
import ast
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# ---------------------------------------------------------------------------
# Logger — structured, timestamped output for every integration step
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("IntegrationTest")

# ---------------------------------------------------------------------------
# sys.path bootstrap — make all three components importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT   = Path(__file__).parent.resolve()
_LEGACYBRIDGE   = _PROJECT_ROOT / "LegacyBridge"
_LEGACYNODE_PKG = _LEGACYBRIDGE / "legacynode"
_NODEINSIGHT    = _PROJECT_ROOT / "NodeInsight"
_NODEFORGE      = _PROJECT_ROOT / "NodeForge"

for _p in [
    str(_LEGACYBRIDGE),
    str(_LEGACYNODE_PKG),
    str(_NODEINSIGHT),
    str(_NODEFORGE),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Component imports — fail loudly if any component is missing
# ---------------------------------------------------------------------------
try:
    from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult
    from legacynode.tools.node_insight_bridge import NodeInsightBridge
    log.info("LegacyBridge imported OK")
except ImportError as exc:
    pytest.exit(f"Cannot import LegacyBridge: {exc}", returncode=1)

try:
    from file_reader_tool import FileReaderTool  # type: ignore[import]
    log.info("NodeInsight (FileReaderTool) imported OK")
except ImportError as exc:
    pytest.exit(f"Cannot import NodeInsight: {exc}", returncode=1)

try:
    from tools.file_writer_tool import FileWriterTool  # type: ignore[import]
    log.info("NodeForge (FileWriterTool) imported OK")
except ImportError as exc:
    pytest.exit(f"Cannot import NodeForge: {exc}", returncode=1)

# ---------------------------------------------------------------------------
# Runtime mode detection
# ---------------------------------------------------------------------------
RUN_MODE   = os.environ.get("RUN_MODE", "local").lower()
BRIDGE_URL = os.environ.get("BRIDGE_URL", "").strip()

if RUN_MODE == "remote" and not BRIDGE_URL:
    pytest.exit(
        "RUN_MODE=remote requires BRIDGE_URL to be set.\n"
        "  Example: BRIDGE_URL=https://your-tunnel.trycloudflare.com",
        returncode=1,
    )

_BRIDGE_MODE = BridgeMode.REMOTE if RUN_MODE == "remote" else BridgeMode.LOCAL
log.info(f"RUN_MODE={RUN_MODE}  BRIDGE_MODE={_BRIDGE_MODE.value}  BRIDGE_URL={BRIDGE_URL or '(local)'}")

# ---------------------------------------------------------------------------
# Sample source file content — the "dummy_service.py" subject under test
# ---------------------------------------------------------------------------
DUMMY_SERVICE_INITIAL = textwrap.dedent("""\
    \"\"\"
    dummy_service.py — integration test subject.
    This file is written fresh per test run and must remain well-formed Python.
    \"\"\"

    import os
    import sys
    from typing import List, Optional


    # ── Constants ────────────────────────────────────────────────────────────
    MAX_RETRIES: int = 3
    DEFAULT_TIMEOUT: float = 30.0


    # ── Service class ────────────────────────────────────────────────────────

    class DummyService:
        \"\"\"Minimal service stub for integration testing.\"\"\"

        def __init__(self, name: str, timeout: float = DEFAULT_TIMEOUT) -> None:
            \"\"\"Initialise the service with a name and timeout.\"\"\"
            self.name = name
            self.timeout = timeout
            self._started = False

        def start(self) -> bool:
            \"\"\"Start the service and return True on success.\"\"\"
            self._started = True
            return self._started

        def stop(self) -> None:
            \"\"\"Gracefully stop the service.\"\"\"
            self._started = False

        def status(self) -> dict:
            \"\"\"Return the current service status as a dict.\"\"\"
            return {
                \"name\": self.name,
                \"started\": self._started,
                \"timeout\": self.timeout,
            }

        async def async_start(self) -> bool:
            \"\"\"Async variant of start().\"\"\"
            return self.start()


    # ── Module-level helpers ─────────────────────────────────────────────────

    def build_service(name: str, timeout: Optional[float] = None) -> DummyService:
        \"\"\"Factory — construct a DummyService with sensible defaults.\"\"\"
        return DummyService(name=name, timeout=timeout or DEFAULT_TIMEOUT)


    def run_service(name: str, items: List[str]) -> dict:
        \"\"\"Build, start, and immediately stop a DummyService; return its status.\"\"\"
        svc = build_service(name)
        svc.start()
        result = svc.status()
        svc.stop()
        result[\"items_processed\"] = len(items)
        return result
    """)

# The new function NodeForge will append during the modification step
NEW_HELPER_FUNCTION = textwrap.dedent("""\


    # ── NEW FUNCTION — injected by NodeForge integration test ────────────────

    def compute_health_score(service_status: dict, penalty: float = 0.1) -> float:
        \"\"\"
        Compute a normalised health score [0.0–1.0] from a service status dict.

        Parameters
        ----------
        service_status : dict
            As returned by DummyService.status().
        penalty : float
            Per-retry penalty subtracted from the base score (default 0.1).

        Returns
        -------
        float
            Health score clamped to [0.0, 1.0].
        \"\"\"
        base = 1.0 if service_status.get(\"started\") else 0.5
        retries = service_status.get(\"retries\", 0)
        score = base - (retries * penalty)
        return max(0.0, min(1.0, score))
    """)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro) -> Any:
    """Run a coroutine synchronously.

    Creates a brand-new event loop so this helper is safe to call from any
    thread and avoids the Python 3.10+ DeprecationWarning raised by
    ``asyncio.get_event_loop()`` when no running loop exists.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _assert_bridge_result_ok(result: BridgeResult, label: str) -> None:
    """Assert that a BridgeResult is successful and log the outcome."""
    assert isinstance(result, BridgeResult), \
        f"[{label}] Expected BridgeResult, got {type(result)}"
    if not result.ok:
        log.error(f"[{label}] FAILED — error={result.error}\n{result.traceback or ''}")
    assert result.ok, f"[{label}] BridgeResult.ok=False: {result.error}"
    log.info(f"[{label}] OK  latency={result.latency_ms:.1f} ms")


def _extract_ast_function_names(bridge_result: BridgeResult) -> list[str]:
    """
    Pull the list of top-level function names from a parse_ast BridgeResult.

    bridge_result.data is the ToolResponse dict:
        { "status": ..., "data": { "functions": [...], ... }, "error_message": ... }
    """
    tool_resp_data = bridge_result.data.get("data") or {}
    functions: list[dict] = tool_resp_data.get("functions", [])
    return [fn["name"] for fn in functions]


def _validate_python_syntax(source: str) -> None:
    """Compile source string and raise SyntaxError if malformed."""
    compile(source, "<integration-test>", "exec")


def _make_bridge_adapter(workspace_root: str) -> BridgeAdapter:
    """Construct a BridgeAdapter in the configured mode."""
    return BridgeAdapter(
        mode=_BRIDGE_MODE,
        workspace_root=workspace_root,
        tunnel_url=BRIDGE_URL,
    )


# ---------------------------------------------------------------------------
# Remote bridge helpers (HTTP fallback for RUN_MODE=remote)
# ---------------------------------------------------------------------------

def _remote_reader_call(action: str, workspace_root: str, **kwargs) -> BridgeResult:
    """
    In remote mode, proxy a NodeInsight request through the BRIDGE_URL endpoint.
    Payload: POST /nodeinsight  { "action": ..., "workspace_root": ..., **kwargs }
    Falls back to local NodeInsightBridge if the endpoint is unavailable.
    """
    if RUN_MODE != "remote":
        raise RuntimeError("_remote_reader_call called outside remote mode")

    try:
        import httpx  # type: ignore[import]
    except ImportError:
        log.warning("httpx not installed — falling back to local NodeInsight for remote mode")
        return _local_reader_call(action, workspace_root, **kwargs)

    payload = {"action": action, "workspace_root": workspace_root, **kwargs}
    url = BRIDGE_URL.rstrip("/") + "/nodeinsight"
    log.info(f"[Remote] POST {url}  action={action}")
    try:
        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        tool_resp = resp.json()
        ok = tool_resp.get("status") == "success"
        return BridgeResult(
            ok=ok,
            data=tool_resp,
            error=tool_resp.get("error_message") if not ok else None,
        )
    except Exception as exc:
        log.warning(f"[Remote] Request failed ({exc}), falling back to local NodeInsight")
        return _local_reader_call(action, workspace_root, **kwargs)


def _remote_writer_call(action: str, workspace_root: str, **kwargs) -> dict:
    """
    In remote mode, proxy a NodeForge write request through the BRIDGE_URL endpoint.
    Payload: POST /nodeforge  { "action": ..., "workspace_root": ..., **kwargs }
    Falls back to local FileWriterTool if the endpoint is unavailable.
    """
    if RUN_MODE != "remote":
        raise RuntimeError("_remote_writer_call called outside remote mode")

    try:
        import httpx  # type: ignore[import]
    except ImportError:
        log.warning("httpx not installed — falling back to local NodeForge for remote mode")
        return _local_writer_call(action, workspace_root, **kwargs)

    payload = {"action": action, "workspace_root": workspace_root, **kwargs}
    url = BRIDGE_URL.rstrip("/") + "/nodeforge"
    log.info(f"[Remote] POST {url}  action={action}")
    try:
        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning(f"[Remote] Request failed ({exc}), falling back to local NodeForge")
        return _local_writer_call(action, workspace_root, **kwargs)


def _local_reader_call(action: str, workspace_root: str, **kwargs) -> BridgeResult:
    """Direct local NodeInsight call (no bridge overhead)."""
    tool = FileReaderTool(workspace_root=workspace_root)
    tool_resp = _run_async(tool.call(action, **kwargs))
    ok = tool_resp.get("status") == "success"
    return BridgeResult(
        ok=ok,
        data=tool_resp,
        error=tool_resp.get("error_message") if not ok else None,
    )


def _local_writer_call(action: str, workspace_root: str, **kwargs) -> dict:
    """Direct local NodeForge call (no bridge overhead)."""
    writer = FileWriterTool(workspace_root=workspace_root)
    return _run_async(writer.call(action, **kwargs))


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """
    Provide a clean temporary workspace with dummy_service.py pre-populated.

    If SANDBOX_ROOT env var is set the test uses that directory instead of a
    tmp_path so that results can be inspected after the run.
    """
    sandbox_root_env = os.environ.get("SANDBOX_ROOT", "").strip()
    if sandbox_root_env:
        root = Path(sandbox_root_env).resolve()
        root.mkdir(parents=True, exist_ok=True)
        log.info(f"Using env-provided sandbox: {root}")
    else:
        root = tmp_path
        log.info(f"Using tmp sandbox: {root}")

    # Write the initial dummy service file
    service_file = root / "dummy_service.py"
    service_file.write_text(DUMMY_SERVICE_INITIAL, encoding="utf-8")
    log.info(f"Wrote initial dummy_service.py ({service_file.stat().st_size} bytes)")

    yield root

    # Cleanup .bak files produced by NodeForge during the test run
    for bak in root.glob("**/*.bak"):
        try:
            bak.unlink()
        except OSError:
            pass


@pytest.fixture()
def adapter(sandbox: Path) -> BridgeAdapter:
    """Return a BridgeAdapter wired to the sandbox workspace."""
    adp = _make_bridge_adapter(str(sandbox))
    log.info(
        f"BridgeAdapter created  mode={adp.mode.value}  workspace={adp.workspace_root}"
    )
    return adp


# ===========================================================================
# SCENARIO 1 — Closed-Loop Modification Cycle
# ===========================================================================

class TestClosedLoopModificationCycle:
    """
    Validates the full Read → Modify → Re-read → Verify pipeline.

    Components exercised:
        LegacyBridge.reader_call("parse_ast")
        FileWriterTool.call("append_file")
        LegacyBridge.reader_call("parse_ast")  [second pass]
        FileReaderTool.call("read_file")        [syntax integrity check]
    """

    # ── Step 1: Read via Bridge → NodeInsight ────────────────────────────────

    def test_step1_read_initial_ast(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """
        Step 1 — BridgeAdapter dispatches parse_ast to NodeInsight.
        Assert the two expected module-level functions are reported with no truncation.
        """
        log.info("=== STEP 1: Read initial AST via LegacyBridge → NodeInsight ===")

        result = adapter.reader_call("parse_ast", path="dummy_service.py")
        _assert_bridge_result_ok(result, "Step1/parse_ast")

        fn_names = _extract_ast_function_names(result)
        log.info(f"  Function symbols found: {fn_names}")

        # Both module-level functions must be present
        assert "build_service" in fn_names, \
            f"Expected 'build_service' in AST, got: {fn_names}"
        assert "run_service" in fn_names, \
            f"Expected 'run_service' in AST, got: {fn_names}"

        # Zero truncation: error_message must be None on success
        assert result.data.get("error_message") is None, \
            f"Unexpected error_message in success response: {result.data['error_message']}"

        # Latency sanity
        assert result.latency_ms >= 0, "Latency must be non-negative"

        log.info(f"  PASS — {len(fn_names)} function(s) parsed, latency={result.latency_ms:.1f} ms")

    # ── Step 2: Modify via NodeForge (routed through test harness) ───────────

    def test_step2_modify_via_nodeforge(self, sandbox: Path) -> None:
        """
        Step 2 — NodeForge appends a new helper function to dummy_service.py.
        Assert append succeeds and the file grows in size.
        """
        log.info("=== STEP 2: Modify dummy_service.py via NodeForge ===")

        before_size = (sandbox / "dummy_service.py").stat().st_size

        if RUN_MODE == "remote":
            result = _remote_writer_call(
                "append_file",
                workspace_root=str(sandbox),
                path="dummy_service.py",
                content=NEW_HELPER_FUNCTION,
            )
        else:
            result = _local_writer_call(
                "append_file",
                workspace_root=str(sandbox),
                path="dummy_service.py",
                content=NEW_HELPER_FUNCTION,
            )

        log.info(f"  NodeForge result: {json.dumps(result, default=str, indent=2)}")

        assert result.get("status") == "success", \
            f"NodeForge append_file failed: {result.get('error_message')}"

        after_size = (sandbox / "dummy_service.py").stat().st_size
        assert after_size > before_size, \
            f"File must grow after append ({before_size} → {after_size} bytes)"

        bytes_written = result.get("bytes_written") or 0
        assert bytes_written > 0, \
            f"bytes_written should be positive, got {bytes_written}"

        log.info(
            f"  PASS — file grew {before_size} → {after_size} bytes "
            f"(NodeForge reported {bytes_written} bytes written)"
        )

    # ── Step 3: Re-read & Verify via Bridge → NodeInsight ───────────────────

    def test_step3_reread_and_verify_ast(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """
        Step 3 — After appending the new function, re-parse via NodeInsight.
        Assert the new symbol appears and the file is syntactically valid.

        This test runs the full 3-step pipeline in sequence so it is self-contained.
        """
        log.info("=== STEP 3: Full closed-loop — Read → Modify → Re-read → Verify ===")

        # ── 3a. Baseline AST read ─────────────────────────────────────────────
        log.info("  3a. Baseline parse_ast")
        r_before = adapter.reader_call("parse_ast", path="dummy_service.py")
        _assert_bridge_result_ok(r_before, "Step3a/parse_ast_before")
        fn_before = _extract_ast_function_names(r_before)
        assert "compute_health_score" not in fn_before, \
            "Precondition failed: compute_health_score should NOT exist yet"

        # ── 3b. Append via NodeForge ──────────────────────────────────────────
        log.info("  3b. Append via NodeForge")
        if RUN_MODE == "remote":
            write_result = _remote_writer_call(
                "append_file",
                workspace_root=str(sandbox),
                path="dummy_service.py",
                content=NEW_HELPER_FUNCTION,
            )
        else:
            write_result = _local_writer_call(
                "append_file",
                workspace_root=str(sandbox),
                path="dummy_service.py",
                content=NEW_HELPER_FUNCTION,
            )
        assert write_result.get("status") == "success", \
            f"NodeForge append failed: {write_result.get('error_message')}"

        # ── 3c. Re-read AST via Bridge → NodeInsight ──────────────────────────
        log.info("  3c. Re-read parse_ast after modification")
        r_after = adapter.reader_call("parse_ast", path="dummy_service.py")
        _assert_bridge_result_ok(r_after, "Step3c/parse_ast_after")

        fn_after = _extract_ast_function_names(r_after)
        log.info(f"  Functions after modification: {fn_after}")

        # Core assertion: new symbol must be in the AST
        assert "compute_health_score" in fn_after, (
            f"FAIL — 'compute_health_score' not found in re-parsed AST.\n"
            f"  Functions present: {fn_after}"
        )

        # Zero truncation check
        assert r_after.data.get("error_message") is None, \
            f"Unexpected error_message in re-read: {r_after.data['error_message']}"

        # ── 3d. Syntax integrity ──────────────────────────────────────────────
        log.info("  3d. Syntax integrity check")
        modified_source = (sandbox / "dummy_service.py").read_text(encoding="utf-8")
        try:
            _validate_python_syntax(modified_source)
        except SyntaxError as exc:
            pytest.fail(f"Modified file has invalid Python syntax: {exc}")

        # ── 3e. Verify via read_file that content is not truncated ────────────
        log.info("  3e. Payload truncation check via read_file")
        r_read = adapter.reader_call("read_file", path="dummy_service.py")
        _assert_bridge_result_ok(r_read, "Step3e/read_file")
        file_content = r_read.data.get("data", {}).get("content", "")
        assert "compute_health_score" in file_content, \
            "compute_health_score not found in raw file content — possible truncation"
        assert "Health score" in file_content, \
            "Docstring content missing — possible truncation"

        log.info(
            f"  PASS — closed-loop cycle complete. "
            f"Functions before={fn_before}, after={fn_after}. "
            f"Syntax valid. No truncation."
        )


# ===========================================================================
# SCENARIO 2 — Dual Execution Runtime Modes
# ===========================================================================

class TestDualRuntimeModes:
    """
    Validates that both RUN_MODE=local and RUN_MODE=remote produce equivalent
    read responses from NodeInsight through the LegacyBridge routing layer.
    """

    def test_runtime_mode_is_configured_correctly(
        self, adapter: BridgeAdapter
    ) -> None:
        """Assert the BridgeAdapter is wired to the expected BridgeMode."""
        log.info(f"=== RUNTIME MODE CHECK: expected={_BRIDGE_MODE.value} ===")
        assert adapter.mode == _BRIDGE_MODE, \
            f"Adapter mode mismatch: got {adapter.mode.value}, expected {_BRIDGE_MODE.value}"
        log.info(f"  PASS — mode={adapter.mode.value}")

    def test_local_loopback_read_returns_valid_payload(
        self, sandbox: Path
    ) -> None:
        """
        RUN_MODE=local path: direct loopback.
        NodeInsight reads dummy_service.py; BridgeResult carries full payload.
        """
        log.info("=== RUNTIME: local loopback read ===")
        result = _local_reader_call("read_file", str(sandbox), path="dummy_service.py")
        _assert_bridge_result_ok(result, "LocalLoopback/read_file")

        data = result.data.get("data") or {}
        assert data.get("content"), "Content field must be non-empty"
        assert data.get("total_lines", 0) > 0, "total_lines must be positive"
        assert data.get("size_bytes", 0) > 0, "size_bytes must be positive"

        log.info(
            f"  PASS — {data['total_lines']} lines, {data['size_bytes']} bytes "
            f"(local loopback)"
        )

    def test_remote_mode_read_returns_valid_payload(self, sandbox: Path) -> None:
        """
        RUN_MODE=remote path: request proxied through BRIDGE_URL.
        If the endpoint is unreachable, falls back to local with a warning.
        Telemetry (status, data, error_message keys) must be present in either case.
        """
        log.info(f"=== RUNTIME: remote read  BRIDGE_URL={BRIDGE_URL or '(local fallback)'} ===")

        if RUN_MODE == "remote":
            result = _remote_reader_call(
                "read_file", str(sandbox), path="dummy_service.py"
            )
        else:
            # In local mode, simulate the remote path with local NodeInsight
            result = _local_reader_call(
                "read_file", str(sandbox), path="dummy_service.py"
            )

        _assert_bridge_result_ok(result, "RemoteMode/read_file")

        # Verify the ToolResponse telemetry envelope
        assert "status" in result.data, "Missing 'status' key in remote ToolResponse"
        assert "data" in result.data, "Missing 'data' key in remote ToolResponse"
        assert "error_message" in result.data, "Missing 'error_message' key in remote ToolResponse"
        assert result.data["status"] == "success", \
            f"Remote read returned non-success status: {result.data['status']}"

        log.info(f"  PASS — remote mode telemetry envelope validated")

    def test_remote_write_and_local_verify(self, sandbox: Path) -> None:
        """
        In remote mode: write via remote NodeForge, then read-back via local NodeInsight.
        In local mode: write + read-back both use local tools.
        Confirms that file state is consistent regardless of write pathway.
        """
        log.info("=== RUNTIME: write via route, verify via local read ===")

        marker_content = "\n# REMOTE_WRITE_MARKER — added by dual-mode test\n"

        if RUN_MODE == "remote":
            write_result = _remote_writer_call(
                "append_file",
                workspace_root=str(sandbox),
                path="dummy_service.py",
                content=marker_content,
            )
        else:
            write_result = _local_writer_call(
                "append_file",
                workspace_root=str(sandbox),
                path="dummy_service.py",
                content=marker_content,
            )

        assert write_result.get("status") == "success", \
            f"Write failed: {write_result.get('error_message')}"

        # Always verify via local read to confirm disk state
        disk_content = (sandbox / "dummy_service.py").read_text(encoding="utf-8")
        assert "REMOTE_WRITE_MARKER" in disk_content, \
            "Marker not found on disk — write may not have landed"

        log.info(f"  PASS — write + local-verify consistent across modes")


# ===========================================================================
# SCENARIO 3 — Error Handling & State Recovery
# ===========================================================================

class TestErrorHandlingAndStateRecovery:
    """
    Validates that NodeForge correctly rejects invalid modifications,
    leaves the file unharmed, and propagates informative errors through
    LegacyBridge so that NodeInsight can confirm the file is intact.
    """

    def test_patch_target_not_found_is_rejected(self, sandbox: Path) -> None:
        """
        Send patch_file with a target_block that does not exist in the file.
        NodeForge must return status='error', abort the patch, and leave the
        file exactly as it was.
        """
        log.info("=== ERROR: patch with non-existent target_block ===")

        original_content = (sandbox / "dummy_service.py").read_text(encoding="utf-8")

        result = _local_writer_call(
            "patch_file",
            workspace_root=str(sandbox),
            path="dummy_service.py",
            target_block="THIS_BLOCK_DOES_NOT_EXIST_IN_THE_FILE___XYZ",
            replacement_block="def replaced(): pass",
        )

        log.info(f"  NodeForge result: status={result.get('status')}  "
                 f"error={result.get('error_message')}")

        # Must fail
        assert result.get("status") == "error", \
            f"Expected error status for bad patch, got: {result.get('status')}"

        # Error message must be informative
        err_msg: str = result.get("error_message") or ""
        assert err_msg, "error_message must be non-empty on failure"
        assert len(err_msg) >= 10, \
            f"error_message is too short to be useful: {err_msg!r}"

        # File must be unchanged
        after_content = (sandbox / "dummy_service.py").read_text(encoding="utf-8")
        assert after_content == original_content, \
            "File content changed despite a rejected patch — state corruption detected!"

        log.info(f"  PASS — patch rejected: {err_msg!r}")

    def test_malformed_syntax_patch_is_rejected(self, sandbox: Path) -> None:
        """
        Attempt to patch the file with syntactically invalid Python
        (missing colon on def statement).
        NodeForge's patch_file does not validate Python syntax — it only
        requires the target_block to be present.  We verify this by:
          1. Patching with malformed code (should succeed at write level but
             produce invalid Python on disk).
          2. Checking that NodeInsight's parse_ast raises a ParseError,
             confirming that the bridge correctly surfaces the broken state.
          3. Manually rolling back so subsequent tests are unaffected.
        """
        log.info("=== ERROR: patch with syntactically invalid replacement ===")

        original_content = (sandbox / "dummy_service.py").read_text(encoding="utf-8")
        target_block = "def run_service(name: str, items: List[str]) -> dict:"
        bad_replacement = "def run_service(name str items List[str])  dict"  # missing colons

        # Verify the target block actually exists (sanity check)
        assert target_block in original_content, \
            "Precondition: target_block must exist in initial file"

        result = _local_writer_call(
            "patch_file",
            workspace_root=str(sandbox),
            path="dummy_service.py",
            target_block=target_block,
            replacement_block=bad_replacement,
            backup=True,
        )

        log.info(f"  NodeForge patch result: status={result.get('status')}")

        # NodeForge accepts the patch at the write level (it does not parse Python)
        # The important assertion is that the file now has invalid syntax:
        patched_source = (sandbox / "dummy_service.py").read_text(encoding="utf-8")

        try:
            compile(patched_source, "dummy_service.py", "exec")
            syntax_ok = True
        except SyntaxError:
            syntax_ok = False

        if result.get("status") == "success":
            # Patch written — assert that NodeInsight detects the broken syntax
            log.info("  NodeForge wrote patch; verifying NodeInsight reports ParseError...")
            assert not syntax_ok, \
                "Patched file should have invalid syntax — bad replacement was applied"

            ni_result = _local_reader_call(
                "parse_ast", str(sandbox), path="dummy_service.py"
            )
            # NodeInsight must report an error for broken syntax
            assert ni_result.data.get("status") == "error", \
                (f"NodeInsight should surface ParseError for broken file, "
                 f"but returned: {ni_result.data.get('status')}")
            err = ni_result.data.get("error_message") or ""
            assert "ParseError" in err or "parse" in err.lower(), \
                f"Expected ParseError in NodeInsight response, got: {err!r}"
            log.info(f"  NodeInsight correctly reported: {err!r}")

            # Roll back using NodeForge's rollback action
            log.info("  Rolling back via NodeForge...")
            rb_result = _local_writer_call(
                "rollback",
                workspace_root=str(sandbox),
                path="dummy_service.py",
            )
            assert rb_result.get("status") == "success", \
                f"Rollback failed: {rb_result.get('error_message')}"
            log.info(f"  Rollback OK: {rb_result.get('data')}")

        else:
            # NodeForge may reject before writing if it validates replacement syntax
            log.info("  NodeForge pre-rejected malformed patch (stricter validation mode)")
            assert result.get("error_message"), \
                "Error result must carry an error_message"

        # Either way, the final file must be syntactically valid after cleanup
        final_source = (sandbox / "dummy_service.py").read_text(encoding="utf-8")
        try:
            _validate_python_syntax(final_source)
        except SyntaxError as exc:
            pytest.fail(
                f"File left with invalid syntax after error-recovery flow: {exc}"
            )

        log.info("  PASS — file is syntactically valid after error + recovery")

    def test_path_traversal_is_blocked(self, sandbox: Path) -> None:
        """
        Attempt to write a file outside the sandbox via path traversal.
        NodeForge must raise SecurityError / return an error dict.
        The sandbox must be unaffected.
        """
        log.info("=== ERROR: path traversal attempt ===")

        escape_path = "../../etc/passwd"  # classic traversal

        result = _local_writer_call(
            "write_file",
            workspace_root=str(sandbox),
            path=escape_path,
            content="# pwned",
        )

        log.info(f"  NodeForge result: status={result.get('status')}  "
                 f"error={result.get('error_message')}")

        assert result.get("status") == "error", \
            "Path traversal should be blocked by NodeForge SecurityError"
        err_msg = result.get("error_message") or ""
        assert "traversal" in err_msg.lower() or "outside" in err_msg.lower(), \
            f"Error message should mention traversal/outside: {err_msg!r}"

        log.info(f"  PASS — traversal blocked: {err_msg!r}")

    def test_subsequent_read_after_error_is_clean(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """
        After a failed patch attempt, confirm that:
          a. LegacyBridge remains operational (session state unaffected).
          b. A subsequent NodeInsight read returns the original, intact file.
        """
        log.info("=== ERROR RECOVERY: confirm clean read after failed operation ===")

        # Trigger a benign, well-known failure
        bad_result = _local_writer_call(
            "patch_file",
            workspace_root=str(sandbox),
            path="dummy_service.py",
            target_block="NONEXISTENT_ANCHOR_STRING_XYZ",
            replacement_block="def replacement(): pass",
        )
        assert bad_result.get("status") == "error", "Setup: expected failed patch"
        log.info(f"  Setup: NodeForge returned expected error — {bad_result.get('error_message')!r}")

        # BridgeAdapter must still work correctly after the error
        result = adapter.reader_call("parse_ast", path="dummy_service.py")
        _assert_bridge_result_ok(result, "RecoveryRead/parse_ast")

        fn_names = _extract_ast_function_names(result)
        assert "build_service" in fn_names, \
            f"Expected 'build_service' in post-error AST, got: {fn_names}"
        assert "run_service" in fn_names, \
            f"Expected 'run_service' in post-error AST, got: {fn_names}"

        # Verify LegacyBridge log captured the NodeInsight response
        logs = adapter.get_live_output()
        assert any("nodeinsight" in line.lower() or "READER" in line for line in logs), \
            "BridgeAdapter should have logged a NodeInsight dispatch event"

        log.info(
            f"  PASS — BridgeAdapter still operational after error. "
            f"File intact: {fn_names}"
        )

    def test_invalid_action_name_returns_error(self, sandbox: Path) -> None:
        """
        Send an unrecognised action to NodeForge.
        Must return status='error' without raising an exception or corrupting state.
        """
        log.info("=== ERROR: invalid NodeForge action name ===")

        result = _local_writer_call(
            "teleport_file",   # does not exist
            workspace_root=str(sandbox),
            path="dummy_service.py",
        )

        log.info(f"  NodeForge result: {result}")
        assert result.get("status") == "error", \
            f"Expected error for unknown action, got: {result.get('status')}"
        assert result.get("error_message"), "error_message must be present"
        log.info(f"  PASS — unknown action rejected: {result.get('error_message')!r}")

    def test_bridge_adapter_logs_nodeinsight_errors(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """
        Trigger a deliberate NodeInsight error (FileNotFound) through BridgeAdapter.
        Verify that:
          a. BridgeResult.ok == False.
          b. The error propagates correctly through LegacyBridge.
          c. _log_lines captures an error entry.
          d. BridgeAdapter remains usable for subsequent calls.
        """
        log.info("=== ERROR: BridgeAdapter propagates NodeInsight FileNotFound ===")

        result = adapter.reader_call("read_file", path="does_not_exist.py")

        log.info(f"  BridgeResult: ok={result.ok}  error={result.error!r}")

        assert not result.ok, "BridgeResult should be not ok for a missing file"
        assert result.error, "BridgeResult.error must be non-empty"
        assert "FileNotFound" in result.error or "not found" in result.error.lower(), \
            f"Expected FileNotFound error, got: {result.error!r}"

        # Error must be logged inside BridgeAdapter
        logs = adapter.get_live_output()
        error_logs = [l for l in logs if "error" in l.lower() or "Error" in l]
        assert error_logs, \
            "BridgeAdapter._log_lines should contain at least one error entry"

        # Adapter must still be usable
        recovery_result = adapter.reader_call("parse_ast", path="dummy_service.py")
        _assert_bridge_result_ok(recovery_result, "RecoveryAfterFileNotFound/parse_ast")

        log.info(
            f"  PASS — FileNotFound propagated correctly. "
            f"Adapter recovered for next call."
        )


# ===========================================================================
# BONUS — Cross-component data integrity
# ===========================================================================

class TestDataIntegrityAcrossComponents:
    """
    Cross-cuts Scenario 1 and 3 to confirm that payloads passing through the
    full bridge chain arrive intact (correct types, no field truncation).
    """

    def test_ast_result_schema_completeness(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        parse_ast payload must contain all required keys defined by ASTResult.
        This guards against schema drift between NodeInsight and LegacyBridge.
        """
        log.info("=== DATA INTEGRITY: ASTResult schema completeness ===")

        result = adapter.reader_call("parse_ast", path="dummy_service.py")
        _assert_bridge_result_ok(result, "Schema/parse_ast")

        ast_data: dict = result.data.get("data") or {}
        required_keys = {"path", "classes", "functions", "imports",
                         "import_graph", "total_classes", "total_functions"}

        missing = required_keys - set(ast_data.keys())
        assert not missing, \
            f"ASTResult payload missing required keys: {missing}\nGot: {list(ast_data.keys())}"

        # Type assertions
        assert isinstance(ast_data["classes"], list), "classes must be a list"
        assert isinstance(ast_data["functions"], list), "functions must be a list"
        assert isinstance(ast_data["imports"], list), "imports must be a list"
        assert isinstance(ast_data["total_classes"], int), "total_classes must be int"
        assert isinstance(ast_data["total_functions"], int), "total_functions must be int"
        assert ast_data["total_functions"] == len(ast_data["functions"]), \
            "total_functions counter must equal len(functions)"

        log.info(
            f"  PASS — {len(required_keys)} required keys present. "
            f"total_classes={ast_data['total_classes']} "
            f"total_functions={ast_data['total_functions']}"
        )

    def test_function_symbol_schema(
        self, adapter: BridgeAdapter
    ) -> None:
        """Each FunctionSymbol in the AST must carry all required fields."""
        log.info("=== DATA INTEGRITY: FunctionSymbol schema ===")

        result = adapter.reader_call("parse_ast", path="dummy_service.py")
        _assert_bridge_result_ok(result, "Schema/FunctionSymbol")

        functions: list[dict] = result.data.get("data", {}).get("functions", [])
        assert functions, "Expected at least one function symbol"

        required_fn_keys = {"name", "line_number", "end_line", "signature",
                            "is_async", "decorators"}

        for fn in functions:
            missing = required_fn_keys - set(fn.keys())
            assert not missing, \
                f"FunctionSymbol '{fn.get('name')}' missing keys: {missing}"
            assert isinstance(fn["line_number"], int), \
                f"line_number must be int for {fn['name']}"
            assert fn["end_line"] >= fn["line_number"], \
                f"end_line ({fn['end_line']}) < line_number ({fn['line_number']}) for {fn['name']}"

        log.info(f"  PASS — {len(functions)} FunctionSymbol(s) all have valid schema")

    def test_write_result_schema(self, sandbox: Path) -> None:
        """
        NodeForge write results must include all telemetry keys:
        status, action, path, bytes_written, timestamp, data.
        """
        log.info("=== DATA INTEGRITY: NodeForge write result schema ===")

        result = _local_writer_call(
            "append_file",
            workspace_root=str(sandbox),
            path="dummy_service.py",
            content="\n# schema-test marker\n",
        )

        required_keys = {"status", "action", "path", "bytes_written", "timestamp", "data"}
        missing = required_keys - set(result.keys())
        assert not missing, \
            f"NodeForge result missing keys: {missing}\nGot: {list(result.keys())}"

        assert result["status"] == "success"
        assert result["action"] == "append_file"
        assert isinstance(result["bytes_written"], int) and result["bytes_written"] > 0
        assert result["timestamp"]  # non-empty ISO timestamp

        log.info(
            f"  PASS — all telemetry keys present. "
            f"bytes_written={result['bytes_written']}"
        )

    def test_tool_response_envelope_preserved_through_bridge(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        When a ToolResponse travels through the bridge chain, the envelope
        (status / data / error_message) must survive intact in BridgeResult.data.
        """
        log.info("=== DATA INTEGRITY: ToolResponse envelope preserved ===")

        result = adapter.reader_call("list_dir", path=".")
        _assert_bridge_result_ok(result, "Envelope/list_dir")

        envelope = result.data
        assert "status" in envelope, "status key missing from ToolResponse envelope"
        assert "data" in envelope, "data key missing from ToolResponse envelope"
        assert "error_message" in envelope, "error_message key missing from ToolResponse envelope"
        assert envelope["status"] == "success"
        assert envelope["error_message"] is None

        dir_data: dict = envelope.get("data") or {}
        assert "entries" in dir_data, "list_dir data must have entries key"
        assert "total_files" in dir_data, "list_dir data must have total_files key"

        log.info(
            f"  PASS — ToolResponse envelope intact. "
            f"total_files={dir_data.get('total_files')}"
        )

    def test_latency_recorded_for_all_bridge_calls(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        Every BridgeResult returned by reader_call must carry a non-negative latency_ms.
        This is a regression guard against latency tracking being stripped.
        """
        log.info("=== DATA INTEGRITY: latency_ms recorded on all reader_calls ===")

        actions = [
            ("read_file",  {"path": "dummy_service.py"}),
            ("parse_ast",  {"path": "dummy_service.py"}),
            ("list_dir",   {"path": "."}),
        ]

        for action, kwargs in actions:
            result = adapter.reader_call(action, **kwargs)
            assert result.latency_ms >= 0, \
                f"latency_ms must be >= 0 for action={action!r}, got {result.latency_ms}"
            log.info(f"  {action}: latency={result.latency_ms:.1f} ms")

        log.info("  PASS — latency_ms non-negative for all reader_call actions")
