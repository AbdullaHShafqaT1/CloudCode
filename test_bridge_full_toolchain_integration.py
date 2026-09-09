"""
test_bridge_full_toolchain_integration.py
=========================================
Exhaustive End-to-End Integration Test Suite validating the full hybrid toolchain:

    ┌──────────────┐
    │ LegacyBridge │ (Router / Session Dispatcher)
    └──────┬───────┘
           │
           ├──► NodeInsight (Code reader, AST parser, syntax checker)
           ├──► NodeForge   (Atomic file writer, patcher, rollback engine)
           ├──► NodePulse   (Local terminal & unit test executor)
           └──► NodeLink    (Remote execution dispatcher for Cloud/Colab runtimes)

Scenarios Covered
-----------------
1. THE CLOSED-LOOP HYBRID CYCLE (5-Step End-to-End Lifecycle):
   Step 1 (Inspect via NodeInsight): LegacyBridge dispatches to NodeInsight to
          read and parse model_runner.py containing an intentional runtime issue.
   Step 2 (Local Pre-check via NodePulse): LegacyBridge executes
          `pytest test_model_runner.py` locally via NodePulse. Asserts exit_code != 0.
   Step 3 (Patch via NodeForge): LegacyBridge instructs NodeForge to rewrite / patch
          model_runner.py with the corrected implementation.
   Step 4 (Local Re-validation via NodePulse): LegacyBridge reruns local pytest
          suite via NodePulse. Asserts exit_code == 0 and all tests pass.
   Step 5 (Remote Dispatch via NodeLink): LegacyBridge dispatches the verified
          model_runner.py payload to NodeLink to execute on a remote runtime.
          Verifies telemetry, exit code, and zero data truncation.

2. DUAL REMOTE TRANSPORT MODES:
   - Mock Runner Mode (RUN_MODE=mock): Spins up an in-process mock HTTP server
     simulating the remote cloud runner so the entire test runs locally and reliably.
   - Live Cloud Mode (RUN_MODE=remote): Executes against live Colab/cloud runtime
     using REMOTE_ENDPOINT_URL and REMOTE_AUTH_TOKEN.

3. TOOL FAILOVER & BOUNDARY RESILIENCE:
   - Simulates remote execution failure inside NodeLink (e.g. 500 error or timeout).
   - Verifies LegacyBridge captures the failure cleanly, logs the traceback without
     crashing, and maintains session state.
   - Verifies subsequent local calls to NodeInsight, NodeForge, and NodePulse continue
     operating normally with zero state pollution.

4. SERIALIZATION & ZERO BYTE TRUNCATION INVARIANTS:
   - Validates that code payloads, multi-kilobyte AST trees, and JSON telemetry
     traverse all tool boundaries with bit-for-bit fidelity and zero byte truncation.

Run Commands
------------
# Default Mock mode:
    pytest test_bridge_full_toolchain_integration.py -v --tb=short

# Standalone execution:
    python test_bridge_full_toolchain_integration.py

# Live cloud mode (with remote endpoint):
    RUN_MODE=remote REMOTE_ENDPOINT_URL=https://... REMOTE_AUTH_TOKEN=... \\
        pytest test_bridge_full_toolchain_integration.py -v -s
"""

from __future__ import annotations

import ast
import asyncio
import http.server
import json
import logging
import os
import shutil
import sys
import tempfile
import textwrap
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Optional

import pytest

# ---------------------------------------------------------------------------
# Structured, Timestamped Logger with Tool Identifiers
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("FullToolchain")

def log_step(tool: str, msg: str) -> None:
    """Emit formatted, timestamped log tagged by component."""
    log.info(f"[{tool.upper():<9}] {msg}")

# ---------------------------------------------------------------------------
# sys.path Bootstrap — Ensure all 5 components are importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT   = Path(__file__).parent.resolve()
_LEGACYBRIDGE   = _PROJECT_ROOT / "LegacyBridge"
_LEGACYNODE_PKG = _LEGACYBRIDGE / "legacynode"
_NODEINSIGHT    = _PROJECT_ROOT / "NodeInsight"
_NODEFORGE      = _PROJECT_ROOT / "NodeForge"
_NODEPULSE      = _PROJECT_ROOT / "NodePulse"
_NODELINK       = _PROJECT_ROOT / "NodeLink"

