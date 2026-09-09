"""
test_bridge_terminal_integration.py
====================================
End-to-end integration tests for the full NodePulse pipeline:

    LegacyBridge (BridgeAdapter.terminal_call)
        ↓
    NodePulseBridge
        ↓
    NodePulse (TerminalExecutorTool)
        ↓
    BridgeResult → caller

Four test scenarios
-------------------
1. HAPPY PATH — basic command execution, stdout capture, schema validation,
   exit codes, latency tracking.

2. TIMEOUT ENFORCEMENT — commands that run past their deadline are killed cleanly
   and NodePulse / LegacyBridge surface the correct error payload.

3. SECURITY & BLOCKLIST — forbidden-pattern commands are rejected before the
   shell is ever invoked; the error propagates correctly through the bridge chain.

4. ERROR HANDLING & STATE RECOVERY — invalid CWD, unknown actions, and
   post-error adapter reuse.

Run commands
------------
    pytest test_bridge_terminal_integration.py -v --tb=short
    pytest test_bridge_terminal_integration.py -v -s --tb=long
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TerminalIntegrationTest")

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_PROJECT_ROOT    = Path(__file__).parent.resolve()
_LEGACYBRIDGE    = _PROJECT_ROOT / "LegacyBridge"
_LEGACYNODE_PKG  = _LEGACYBRIDGE / "legacynode"
_NODEPULSE       = _PROJECT_ROOT / "NodePulse"

for _p in [
    str(_LEGACYBRIDGE),
    str(_LEGACYNODE_PKG),
    str(_NODEPULSE),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Component imports
# ---------------------------------------------------------------------------
try:
    from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult
    log.info("LegacyBridge imported OK")
except ImportError as exc:
    pytest.exit(f"Cannot import LegacyBridge: {exc}", returncode=1)

try:
    from terminal_executor import TerminalExecutorTool  # type: ignore[import]
    log.info("NodePulse (TerminalExecutorTool) imported OK")
except ImportError as exc:
    pytest.exit(f"Cannot import NodePulse: {exc}", returncode=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro) -> Any:
    """Run a coroutine synchronously from a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _assert_bridge_ok(result: BridgeResult, label: str) -> None:
    assert isinstance(result, BridgeResult), \
        f"[{label}] Expected BridgeResult, got {type(result)}"
    if not result.ok:
        log.error(f"[{label}] FAILED — error={result.error}\n{result.traceback or ''}")
    assert result.ok, f"[{label}] BridgeResult.ok=False: {result.error}"
    log.info(f"[{label}] OK  latency={result.latency_ms:.1f} ms")


def _direct_pulse(sandbox: Path, command: str, **kwargs) -> dict:
    """Call NodePulse directly (bypass bridge) for comparison / setup."""
    tool = TerminalExecutorTool(allowed_project_root=str(sandbox))
    return _run_async(tool.run_command(command=command, cwd=str(sandbox), **kwargs))


