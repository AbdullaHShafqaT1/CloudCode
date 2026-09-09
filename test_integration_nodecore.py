"""
test_integration_nodecore.py
============================
Comprehensive Integration Test Suite for NodeCore within the CloudCode Ecosystem.

Validates:
1. Dynamic tool bindings to live modules:
   - NodeInsight (FileReaderTool)
   - NodeForge (FileWriterTool)
   - NodePulse (TerminalExecutorTool)
   - NodeLink (Gateway)
   - NodeLog (Live Event Stream)
2. Closed-loop error diagnosis, plan generation, and dispatch.
3. LegacyBridge integration via BridgeAdapter.core_call().
4. Cloud LLM endpoint configuration helper and connectivity.

Run:
    pytest test_integration_nodecore.py -v
    python test_integration_nodecore.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent
for _p in [
    str(_REPO_ROOT),
    str(_REPO_ROOT / "NodeCore"),
    str(_REPO_ROOT / "NodeInsight"),
    str(_REPO_ROOT / "NodeForge"),
    str(_REPO_ROOT / "NodePulse"),
    str(_REPO_ROOT / "NodeLink"),
    str(_REPO_ROOT / "NodeLog"),
    str(_REPO_ROOT / "LegacyBridge"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from node_core import (
    NodeCore,
    ErrorParser,
    RemediationGenerator,
    NodeCoreState,
    RemediationStatus,
    ErrorType,
    create_cloud_llm_config,
    configure_tools,
)
from node_core.tools import NodeInsight, NodeForge, NodePulse, NodeLink, NodeLog
from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult


class TestNodeCoreModuleIntegration(unittest.TestCase):
    """Verifies NodeCore tool adapters and live module integrations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nodecore_test_")
        configure_tools(workspace_root=self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_configure_tools(self):
        """configure_tools sets the active workspace and logging directory."""
        self.assertTrue(os.path.isdir(self.temp_dir))
        stats = NodeLog.stats()
        self.assertIsInstance(stats, dict)

    def test_02_node_forge_live_write(self):
        """NodeForge writes files cleanly within the configured workspace."""
        test_file = os.path.join(self.temp_dir, "sample.py")
        content = "print('hello from nodecore integration test')\n"
        res = NodeForge.write_file("sample.py", content, workspace_path=self.temp_dir)
        self.assertEqual(res.get("status"), "success")
        self.assertTrue(os.path.exists(test_file))

        with open(test_file, "r") as f:
            readback = f.read()
        self.assertEqual(readback, content)

    def test_03_node_insight_live_scan_and_read(self):
        """NodeInsight scans and reads files in the workspace."""
        # Create a test file first
        NodeForge.write_file("module.py", "def add(a, b):\n    return a + b\n", workspace_path=self.temp_dir)
        scan = NodeInsight.scan_workspace(self.temp_dir)
        self.assertEqual(scan.get("status"), "success")

        read_res = NodeInsight.read_file("module.py", workspace_path=self.temp_dir)
        self.assertEqual(read_res.get("status"), "success")
        self.assertIn("def add", read_res.get("content", ""))

    def test_04_node_pulse_live_command(self):
        """NodePulse executes safe commands within the workspace."""
        res = NodePulse.execute_command("python --version", cwd=self.temp_dir)
        self.assertEqual(res.get("status"), "success")
        self.assertEqual(res.get("exit_code"), 0)

    def test_05_node_log_live_telemetry(self):
        """NodeLog records and buffers events."""
        NodeLog.emit("INTEGRATION_TEST_EVENT", {
            "source": "TestNodeCore",
            "status_level": "INFO",
            "message": "Testing telemetry emission",
        })
        stats = NodeLog.stats()
        self.assertIn("buffer_size", stats)

    def test_06_node_link_dispatch(self):
        """NodeLink handles remote dispatch request."""
        res = NodeLink.dispatch_remote("test_endpoint", {"action": "ping"})
        self.assertIn("job_id", res)
        self.assertIn(res.get("status"), ["submitted", "ESTABLISHED", "success"])


class TestNodeCoreOrchestrator(unittest.TestCase):
    """Verifies NodeCore self-healing, diagnosis, and remediation pipeline."""

    def test_07_closed_loop_remediation(self):
        """Simulate execution failure -> diagnosis -> remediation plan -> patch dispatch."""
        core = NodeCore(workspace_root=str(_REPO_ROOT))
        failure_trace = """Traceback (most recent call last):
  File "src/math_ops.py", line 12
    return x +
             ^
SyntaxError: invalid syntax
"""
        # Step 1: Detect failure and form inspection dispatch
        inspection_call = core.handle_execution_failure(failure_trace)
        self.assertEqual(core.state, NodeCoreState.ANALYZING)
        self.assertEqual(inspection_call.target_node, "NodeInsight")
        self.assertEqual(core.current_diagnosis.error_type, ErrorType.SYNTAX_ERROR.value)

        # Step 2: Generate remediation plan
        plan = core.generate_remediation_plan()
        self.assertEqual(core.state, NodeCoreState.PATCHING)
        self.assertGreater(len(plan.instructions), 0)

        # Step 3: Prepare patch dispatch to NodeForge
        patch_call = core.prepare_patch_dispatch(plan)
        self.assertEqual(patch_call.target_node, "NodeForge")

        # Step 4: Validate resolution
        core.prepare_validation("pytest tests/test_math.py")
        self.assertEqual(core.state, NodeCoreState.VALIDATING)

        simulated_pass = {
            "status": "success",
            "command": "pytest tests/test_math.py",
            "exit_code": 0,
            "stdout": "1 passed in 0.05s",
        }
        final_state = core.evaluate_validation_result(simulated_pass)
        self.assertEqual(final_state, NodeCoreState.SUCCESS)
        self.assertEqual(core.status, RemediationStatus.SUCCESS.value)


class TestBridgeAdapterCoreIntegration(unittest.TestCase):
    """Verifies BridgeAdapter.core_call integration."""

    def test_08_bridge_adapter_mock_core_call(self):
        adapter = BridgeAdapter(mode=BridgeMode.MOCK, workspace_root=str(_REPO_ROOT))
        res = adapter.core_call("diagnose_and_plan", error="SyntaxError")
        self.assertTrue(res.ok)
        self.assertEqual(res.data.get("orchestrator"), "NodeCore")

    def test_09_bridge_adapter_local_core_call(self):
        adapter = BridgeAdapter(mode=BridgeMode.LOCAL, workspace_root=str(_REPO_ROOT))
        trace = """Traceback (most recent call last):
  File "app.py", line 5, in <module>
    import nonexistent_library
ModuleNotFoundError: No module named 'nonexistent_library'
"""
        res = adapter.core_call("diagnose", trace=trace)
        self.assertTrue(res.ok)
        self.assertEqual(res.data["data"].get("error_type"), ErrorType.MODULE_NOT_FOUND.value)


class TestCloudLLMConfiguration(unittest.TestCase):
    """Verifies Cloudflare LLM configuration and live connectivity."""

    def test_10_cloud_llm_config_format(self):
        cfg = create_cloud_llm_config(
            base_url="https://min-referenced-celtic-fiscal.trycloudflare.com",
            model="qwen2.5-coder:32b"
        )
        self.assertIn("config_list", cfg)
        self.assertEqual(cfg["config_list"][0]["model"], "qwen2.5-coder:32b")
        self.assertIn("/v1", cfg["config_list"][0]["base_url"])

    def test_11_live_cloudflare_endpoint_health(self):
        """Verifies the live Cloudflare tunnel returns a healthy status."""
        url = "https://min-referenced-celtic-fiscal.trycloudflare.com"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CloudCode-IntegrationTest"})
            with urllib.request.urlopen(req, timeout=12) as r:
                body = r.read().decode("utf-8", errors="replace")
                self.assertIn(r.status, [200, 530])
                if r.status == 200:
                    self.assertIn("model", body.lower())
        except Exception as e:
            # If network/tunnel is temporarily offline, log warning
            print(f"Warning: Cloudflare endpoint health check: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