for _p in [
    str(_PROJECT_ROOT),
    str(_LEGACYBRIDGE),
    str(_LEGACYNODE_PKG),
    str(_NODEINSIGHT),
    str(_NODEFORGE),
    str(_NODEPULSE),
    str(_NODELINK),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Component Imports & Availability Verification
# ---------------------------------------------------------------------------
try:
    from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult
    from legacynode.tools.node_insight_bridge import NodeInsightBridge
    from legacynode.tools.node_pulse_bridge import NodePulseBridge
    from legacynode.tools.node_link_bridge import NodeLinkBridge
    log_step("BRIDGE", "LegacyBridge & Bridge Adapters imported OK")
except ImportError as exc:
    pytest.exit(f"Failed to import LegacyBridge: {exc}", returncode=1)

try:
    from file_reader_tool import FileReaderTool
    log_step("INSIGHT", "NodeInsight (FileReaderTool) imported OK")
except ImportError as exc:
    pytest.exit(f"Failed to import NodeInsight: {exc}", returncode=1)

try:
    from tools.file_writer_tool import FileWriterTool
    log_step("FORGE", "NodeForge (FileWriterTool) imported OK")
except ImportError as exc:
    pytest.exit(f"Failed to import NodeForge: {exc}", returncode=1)

try:
    from terminal_executor import TerminalExecutorTool
    log_step("PULSE", "NodePulse (TerminalExecutorTool) imported OK")
except ImportError as exc:
    pytest.exit(f"Failed to import NodePulse: {exc}", returncode=1)

try:
    from nodelink import NodeLink, ConnectionHandle, ApiResponse, RemoteJobHandle, JobResult
    log_step("LINK", "NodeLink (Gateway & Models) imported OK")
except ImportError as exc:
    pytest.exit(f"Failed to import NodeLink: {exc}", returncode=1)


# ---------------------------------------------------------------------------
# Runtime Mode Configuration
# ---------------------------------------------------------------------------
RUN_MODE = os.environ.get("RUN_MODE", "mock").strip().lower()
REMOTE_ENDPOINT_URL = os.environ.get("REMOTE_ENDPOINT_URL", os.environ.get("BRIDGE_URL", "")).strip()
REMOTE_AUTH_TOKEN   = os.environ.get("REMOTE_AUTH_TOKEN", os.environ.get("AUTH_TOKEN", "mock_secret_token")).strip()

log_step("CONFIG", f"Active RUN_MODE={RUN_MODE} | REMOTE_ENDPOINT_URL={REMOTE_ENDPOINT_URL or '(local mock)'}")


# ---------------------------------------------------------------------------
# Test Subjects: model_runner.py (buggy & fixed) and test_model_runner.py
# ---------------------------------------------------------------------------
BUGGY_MODEL_RUNNER_CODE = textwrap.dedent("""\
    \"\"\"
    model_runner.py — Hybrid Pipeline Test Subject
    Contains a deliberate runtime bug (load_weights raises NotImplementedError).
    \"\"\"
    import os
    import sys
    from typing import Dict, List, Any


    class ModelRunner:
        \"\"\"Executes ML inference tasks locally or remotely.\"\"\"

        def __init__(self, model_name: str = "resnet50", batch_size: int = 32) -> None:
            self.model_name = model_name
            self.batch_size = batch_size
            self.weights_loaded = False

        def load_weights(self) -> bool:
            # DELIBERATE BUG: Should load weights, but raises an error
            raise NotImplementedError("Model weights loader has not been implemented!")

        def predict(self, inputs: List[float]) -> Dict[str, Any]:
            if not self.weights_loaded:
                raise RuntimeError("Cannot perform inference before loading weights")
            return {
                "status": "success",
                "model": self.model_name,
                "batch_size": self.batch_size,
                "predictions": [round(x * 1.5, 4) for x in inputs],
                "telemetry": {"device": "gpu" if os.environ.get("CUDA_VISIBLE_DEVICES") else "cpu"},
            }


    def run_benchmark(batch_size: int = 16) -> Dict[str, Any]:
        runner = ModelRunner(batch_size=batch_size)
        runner.load_weights()
        return runner.predict([1.0, 2.0, 3.0])
""")

FIXED_MODEL_RUNNER_CODE = textwrap.dedent("""\
    \"\"\"
    model_runner.py — Hybrid Pipeline Test Subject (CORRECTED BY NODEFORGE)
    The runtime bug in load_weights has been fixed.
    \"\"\"
    import os
    import sys
    from typing import Dict, List, Any


    class ModelRunner:
        \"\"\"Executes ML inference tasks locally or remotely.\"\"\"

        def __init__(self, model_name: str = "resnet50", batch_size: int = 32) -> None:
            self.model_name = model_name
            self.batch_size = batch_size
            self.weights_loaded = False

        def load_weights(self) -> bool:
            # BUG FIXED: Weights successfully loaded
            self.weights_loaded = True
            return True

        def predict(self, inputs: List[float]) -> Dict[str, Any]:
            if not self.weights_loaded:
                raise RuntimeError("Cannot perform inference before loading weights")
            return {
                "status": "success",
                "model": self.model_name,
                "batch_size": self.batch_size,
                "predictions": [round(x * 1.5, 4) for x in inputs],
                "telemetry": {"device": "gpu" if os.environ.get("CUDA_VISIBLE_DEVICES") else "cpu"},
            }


    def run_benchmark(batch_size: int = 16) -> Dict[str, Any]:
        runner = ModelRunner(batch_size=batch_size)
        runner.load_weights()
        return runner.predict([1.0, 2.0, 3.0])
""")

TEST_MODEL_RUNNER_CODE = textwrap.dedent("""\
    \"\"\"
    test_model_runner.py — Unit test suite executed locally via NodePulse.
    \"\"\"
    import pytest
    from model_runner import ModelRunner, run_benchmark


    def test_model_initialization():
        runner = ModelRunner(model_name="bert-base", batch_size=8)
        assert runner.model_name == "bert-base"
        assert runner.batch_size == 8
        assert runner.weights_loaded is False


    def test_model_load_and_predict():
        runner = ModelRunner(model_name="resnet50", batch_size=4)
        assert runner.load_weights() is True
        result = runner.predict([0.1, 0.2, 0.3])
        assert result["status"] == "success"
        assert len(result["predictions"]) == 3
        assert result["predictions"][0] == 0.15


    def test_run_benchmark():
        res = run_benchmark(batch_size=16)
        assert res["status"] == "success"
        assert res["batch_size"] == 16
""")


# ---------------------------------------------------------------------------
# In-Process HTTP Mock Cloud Server for Mock Transport Mode
# ---------------------------------------------------------------------------
class MockCloudServerHandler(http.server.BaseHTTPRequestHandler):
    """
    Mock Colab / Remote GPU Runner server simulating remote endpoints:
    - POST /execute: Submits command/notebook payload
    - GET  /jobs/{job_id}: Polls status & execution results
    - GET  /health: Worker healthcheck
    - POST /simulate_fail: Triggers simulated 500 error for failover tests
    """

    jobs: Dict[str, Dict[str, Any]] = {}
    should_fail: bool = False

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP request logging
        pass

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "gpu": True,
                "device": "NVIDIA A100-SXM4-40GB",
                "vram_free_gb": 38.5,
            })
        elif self.path.startswith("/jobs/"):
            job_id = self.path.split("/jobs/")[-1]
            if job_id in self.jobs:
                self._send_json(200, self.jobs[job_id])
            else:
                self._send_json(404, {"error": f"Job {job_id} not found"})
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(raw_body)
        except Exception:
            payload = {}

        if self.path == "/simulate_fail" or MockCloudServerHandler.should_fail:
            self._send_json(500, {
                "error": "RemoteRuntimeError: GPU out of memory / dependency missing",
                "traceback": "Traceback (most recent call last):\n  File 'runner.py', line 42, in <module>\nRuntimeError: CUDA OOM",
            })
            return

        if self.path in ("/execute", "/api/kernels/execute"):
            job_id = f"job_remote_{int(time.time() * 1000)}"
            cmd = payload.get("command") or payload.get("code") or ""
            params = payload.get("params") or {}

            # Store completed job simulation
            MockCloudServerHandler.jobs[job_id] = {
                "status": "COMPLETED",
                "job_id": job_id,
                "result": {
                    "exit_code": 0,
                    "stdout": (
                        f"[Remote Colab/GPU] Successfully executed model_runner.py\n"
                        f"Accuracy: 0.984 | Latency: 12.4ms | Payload Size: {len(cmd)} bytes\n"
                    ),
                    "stderr": "",
                    "metrics": {
                        "accuracy": 0.984,
                        "epochs": params.get("epochs", 5),
                        "batch_size": params.get("batch_size", 32),
                        "duration_ms": 142.0,
                    },
                    "payload_echo_length": len(cmd),
                },
            }
            self._send_json(200, {"job_id": job_id, "status": "SUBMITTED"})
        else:
            self._send_json(200, {"status": "ok", "received": payload})