def _make_adapter(sandbox: Path) -> BridgeAdapter:
    return BridgeAdapter(
        mode=BridgeMode.LOCAL,
        workspace_root=str(sandbox),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """Clean temp workspace.  NodePulse uses it as its allowed_project_root."""
    log.info(f"Sandbox: {tmp_path}")
    return tmp_path


@pytest.fixture()
def adapter(sandbox: Path) -> BridgeAdapter:
    adp = _make_adapter(sandbox)
    log.info(f"BridgeAdapter  mode={adp.mode.value}  workspace={adp.workspace_root}")
    return adp


# ===========================================================================
# SCENARIO 1 — Happy Path
# ===========================================================================

class TestHappyPath:
    """Basic command execution through the full bridge chain."""

    def test_simple_echo_command(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """Echo a unique string — must appear verbatim in stdout."""
        log.info("=== HAPPY PATH: simple echo ===")
        result = adapter.terminal_call(
            command='python -c "print(\'BRIDGE_SENTINEL_OK\')"',
            allowed_project_root=str(sandbox),
        )
        _assert_bridge_ok(result, "Echo/terminal_call")

        data = result.data
        assert "BRIDGE_SENTINEL_OK" in data["stdout"], \
            f"Sentinel not found in stdout: {data['stdout']!r}"
        assert data["exit_code"] == 0
        log.info(f"  stdout={data['stdout'].strip()!r}")

    def test_python_version_command(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """Run `python --version` — must succeed and contain 'Python'."""
        log.info("=== HAPPY PATH: python --version ===")
        result = adapter.terminal_call(
            command="python --version",
            allowed_project_root=str(sandbox),
        )
        _assert_bridge_ok(result, "PythonVersion/terminal_call")
        combined = result.data["stdout"] + result.data["stderr"]
        assert "Python" in combined, \
            f"'Python' not found in output: {combined!r}"
        log.info(f"  output={combined.strip()!r}")

    def test_exit_code_zero_maps_to_ok_true(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """Exit-code 0 must produce BridgeResult.ok=True."""
        log.info("=== HAPPY PATH: exit_code 0 → ok=True ===")
        result = adapter.terminal_call(
            command='python -c "import sys; sys.exit(0)"',
            allowed_project_root=str(sandbox),
        )
        assert result.ok is True
        assert result.data["exit_code"] == 0
        log.info("  PASS — exit_code=0 → ok=True")

    def test_exit_code_nonzero_maps_to_ok_false(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """Non-zero exit code must produce BridgeResult.ok=False."""
        log.info("=== HAPPY PATH: exit_code ≠ 0 → ok=False ===")
        result = adapter.terminal_call(
            command='python -c "import sys; sys.exit(7)"',
            allowed_project_root=str(sandbox),
        )
        assert result.ok is False
        assert result.data["exit_code"] == 7
        log.info(f"  PASS — exit_code=7 → ok=False  error={result.error!r}")

    def test_stderr_captured_on_failure(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """stderr written by a failing command must appear in result.data['stderr']."""
        log.info("=== HAPPY PATH: stderr capture ===")
        result = adapter.terminal_call(
            command='python -c "import sys; sys.stderr.write(\'STDERR_SENTINEL\\n\'); sys.exit(1)"',
            allowed_project_root=str(sandbox),
        )
        assert "STDERR_SENTINEL" in result.data["stderr"], \
            f"Stderr sentinel missing: {result.data['stderr']!r}"
        log.info("  PASS — stderr captured correctly")

    def test_response_schema_completeness(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """Full NodePulse schema must be present inside BridgeResult.data."""
        log.info("=== HAPPY PATH: schema completeness ===")
        result = adapter.terminal_call(
            command='python -c "print(1)"',
            allowed_project_root=str(sandbox),
        )
        _assert_bridge_ok(result, "Schema/terminal_call")

        required = {
            "status", "action", "command", "cwd",
            "exit_code", "stdout", "stderr",
            "duration_ms", "timestamp", "error_message", "truncated",
        }
        missing = required - set(result.data.keys())
        assert not missing, f"BridgeResult.data missing keys: {missing}"
        log.info(f"  PASS — all {len(required)} schema keys present")

    def test_latency_ms_nonnegative(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """BridgeResult.latency_ms must be ≥ 0 for every terminal_call."""
        log.info("=== HAPPY PATH: latency_ms ===")
        result = adapter.terminal_call(
            command='python -c "print(\'ok\')"',
            allowed_project_root=str(sandbox),
        )
        assert result.latency_ms >= 0, \
            f"latency_ms must be non-negative, got {result.latency_ms}"
        log.info(f"  PASS — latency_ms={result.latency_ms:.1f}")

    def test_duration_ms_nonnegative(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """NodePulse duration_ms must be a non-negative integer."""
        log.info("=== HAPPY PATH: duration_ms ===")
        result = adapter.terminal_call(
            command='python -c "print(\'ok\')"',
            allowed_project_root=str(sandbox),
        )
        _assert_bridge_ok(result, "DurationMs/terminal_call")
        dur = result.data["duration_ms"]
        assert isinstance(dur, int) and dur >= 0, \
            f"duration_ms must be non-negative int, got {dur!r}"
        log.info(f"  PASS — duration_ms={dur}")

    def test_bridge_logs_dispatch_event(self, adapter: BridgeAdapter, sandbox: Path) -> None:
        """BridgeAdapter must log a TERMINAL dispatch entry after terminal_call."""
        log.info("=== HAPPY PATH: bridge logs dispatch ===")
        adapter.terminal_call(
            command='python -c "print(\'logged\')"',
            allowed_project_root=str(sandbox),
        )
        logs = adapter.get_live_output()
        assert any("TERMINAL" in line or "nodepulse" in line.lower() for line in logs), \
            f"No TERMINAL log entry found. Logs: {logs}"
        log.info("  PASS — TERMINAL dispatch event logged")

    def test_mock_mode_returns_simulated_response(self, sandbox: Path) -> None:
        """In MOCK mode, terminal_call must return a safe simulated response."""
        log.info("=== HAPPY PATH: mock mode ===")
        mock_adapter = BridgeAdapter(
            mode=BridgeMode.MOCK,
            workspace_root=str(sandbox),
        )
        result = mock_adapter.terminal_call(
            command='python -c "print(1)"',
            allowed_project_root=str(sandbox),
        )
        assert result.ok is True
        assert "[MOCK]" in result.data["stdout"]
        log.info(f"  PASS — mock stdout={result.data['stdout']!r}")


# ===========================================================================
# SCENARIO 2 — Timeout Enforcement
# ===========================================================================

class TestTimeoutEnforcement:
    """Commands that exceed their deadline must be killed cleanly."""

    def test_timeout_status_propagated_as_ok_false(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """A timed-out command must produce BridgeResult.ok=False."""
        log.info("=== TIMEOUT: ok=False on timeout ===")
        result = adapter.terminal_call(
            command='python -c "import time; time.sleep(10)"',
            timeout=1,
            allowed_project_root=str(sandbox),
        )
        assert result.ok is False, \
            f"Expected ok=False for timed-out command, got ok={result.ok}"
        log.info(f"  PASS — ok=False  error={result.error!r}")

    def test_timeout_data_status_is_timeout(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """NodePulse data.status must be 'timeout' when deadline is exceeded."""
        log.info("=== TIMEOUT: data.status == 'timeout' ===")
        result = adapter.terminal_call(
            command='python -c "import time; time.sleep(10)"',
            timeout=1,
            allowed_project_root=str(sandbox),
        )
        assert result.data.get("status") == "timeout", \
            f"Expected status='timeout', got {result.data.get('status')!r}"
        log.info("  PASS — status='timeout'")

    def test_timeout_error_message_present(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """error_message must mention 'timeout' or 'exceeded'."""
        log.info("=== TIMEOUT: error_message descriptive ===")
        result = adapter.terminal_call(
            command='python -c "import time; time.sleep(10)"',
            timeout=1,
            allowed_project_root=str(sandbox),
        )
        err = (result.data.get("error_message") or "").lower()
        assert "timeout" in err or "exceeded" in err, \
            f"Expected 'timeout'/'exceeded' in error_message, got: {err!r}"
        log.info(f"  PASS — error_message={err!r}")

    def test_timeout_adapter_remains_usable(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """After a timeout, the BridgeAdapter must still work for the next call."""
        log.info("=== TIMEOUT: adapter recovers after timeout ===")
        # First call — times out
        result1 = adapter.terminal_call(
            command='python -c "import time; time.sleep(10)"',
            timeout=1,
            allowed_project_root=str(sandbox),
        )
        assert result1.ok is False

        # Second call — must succeed normally
        result2 = adapter.terminal_call(
            command='python -c "print(\'RECOVERY_OK\')"',
            allowed_project_root=str(sandbox),
        )
        _assert_bridge_ok(result2, "Timeout/Recovery")
        assert "RECOVERY_OK" in result2.data["stdout"]
        log.info("  PASS — adapter recovered after timeout")


# ===========================================================================
# SCENARIO 3 — Security & Blocklist
# ===========================================================================

class TestSecurityAndBlocklist:
    """Forbidden-pattern commands must be rejected before shell invocation."""

    def _assert_blocked(self, result: BridgeResult, label: str) -> None:
        assert result.ok is False, \
            f"[{label}] Dangerous command was NOT blocked! result.ok=True"
        err = (result.error or result.data.get("error_message") or "").lower()
        assert any(word in err for word in ("forbidden", "safety", "guardrail", "blocked")), \
            f"[{label}] Expected safety error, got: {err!r}"
        assert result.data.get("exit_code") == -1, \
            f"[{label}] exit_code must be -1 for blocked command"
        log.info(f"  [{label}] PASS — blocked correctly: {err!r}")

    def test_rm_rf_slash_is_blocked(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        log.info("=== SECURITY: rm -rf / blocked ===")
        result = adapter.terminal_call(
            command="rm -rf /",
            allowed_project_root=str(sandbox),
        )
        self._assert_blocked(result, "rm-rf-slash")

    def test_mkfs_is_blocked(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        log.info("=== SECURITY: mkfs blocked ===")
        result = adapter.terminal_call(
            command="mkfs.ext4 /dev/sda",
            allowed_project_root=str(sandbox),
        )
        self._assert_blocked(result, "mkfs")

    def test_shutdown_is_blocked(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        log.info("=== SECURITY: shutdown blocked ===")
        result = adapter.terminal_call(
            command="shutdown -h now",
            allowed_project_root=str(sandbox),
        )
        self._assert_blocked(result, "shutdown")

    def test_reboot_is_blocked(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        log.info("=== SECURITY: reboot blocked ===")
        result = adapter.terminal_call(
            command="reboot",
            allowed_project_root=str(sandbox),
        )
        self._assert_blocked(result, "reboot")

    def test_blocked_stdout_is_empty(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """Blocked commands must never produce stdout output."""
        log.info("=== SECURITY: blocked stdout is empty ===")
        result = adapter.terminal_call(
            command="shutdown now",
            allowed_project_root=str(sandbox),
        )
        assert result.data.get("stdout") == "", \
            f"stdout must be empty for a blocked command, got: {result.data.get('stdout')!r}"
        log.info("  PASS — stdout empty for blocked command")

    def test_adapter_remains_usable_after_blocked_command(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """After a blocked command, BridgeAdapter must still work correctly."""
        log.info("=== SECURITY: adapter recovers after blocked command ===")
        # Block
        adapter.terminal_call(command="mkfs /dev/sda", allowed_project_root=str(sandbox))
        # Recover
        result = adapter.terminal_call(
            command='python -c "print(\'AFTER_BLOCK_OK\')"',
            allowed_project_root=str(sandbox),
        )
        _assert_bridge_ok(result, "Security/Recovery")
        assert "AFTER_BLOCK_OK" in result.data["stdout"]
        log.info("  PASS — adapter recovered after blocked command")


# ===========================================================================
# SCENARIO 4 — Error Handling & State Recovery
# ===========================================================================

class TestErrorHandlingAndRecovery:
    """Invalid inputs must produce clean error dicts, not exceptions."""

    def test_unknown_action_returns_error(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """Dispatching an unknown NodePulse action must return ok=False cleanly."""
        log.info("=== ERROR: unknown action ===")
        from legacynode.tools.node_pulse_bridge import NodePulseBridge
        bridge = NodePulseBridge(allowed_project_root=str(sandbox))
        resp = _run_async(bridge.call("teleport_command", command="echo hi"))
        assert resp.get("status") == "error"
        assert "Unsupported action" in (resp.get("error_message") or ""), \
            f"Expected 'Unsupported action' in error_message: {resp.get('error_message')!r}"
        log.info(f"  PASS — error_message={resp.get('error_message')!r}")

    def test_bridge_adapter_logs_error_on_failure(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """Failed terminal_call must produce a log entry containing 'error'."""
        log.info("=== ERROR: bridge logs error on failure ===")
        adapter.terminal_call(
            command='python -c "import sys; sys.exit(99)"',
            allowed_project_root=str(sandbox),
        )
        logs = adapter.get_live_output()
        # Either TERMINAL OK with exit_code=99, or an ERROR entry
        assert any(
            "TERMINAL" in line or "ERROR" in line for line in logs
        ), f"No TERMINAL/ERROR log entry found. Logs: {logs}"
        log.info("  PASS — adapter logged the terminal event")

    def test_multiple_sequential_calls_all_succeed(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """Multiple sequential terminal_calls must all succeed independently."""
        log.info("=== RECOVERY: multiple sequential calls ===")
        commands = [
            ('python -c "print(\'A\')"', "A"),
            ('python -c "print(\'B\')"', "B"),
            ('python -c "print(\'C\')"', "C"),
        ]
        for cmd, sentinel in commands:
            result = adapter.terminal_call(
                command=cmd,
                allowed_project_root=str(sandbox),
            )
            _assert_bridge_ok(result, f"Sequential/{sentinel}")
            assert sentinel in result.data["stdout"], \
                f"Sentinel {sentinel!r} not found in stdout"
        log.info("  PASS — 3 sequential calls all returned correct output")

    def test_node_pulse_bridge_is_available(self) -> None:
        """NodePulseBridge.is_available() must return True with NodePulse on sys.path."""
        log.info("=== AVAILABILITY: NodePulseBridge.is_available() ===")
        from legacynode.tools.node_pulse_bridge import NodePulseBridge
        assert NodePulseBridge.is_available() is True, \
            "NodePulseBridge.is_available() returned False — NodePulse may not be on sys.path"
        log.info("  PASS — NodePulse is available")

    def test_latency_recorded_across_multiple_calls(
        self, adapter: BridgeAdapter, sandbox: Path
    ) -> None:
        """Every BridgeResult from terminal_call must carry non-negative latency_ms."""
        log.info("=== RECOVERY: latency_ms on multiple calls ===")
        commands = [
            'python -c "print(\'x\')"',
            'python -c "import sys; sys.exit(1)"',
        ]
        for cmd in commands:
            result = adapter.terminal_call(
                command=cmd,
                allowed_project_root=str(sandbox),
            )
            assert result.latency_ms >= 0, \
                f"latency_ms < 0 for command={cmd!r}: {result.latency_ms}"
            cmd_short = repr(cmd)[:40]
            log.info(f"  {cmd_short}: latency={result.latency_ms:.1f} ms")
        log.info("  PASS — latency_ms non-negative for all calls")