class MockCloudServer:
    """Lifecycle manager for the in-process mock cloud server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.server = http.server.ThreadingHTTPServer((host, port), MockCloudServerHandler)
        self.port = self.server.server_address[1]
        self.endpoint = f"http://{host}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> str:
        MockCloudServerHandler.jobs.clear()
        MockCloudServerHandler.should_fail = False
        self.thread.start()
        log_step("LINK", f"Mock Cloud Server started at {self.endpoint}")
        return self.endpoint

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        log_step("LINK", "Mock Cloud Server stopped")


# ---------------------------------------------------------------------------
# Pytest Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def mock_cloud_endpoint() -> Generator[str, None, None]:
    """Starts a local mock cloud server for the test session."""
    server = MockCloudServer()
    endpoint = server.start()
    yield endpoint
    server.stop()


@pytest.fixture()
def hybrid_sandbox(tmp_path: Path) -> Path:
    """
    Sets up a clean workspace sandbox with the initial buggy model_runner.py
    and the unit test suite test_model_runner.py.
    """
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    # 1. Write the buggy model_runner.py
    runner_file = ws / "model_runner.py"
    runner_file.write_text(BUGGY_MODEL_RUNNER_CODE, encoding="utf-8")

    # 2. Write test_model_runner.py
    test_file = ws / "test_model_runner.py"
    test_file.write_text(TEST_MODEL_RUNNER_CODE, encoding="utf-8")

    log_step("SANDBOX", f"Initialized sandbox at {ws} with model_runner.py & test_model_runner.py")
    return ws


@pytest.fixture()
def bridge_adapter(hybrid_sandbox: Path) -> BridgeAdapter:
    """Constructs a BridgeAdapter configured in LOCAL mode targeting the sandbox."""
    return BridgeAdapter(
        mode=BridgeMode.LOCAL,
        workspace_root=str(hybrid_sandbox),
    )


# ===========================================================================
# SCENARIO 1: The Closed-Loop Hybrid Cycle (5-Step Pipeline)
# ===========================================================================

class TestClosedLoopHybridCycle:
    """
    Validates the entire 5-step closed-loop hybrid development cycle:
    NodeInsight (read) -> NodePulse (fail) -> NodeForge (patch) -> NodePulse (pass) -> NodeLink (remote).
    """

    def test_full_closed_loop_hybrid_pipeline(
        self,
        hybrid_sandbox: Path,
        bridge_adapter: BridgeAdapter,
        mock_cloud_endpoint: str,
    ) -> None:
        log_step("TEST", "=== Beginning Scenario 1: Closed-Loop Hybrid Cycle ===")

        # -------------------------------------------------------------------
        # Step 1 (Inspect via NodeInsight): Read and AST-parse model_runner.py
        # -------------------------------------------------------------------
        log_step("INSIGHT", "Step 1: Inspecting model_runner.py via LegacyBridge.reader_call")
        read_res: BridgeResult = bridge_adapter.reader_call("read_file", path="model_runner.py")
        assert read_res.ok, f"NodeInsight failed to read model_runner.py: {read_res.error}"
        initial_content = read_res.data["data"]["content"]
        assert "NotImplementedError" in initial_content, "Initial content must contain the deliberate bug"

        ast_res: BridgeResult = bridge_adapter.reader_call("parse_ast", path="model_runner.py")
        assert ast_res.ok, f"NodeInsight failed to parse AST: {ast_res.error}"
        classes = [cls["name"] for cls in ast_res.data["data"].get("classes", [])]
        assert "ModelRunner" in classes, "AST must identify the ModelRunner class"
        log_step("INSIGHT", f"Step 1 OK — File read and AST parsed successfully ({len(initial_content)} bytes)")

        # -------------------------------------------------------------------
        # Step 2 (Local Pre-check via NodePulse): Run pytest locally (MUST FAIL)
        # -------------------------------------------------------------------
        log_step("PULSE", "Step 2: Running local unit tests via LegacyBridge.terminal_call (expecting failure)")
        pulse_precheck: BridgeResult = bridge_adapter.terminal_call(
            command=f"{sys.executable} -m pytest test_model_runner.py -v",
            cwd=str(hybrid_sandbox),
            timeout=30,
        )
        pulse_data = pulse_precheck.data or {}
        assert pulse_data.get("exit_code") != 0, (
            f"Pre-check tests must FAIL on buggy code, got exit_code={pulse_data.get('exit_code')}"
        )
        assert "NotImplementedError" in pulse_data.get("stdout", "") or "FAILED" in pulse_data.get("stdout", ""), (
            "Stdout must report the deliberate NotImplementedError failure"
        )
        log_step("PULSE", f"Step 2 OK — Local test failed as expected (exit_code={pulse_data.get('exit_code')})")

        # -------------------------------------------------------------------
        # Step 3 (Patch via NodeForge): Rewrite/patch model_runner.py
        # -------------------------------------------------------------------
        log_step("FORGE", "Step 3: Patching model_runner.py with corrected code via LegacyBridge.writer_call")
        write_res: BridgeResult = bridge_adapter.writer_call(
            action="write_file",
            path="model_runner.py",
            content=FIXED_MODEL_RUNNER_CODE,
        )
        assert write_res.ok, f"NodeForge failed to patch model_runner.py: {write_res.error}"
        written_bytes = write_res.data.get("bytes_written", len(FIXED_MODEL_RUNNER_CODE))
        assert written_bytes > 0, "NodeForge must confirm positive byte count written"

        # Sanity re-read via NodeInsight to verify patch application on disk
        verify_read: BridgeResult = bridge_adapter.reader_call("read_file", path="model_runner.py")
        assert verify_read.ok
        patched_code = verify_read.data["data"]["content"]
        assert "BUG FIXED: Weights successfully loaded" in patched_code
        log_step("FORGE", f"Step 3 OK — Code patched atomically ({written_bytes} bytes written)")

        # -------------------------------------------------------------------
        # Step 4 (Local Re-validation via NodePulse): Rerun pytest (MUST PASS)
        # -------------------------------------------------------------------
        log_step("PULSE", "Step 4: Re-running local unit tests via LegacyBridge.terminal_call (expecting success)")
        pulse_recheck: BridgeResult = bridge_adapter.terminal_call(
            command=f"{sys.executable} -m pytest test_model_runner.py -v",
            cwd=str(hybrid_sandbox),
            timeout=30,
        )
        assert pulse_recheck.ok, f"NodePulse call error: {pulse_recheck.error}"
        recheck_data = pulse_recheck.data
        assert recheck_data["exit_code"] == 0, (
            f"Re-check tests must PASS after patch! stdout:\n{recheck_data['stdout']}\nstderr:\n{recheck_data['stderr']}"
        )
        assert "passed" in recheck_data["stdout"], "Stdout must report tests passed"
        log_step("PULSE", "Step 4 OK — All local unit tests passed cleanly (exit_code=0)")

        # -------------------------------------------------------------------
        # Step 5 (Remote Dispatch via NodeLink): Dispatch execution to remote runtime
        # -------------------------------------------------------------------
        target_endpoint = REMOTE_ENDPOINT_URL if RUN_MODE == "remote" else mock_cloud_endpoint
        log_step("LINK", f"Step 5: Dispatching model_runner.py to remote runtime ({target_endpoint}) via NodeLink")

        link_config = {
            "endpoint": target_endpoint,
            "auth_type": "BEARER_TOKEN",
            "token": REMOTE_AUTH_TOKEN,
            "adapter": "generic",
        }

        # Dispatch execution and wait for completion
        link_res: BridgeResult = bridge_adapter.link_call(
            action="execute_and_wait",
            target_id="cloud_gpu_target",
            config=link_config,
            command_or_notebook=patched_code,
            params={"batch_size": 32, "epochs": 10, "eval_benchmark": True},
            timeout=30,
        )

        assert link_res.ok, f"NodeLink remote dispatch failed: {link_res.error}"
        link_data = link_res.data
        assert link_data["status"] == "success", f"Remote execution returned non-success: {link_data}"
        assert link_data["job_status"] == "COMPLETED", f"Remote job not completed: {link_data}"

        res_data = link_data.get("result_data", {})
        # Verify zero truncation and execution results
        assert res_data.get("exit_code") == 0, f"Remote execution exited non-zero: {res_data}"
        assert "metrics" in res_data or "output" in res_data, "Remote result must include telemetry metrics or output"

        # Disconnect gracefully
        disc_res: BridgeResult = bridge_adapter.link_call("disconnect", target_id="cloud_gpu_target")
        assert disc_res.ok, "NodeLink disconnect should succeed"

        log_step("BRIDGE", "=== Scenario 1: Closed-Loop Hybrid Cycle PASSED (All 5 components verified) ===")


# ===========================================================================
# SCENARIO 2: Dual Remote Transport Modes (Mock vs Remote)
# ===========================================================================

class TestDualRemoteTransportModes:
    """
    Validates dual transport modes:
    - RUN_MODE=mock: Local mock runner server
    - RUN_MODE=remote: Live Cloud/Colab runner with environment credentials
    """

    def test_mock_transport_mode_healthcheck_and_dispatch(
        self,
        bridge_adapter: BridgeAdapter,
        mock_cloud_endpoint: str,
    ) -> None:
        """Verifies full healthcheck, auth injection, and job execution via Mock Server."""
        log_step("LINK", f"Testing Mock Transport Mode against {mock_cloud_endpoint}")

        config = {
            "endpoint": mock_cloud_endpoint,
            "auth_type": "BEARER_TOKEN",
            "token": "secret_mock_token_123",
            "adapter": "generic",
        }

        # 1. Connect
        conn_res = bridge_adapter.link_call("connect", target_id="mock_target", config=config)
        assert conn_res.ok
        assert conn_res.data["handle"]["status"] == "ESTABLISHED"

        # 2. Healthcheck ping via send_request
        health_res = bridge_adapter.link_call(
            "send_request",
            target_id="mock_target",
            endpoint="health",
            method="GET",
        )
        assert health_res.ok
        assert health_res.data["status_code"] == 200
        assert health_res.data["data"]["gpu"] is True

        # 3. Remote execution
        exec_res = bridge_adapter.link_call(
            "execute_and_wait",
            target_id="mock_target",
            command="import model_runner; print(model_runner.run_benchmark())",
            params={"epochs": 5},
        )
        assert exec_res.ok
        assert exec_res.data["job_status"] == "COMPLETED"

        # 4. Disconnect
        disc_res = bridge_adapter.link_call("disconnect", target_id="mock_target")
        assert disc_res.ok
        log_step("LINK", "Mock Transport Mode verified OK")

    @pytest.mark.skipif(
        RUN_MODE != "remote" or not REMOTE_ENDPOINT_URL,
        reason="RUN_MODE is not 'remote' or REMOTE_ENDPOINT_URL is not set",
    )
    def test_live_remote_cloud_mode(self, bridge_adapter: BridgeAdapter) -> None:
        """Verifies connection and remote execution against a live cloud runner."""
        log_step("LINK", f"Testing Live Remote Cloud Mode against {REMOTE_ENDPOINT_URL}")

        config = {
            "endpoint": REMOTE_ENDPOINT_URL,
            "auth_type": "BEARER_TOKEN",
            "token": REMOTE_AUTH_TOKEN,
            "adapter": "colab" if "colab" in REMOTE_ENDPOINT_URL.lower() else "generic",
        }

        conn_res = bridge_adapter.link_call("connect", target_id="live_cloud", config=config)
        assert conn_res.ok, f"Failed to connect to live remote cloud: {conn_res.error}"

        exec_res = bridge_adapter.link_call(
            "execute_and_wait",
            target_id="live_cloud",
            command="print('LegacyBridge + NodeLink Live Cloud Handshake OK')",
            timeout=60,
        )
        assert exec_res.ok, f"Live cloud execution failed: {exec_res.error}"
        bridge_adapter.link_call("disconnect", target_id="live_cloud")
        log_step("LINK", "Live Remote Cloud Mode verified OK")


# ===========================================================================
# SCENARIO 3: Tool Failover & Boundary Resilience
# ===========================================================================

class TestToolFailoverAndBoundaryResilience:
    """
    Validates that remote execution failures (HTTP 500, network error, or timeout)
    inside NodeLink:
    1. Are captured cleanly by LegacyBridge with tracebacks recorded without crashing.
    2. Do not corrupt LegacyBridge session state.
    3. Allow subsequent local operations (NodeInsight, NodeForge, NodePulse) to proceed normally.
    """

    def test_remote_failure_containment_and_local_tool_recovery(
        self,
        hybrid_sandbox: Path,
        bridge_adapter: BridgeAdapter,
        mock_cloud_endpoint: str,
    ) -> None:
        log_step("TEST", "=== Beginning Scenario 3: Failover & Boundary Resilience ===")

        # Step 3a: Trigger a deliberate remote failure in NodeLink
        MockCloudServerHandler.should_fail = True
        log_step("LINK", "Triggering deliberate simulated remote 500 error in NodeLink")

        fail_config = {
            "endpoint": mock_cloud_endpoint,
            "auth_type": "BEARER_TOKEN",
            "token": "token",
            "adapter": "generic",
        }

        res: BridgeResult = bridge_adapter.link_call(
            "execute_and_wait",
            target_id="failing_target",
            config=fail_config,
            command="print('This job will fail')",
            timeout=10,
        )

        # Assert LegacyBridge captured the error without crashing
        assert not res.ok, "Expected link_call to return ok=False on remote failure"
        assert res.error is not None, "Error description must be populated"
        log_step("BRIDGE", f"Captured expected remote failure: {res.error}")

        # Reset mock server failure flag
        MockCloudServerHandler.should_fail = False

        # Step 3b: Assert BridgeAdapter session is intact and ready
        assert bridge_adapter.is_ready or bridge_adapter.session_status is not None, (
            "BridgeAdapter must maintain valid session state after remote failure"
        )

        # Step 3c: Verify NodeInsight continues to work immediately
        log_step("INSIGHT", "Verifying NodeInsight operational after remote failure")
        read_res: BridgeResult = bridge_adapter.reader_call("read_file", path="model_runner.py")
        assert read_res.ok, "NodeInsight must remain operational"
        assert len(read_res.data["data"]["content"]) > 0

        # Step 3d: Verify NodeForge continues to work immediately
        log_step("FORGE", "Verifying NodeForge operational after remote failure")
        write_res: BridgeResult = bridge_adapter.writer_call(
            "append_file",
            path="model_runner.py",
            content="\n# Resilience audit line: verified intact\n",
        )
        assert write_res.ok, "NodeForge must remain operational"

        # Step 3e: Verify NodePulse continues to work immediately
        log_step("PULSE", "Verifying NodePulse operational after remote failure")
        pulse_res: BridgeResult = bridge_adapter.terminal_call(
            command=f"{sys.executable} -c \"print('NodePulse Local Sanity OK')\"",
            cwd=str(hybrid_sandbox),
        )
        assert pulse_res.ok, "NodePulse must remain operational"
        assert "NodePulse Local Sanity OK" in pulse_res.data["stdout"]

        log_step("BRIDGE", "=== Scenario 3: Failover & Boundary Resilience PASSED ===")


# ===========================================================================
# SCENARIO 4: Serialization & Zero Byte Truncation Invariants
# ===========================================================================

class TestSerializationAndTruncationInvariants:
    """
    Validates exact byte transmission and clean JSON serialization across tool boundaries.
    """

    def test_zero_truncation_across_pipeline(
        self,
        hybrid_sandbox: Path,
        bridge_adapter: BridgeAdapter,
        mock_cloud_endpoint: str,
    ) -> None:
        log_step("TEST", "=== Beginning Scenario 4: Zero Byte Truncation Invariants ===")

        # Generate a large multi-kilobyte script payload (100 functions)
        large_code_lines = [
            f"def fn_{i}():\n    \"\"\"Docstring for function {i}.\"\"\"\n    return {i} * 42\n"
            for i in range(100)
        ]
        large_payload = '"""Large benchmark module."""\n\n' + "\n".join(large_code_lines)
        expected_bytes = len(large_payload.encode("utf-8"))

        # 1. Write via NodeForge
        w_res = bridge_adapter.writer_call("write_file", path="large_bench.py", content=large_payload)
        assert w_res.ok

        # 2. Read via NodeInsight and assert byte fidelity
        r_res = bridge_adapter.reader_call("read_file", path="large_bench.py")
        assert r_res.ok
        read_content = r_res.data["data"]["content"]
        actual_bytes = len(read_content.encode("utf-8"))
        assert actual_bytes == expected_bytes, (
            f"Byte truncation detected in NodeInsight: expected {expected_bytes}, got {actual_bytes}"
        )

        # 3. Parse AST via NodeInsight
        ast_res = bridge_adapter.reader_call("parse_ast", path="large_bench.py")
        assert ast_res.ok
        functions = ast_res.data["data"].get("functions", [])
        assert len(functions) == 100, f"Expected 100 parsed functions, got {len(functions)}"

        # 4. Dispatch to NodeLink and verify zero truncation on mock echo
        config = {
            "endpoint": mock_cloud_endpoint,
            "auth_type": "BEARER_TOKEN",
            "token": "token",
            "adapter": "generic",
        }
        link_res = bridge_adapter.link_call(
            "execute_and_wait",
            target_id="payload_check",
            config=config,
            command=read_content,
        )
        assert link_res.ok
        echo_len = link_res.data["result_data"].get("payload_echo_length", 0)
        assert echo_len == len(read_content), (
            f"Byte truncation detected in NodeLink: sent {len(read_content)}, server echoed {echo_len}"
        )

        # Clean up
        bridge_adapter.link_call("disconnect", target_id="payload_check")
        log_step("BRIDGE", "=== Scenario 4: Zero Byte Truncation PASSED ===")


# ===========================================================================
# Standalone CLI Entry Point
# ===========================================================================
if __name__ == "__main__":
    log.info("Running full toolchain integration test suite via CLI...")
    exit_code = pytest.main([__file__, "-v", "--tb=short"])
    sys.exit(exit_code)
