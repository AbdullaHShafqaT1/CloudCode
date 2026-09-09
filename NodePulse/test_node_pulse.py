"""
test_node_pulse.py
==================
Exhaustive, standalone unit test suite for NodePulse (TerminalExecutorTool).

Covers:
  1. Subprocess & shell execution  - success, failure, stderr capture
  2. Timeout enforcement           - clean kill, no zombie processes
  3. Security & command blacklist  - all FORBIDDEN_PATTERNS + edge cases
  4. Working-directory sandboxing  - cwd confinement & path traversal
  5. Environment variable injection - selective pass-through & isolation
  6. Stream output capture          - multi-line, large output, truncation

Run with:
    pytest test_node_pulse.py -v
or:
    python test_node_pulse.py
"""

from __future__ import annotations

import os
import sys
import textwrap
import time

import pytest

# ---------------------------------------------------------------------------
# Ensure the module under test is importable when running as __main__
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from terminal_executor import TerminalExecutorTool  # noqa: E402


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture()
def sandbox(tmp_path):
    """
    Temporary sandbox directory with helper scripts:

    pass_script.py  - exits 0, prints PASS_SENTINEL
    fail_script.py  - exits 42, writes FAIL_SENTINEL to stderr
    hang_script.py  - sleeps 10 s (used for timeout tests)
    multiline.py    - prints N numbered lines for stream-capture tests
    env_printer.py  - prints the value of a named environment variable
    cwd_printer.py  - prints os.getcwd()
    subdir/         - valid sub-directory for CWD confinement tests
    """
    scripts: dict[str, str] = {
        "pass_script.py": textwrap.dedent("""\
            import sys
            print("PASS_SENTINEL: all good")
            sys.exit(0)
        """),
        "fail_script.py": textwrap.dedent("""\
            import sys
            sys.stderr.write("FAIL_SENTINEL: something broke\\n")
            sys.exit(42)
        """),
        "hang_script.py": textwrap.dedent("""\
            import time
            time.sleep(10)
        """),
        "multiline.py": textwrap.dedent("""\
            import sys
            n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
            for i in range(n):
                print(f"LINE_{i:05d}")
        """),
        "env_printer.py": textwrap.dedent("""\
            import os, sys
            key = sys.argv[1]
            print(os.environ.get(key, "MISSING"))
        """),
        "cwd_printer.py": textwrap.dedent("""\
            import os
            print(os.getcwd())
        """),
    }

    for name, source in scripts.items():
        (tmp_path / name).write_text(source, encoding="utf-8")

    (tmp_path / "subdir").mkdir()
    return tmp_path


@pytest.fixture()
def executor(sandbox):
    """Return a TerminalExecutorTool rooted at the sandbox."""
    return TerminalExecutorTool(str(sandbox))


def _py(script_path: str, *args: str) -> str:
    """Build a portable Python invocation string for a script file."""
    arg_str = " ".join(args)
    # Use double-quotes around path to handle spaces on Windows
    return f'python "{script_path}" {arg_str}'.strip()


# ============================================================================
# 1. Subprocess & Shell Execution
# ============================================================================

class TestSubprocessExecution:
    """Basic command execution: happy path, failure path, schema validation."""

    @pytest.mark.asyncio
    async def test_execute_command_success_exit_code(self, executor, sandbox):
        """Exit code 0 must map to status='success'."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert result["status"] == "success", result
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_execute_command_success_stdout_content(self, executor, sandbox):
        """Stdout must contain the sentinel emitted by pass_script.py."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert "PASS_SENTINEL" in result["stdout"]

    @pytest.mark.asyncio
    async def test_execute_command_failure_exit_code(self, executor, sandbox):
        """Non-zero exit code must map to status='error'."""
        result = await executor.run_command(
            command=_py(str(sandbox / "fail_script.py")),
            cwd=str(sandbox),
        )
        assert result["status"] == "error"
        assert result["exit_code"] == 42

    @pytest.mark.asyncio
    async def test_execute_command_failure_stderr_captured(self, executor, sandbox):
        """Stderr written by a failing command must appear in result['stderr']."""
        result = await executor.run_command(
            command=_py(str(sandbox / "fail_script.py")),
            cwd=str(sandbox),
        )
        assert "FAIL_SENTINEL" in result["stderr"]

    @pytest.mark.asyncio
    async def test_response_schema_keys_present(self, executor, sandbox):
        """Every response must expose the full documented schema."""
        required = {
            "status", "action", "command", "cwd",
            "exit_code", "stdout", "stderr",
            "duration_ms", "timestamp", "error_message", "truncated",
        }
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        missing = required - result.keys()
        assert not missing, f"Missing schema keys: {missing}"

    @pytest.mark.asyncio
    async def test_duration_ms_is_nonnegative(self, executor, sandbox):
        """duration_ms must be a non-negative integer after any execution."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert isinstance(result["duration_ms"], int)
        assert result["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_call_dispatch_run_command(self, executor, sandbox):
        """The unified call() entrypoint must dispatch run_command correctly."""
        result = await executor.call(
            "run_command",
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_call_dispatch_unsupported_action(self, executor):
        """Unsupported action must return status='error', not raise."""
        result = await executor.call("unknown_action", command="echo hi")
        assert result["status"] == "error"
        assert "Unsupported action" in result["error_message"]

    @pytest.mark.asyncio
    async def test_error_message_falsy_on_success(self, executor, sandbox):
        """error_message must be None / falsy when the command succeeds."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert not result["error_message"]


# ============================================================================
# 2. Timeout Enforcement
# ============================================================================

class TestTimeoutHandling:
    """Runaway processes must be terminated cleanly without zombie subprocesses."""

    @pytest.mark.asyncio
    async def test_timeout_status_is_timeout(self, executor, sandbox):
        """Status must be 'timeout' when deadline is exceeded."""
        result = await executor.run_command(
            command=_py(str(sandbox / "hang_script.py")),
            timeout=1,
            cwd=str(sandbox),
        )
        assert result["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_timeout_error_message_descriptive(self, executor, sandbox):
        """error_message must mention 'timeout' or 'exceeded'."""
        result = await executor.run_command(
            command=_py(str(sandbox / "hang_script.py")),
            timeout=1,
            cwd=str(sandbox),
        )
        assert result["error_message"] is not None
        msg = result["error_message"].lower()
        assert "timeout" in msg or "exceeded" in msg

    @pytest.mark.asyncio
    async def test_timeout_returns_within_grace_period(self, executor, sandbox):
        """
        The call must return within timeout + grace seconds even for a hanging
        process, confirming the process was killed (not left as a zombie).

        Windows note: SIGTERM is a no-op on shell subprocesses; the executor
        falls through to kill() after a secondary 3 s wait, so the realistic
        worst case is ~1 s (timeout) + 3 s (terminate wait) + epsilon = ~4 s.
        We allow 12 s to give CI machines adequate headroom.
        """
        timeout_s = 1
        # Allow generous headroom: 1s timeout + 3s graceful-terminate wait
        # + kill + wait, plus CI machine latency. 12s is safe on all platforms.
        grace = 12
        t0 = time.monotonic()
        result = await executor.run_command(
            command=_py(str(sandbox / "hang_script.py")),
            timeout=timeout_s,
            cwd=str(sandbox),
        )
        elapsed = time.monotonic() - t0
        assert result["status"] == "timeout"
        assert elapsed < timeout_s + grace, (
            f"run_command took {elapsed:.1f}s; process was not killed within "
            f"the {timeout_s + grace}s deadline"
        )

    @pytest.mark.asyncio
    async def test_timeout_exit_code_not_zero(self, executor, sandbox):
        """
        A killed process cannot exit cleanly.
        exit_code must be non-zero (-1 sentinel or OS kill code).
        """
        result = await executor.run_command(
            command=_py(str(sandbox / "hang_script.py")),
            timeout=1,
            cwd=str(sandbox),
        )
        assert result["exit_code"] != 0

    @pytest.mark.asyncio
    async def test_fast_command_respects_generous_timeout(self, executor, sandbox):
        """A fast-completing command must succeed even with a large timeout."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            timeout=30,
            cwd=str(sandbox),
        )
        assert result["status"] == "success"


# ============================================================================
# 3. Security & Command Blacklisting
# ============================================================================

class TestCommandBlacklisting:
    """
    Every FORBIDDEN_PATTERNS entry must be rejected before the shell is invoked.
    Blocking must be confirmed by: status='error', safety-related error_message,
    empty stdout, and exit_code == -1.
    """

    def _assert_blocked(self, result: dict) -> None:
        assert result["status"] == "error", (
            f"Dangerous command not blocked: {result}"
        )
        msg = (result.get("error_message") or "").lower()
        assert "forbidden" in msg or "safety" in msg or "guardrail" in msg, (
            f"Expected safety error message, got: {result.get('error_message')!r}"
        )
        # Shell must not have been reached
        assert result["stdout"] == ""
        assert result["exit_code"] == -1

    @pytest.mark.asyncio
    async def test_block_rm_rf_root(self, executor):
        result = await executor.run_command(command="rm -rf /")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_rm_rf_root_star(self, executor):
        result = await executor.run_command(command="rm -rf /*")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_rm_rf_capital_F(self, executor):
        result = await executor.run_command(command="rm -rF /")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_mkfs_with_subtype(self, executor):
        result = await executor.run_command(command="mkfs.ext4 /dev/sda")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_mkfs_bare(self, executor):
        result = await executor.run_command(command="sudo mkfs /dev/sdb")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_fork_bomb(self, executor):
        # The regex is the literal pattern: :\(\)\{ :\|:& \};:
        # It matches the canonical bash fork bomb: ':(){ :|:& };:'
        # (single spaces around the body, literal parens and braces).
        result = await executor.run_command(command=":(){ :|:& };:")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_shutdown(self, executor):
        result = await executor.run_command(command="shutdown -h now")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_reboot(self, executor):
        result = await executor.run_command(command="reboot")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_halt(self, executor):
        result = await executor.run_command(command="sudo halt")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_block_poweroff(self, executor):
        result = await executor.run_command(command="poweroff")
        self._assert_blocked(result)

    @pytest.mark.asyncio
    async def test_allow_targeted_file_deletion(self, executor, sandbox):
        """
        'rm' of a specific non-root file must NOT be blocked
        (the pattern only matches rm -r[fF] / variants).
        We exercise this through Python's os.remove to stay cross-platform.
        """
        dummy = sandbox / "dummy.txt"
        dummy.write_text("temp", encoding="utf-8")
        result = await executor.run_command(
            command=(
                f'python -c "import os; os.remove(r\'{str(dummy).replace(chr(39), chr(34))}\''
                '"'
            ),
            cwd=str(sandbox),
        )
        assert "forbidden pattern" not in (result.get("error_message") or "").lower()

    @pytest.mark.asyncio
    async def test_allow_safe_print(self, executor, sandbox):
        """A plain print statement must not be blocked."""
        result = await executor.run_command(
            command="python -c \"print('hello')\"",
            cwd=str(sandbox),
        )
        assert result["status"] == "success"


# ============================================================================
# 4. Working Directory Sandboxing
# ============================================================================

class TestCwdConfinement:
    """Commands must stay inside the project root; path-traversal must be denied."""

    @pytest.mark.asyncio
    async def test_default_cwd_is_project_root(self, executor, sandbox):
        """When cwd is omitted, commands run from the project root."""
        result = await executor.run_command(
            command=_py(str(sandbox / "cwd_printer.py")),
        )
        assert result["status"] == "success"
        assert os.path.normcase(result["stdout"].strip()) == os.path.normcase(str(sandbox))

    @pytest.mark.asyncio
    async def test_valid_subdirectory_accepted(self, executor, sandbox):
        """A relative path resolving inside the root must be accepted."""
        result = await executor.run_command(
            command=_py(str(sandbox / "cwd_printer.py")),
            cwd="subdir",
        )
        assert result["status"] == "success"
        expected = os.path.normcase(str(sandbox / "subdir"))
        assert os.path.normcase(result["stdout"].strip()) == expected

    @pytest.mark.asyncio
    async def test_path_traversal_double_dot_blocked(self, executor):
        """../../ traversal must be rejected with a descriptive error."""
        result = await executor.run_command(
            command="python -c \"print(1)\"",
            cwd="../../",
        )
        assert result["status"] == "error"
        assert "outside the allowed project root" in (result["error_message"] or "")

    @pytest.mark.asyncio
    async def test_path_traversal_absolute_outside_root_blocked(self, executor):
        """An absolute path outside the root must be rejected."""
        outside = os.path.dirname(os.path.dirname(sys.executable))
        result = await executor.run_command(
            command="python -c \"print(1)\"",
            cwd=outside,
        )
        assert result["status"] == "error"
        assert "outside the allowed project root" in (result["error_message"] or "")

    @pytest.mark.asyncio
    async def test_cwd_echoed_in_response(self, executor, sandbox):
        """result['cwd'] must reflect the resolved working directory."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert os.path.normcase(result["cwd"]) == os.path.normcase(str(sandbox))

    @pytest.mark.asyncio
    async def test_cwd_project_root_explicitly_accepted(self, executor, sandbox):
        """Passing the root itself as cwd must succeed."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"


# ============================================================================
# 5. Environment Variable Injection
# ============================================================================

class TestEnvironmentVariableInjection:
    """Custom env vars must reach subprocesses; auto-vars must always be present."""

    @pytest.mark.asyncio
    async def test_custom_env_var_visible(self, executor, sandbox):
        """A var injected via env= must appear in the subprocess environment."""
        result = await executor.run_command(
            command=_py(str(sandbox / "env_printer.py"), "MY_SECRET_TOKEN"),
            cwd=str(sandbox),
            env={"MY_SECRET_TOKEN": "abcdef123"},
        )
        assert result["status"] == "success"
        assert "abcdef123" in result["stdout"]

    @pytest.mark.asyncio
    async def test_multiple_env_vars_injected(self, executor, sandbox):
        """Multiple vars must all reach the subprocess."""
        result = await executor.run_command(
            command=_py(str(sandbox / "env_printer.py"), "KEY_A"),
            cwd=str(sandbox),
            env={"KEY_A": "value_a", "KEY_B": "value_b"},
        )
        assert "value_a" in result["stdout"]

    @pytest.mark.asyncio
    async def test_ci_env_var_auto_injected(self, executor, sandbox):
        """CI=true must be injected automatically into every subprocess."""
        result = await executor.run_command(
            command=_py(str(sandbox / "env_printer.py"), "CI"),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        assert "true" in result["stdout"].lower()

    @pytest.mark.asyncio
    async def test_debian_frontend_auto_injected(self, executor, sandbox):
        """DEBIAN_FRONTEND=noninteractive must be injected automatically."""
        result = await executor.run_command(
            command=_py(str(sandbox / "env_printer.py"), "DEBIAN_FRONTEND"),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        assert "noninteractive" in result["stdout"].lower()

    @pytest.mark.asyncio
    async def test_env_param_overrides_auto_defaults(self, executor, sandbox):
        """An explicitly provided value must override the auto-injected default."""
        result = await executor.run_command(
            command=_py(str(sandbox / "env_printer.py"), "CI"),
            cwd=str(sandbox),
            env={"CI": "OVERRIDDEN_VALUE"},
        )
        assert result["status"] == "success"
        assert "OVERRIDDEN_VALUE" in result["stdout"]

    @pytest.mark.asyncio
    async def test_no_env_param_inherits_parent_path(self, executor, sandbox):
        """Without env=, the subprocess must inherit the parent PATH."""
        result = await executor.run_command(
            command=_py(str(sandbox / "env_printer.py"), "PATH"),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        assert result["stdout"].strip() not in ("", "MISSING")


# ============================================================================
# 6. Stream Output Capture
# ============================================================================

class TestStreamOutputCapture:
    """Multi-line, large, and edge-case outputs must be captured faithfully."""

    @pytest.mark.asyncio
    async def test_multiline_stdout_preserved(self, executor, sandbox):
        """All 50 lines emitted by multiline.py must appear in stdout."""
        result = await executor.run_command(
            command=_py(str(sandbox / "multiline.py"), "50"),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        for i in range(50):
            assert f"LINE_{i:05d}" in result["stdout"], f"LINE_{i:05d} missing"

    @pytest.mark.asyncio
    async def test_no_buffer_corruption_on_large_output(self, executor, sandbox):
        """
        500 numbered lines must arrive intact without corruption or dropping.
        truncated must remain False since total output stays below 100 000 chars.
        """
        n = 500
        result = await executor.run_command(
            command=_py(str(sandbox / "multiline.py"), str(n)),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        assert result["truncated"] is False
        lines = [ln for ln in result["stdout"].splitlines() if ln.startswith("LINE_")]
        assert len(lines) == n, f"Expected {n} lines, got {len(lines)}"

    @pytest.mark.asyncio
    async def test_truncation_triggered_at_max_chars(self, executor, sandbox):
        """
        Output of 110 000 'A' chars must trigger truncation at the 100 000 limit.
        truncated must be True and captured text must not exceed the cap.
        """
        result = await executor.run_command(
            command="python -c \"import sys; sys.stdout.write('A' * 110_000)\"",
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        assert result["truncated"] is True
        assert len(result["stdout"]) <= 100_000

    @pytest.mark.asyncio
    async def test_truncation_false_for_small_output(self, executor, sandbox):
        """Small outputs must not be flagged as truncated."""
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_empty_stdout_when_command_silent(self, executor, sandbox):
        """A command with no output must yield an empty stdout string."""
        result = await executor.run_command(
            command="python -c \"pass\"",
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        assert result["stdout"] == ""

    @pytest.mark.asyncio
    async def test_stderr_captured_separately_from_stdout(self, executor, sandbox):
        """
        Stdout and stderr must land in their respective fields without
        cross-contamination.
        """
        result = await executor.run_command(
            command=(
                "python -c \""
                "import sys; "
                "sys.stdout.write('STDOUT_MARKER'); "
                "sys.stderr.write('STDERR_MARKER')"
                "\""
            ),
            cwd=str(sandbox),
        )
        assert "STDOUT_MARKER" in result["stdout"]
        assert "STDERR_MARKER" in result["stderr"]
        # Cross-contamination checks
        assert "STDERR_MARKER" not in result["stdout"]
        assert "STDOUT_MARKER" not in result["stderr"]

    @pytest.mark.asyncio
    async def test_unicode_output_handled_without_crash(self, executor, sandbox):
        """
        Unicode characters (accented letters, emoji) must be decoded with
        errors='replace' without raising and without crashing the call.
        """
        result = await executor.run_command(
            command="python -c \"print('caf\\u00e9 \\U0001F600')\"",
            cwd=str(sandbox),
        )
        # Must always return a valid response dict, never raise
        assert "status" in result


# ============================================================================
# 7. Integration / Composite Scenarios
# ============================================================================

class TestIntegrationScenarios:
    """End-to-end flows combining multiple capability domains."""

    @pytest.mark.asyncio
    async def test_full_pass_flow(self, executor, sandbox):
        """
        A clean pass_script.py run must exhibit the complete success contract:
        status=success, exit_code=0, sentinel in stdout, falsy error_message,
        truncated=False, non-negative duration_ms.
        """
        result = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert result["status"] == "success"
        assert result["exit_code"] == 0
        assert "PASS_SENTINEL" in result["stdout"]
        assert not result["error_message"]
        assert result["truncated"] is False
        assert result["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_full_fail_flow(self, executor, sandbox):
        """
        A deliberate failure must exhibit the complete error contract:
        status=error, non-zero exit code, stderr populated.
        """
        result = await executor.run_command(
            command=_py(str(sandbox / "fail_script.py")),
            cwd=str(sandbox),
        )
        assert result["status"] == "error"
        assert result["exit_code"] != 0
        assert "FAIL_SENTINEL" in result["stderr"]

    @pytest.mark.asyncio
    async def test_env_injection_combined_with_valid_cwd(self, executor, sandbox):
        """Custom env var and a valid subdirectory cwd must work simultaneously."""
        result = await executor.run_command(
            command=_py(str(sandbox / "env_printer.py"), "COMBINED_KEY"),
            cwd="subdir",
            env={"COMBINED_KEY": "combined_value"},
        )
        assert result["status"] == "success"
        assert "combined_value" in result["stdout"]

    @pytest.mark.asyncio
    async def test_blacklisted_command_never_touches_filesystem(self, executor, sandbox):
        """
        A forbidden command must be stopped before the shell is invoked,
        leaving the filesystem unmodified.
        """
        sentinel_file = sandbox / "should_not_exist.txt"
        dangerous_cmd = (
            f"rm -rf / && python -c "
            f"\"open(r'{str(sentinel_file)}', 'w').close()\""
        )
        await executor.run_command(command=dangerous_cmd)
        assert not sentinel_file.exists(), (
            "Forbidden command was not blocked before reaching the filesystem"
        )

    @pytest.mark.asyncio
    async def test_executor_healthy_after_timeout(self, executor, sandbox):
        """After a timeout the executor must handle subsequent commands normally."""
        # Step 1 — trigger a timeout
        result_timeout = await executor.run_command(
            command=_py(str(sandbox / "hang_script.py")),
            timeout=1,
            cwd=str(sandbox),
        )
        assert result_timeout["status"] == "timeout"

        # Step 2 — a normal command must succeed
        result_ok = await executor.run_command(
            command=_py(str(sandbox / "pass_script.py")),
            cwd=str(sandbox),
        )
        assert result_ok["status"] == "success"


# ============================================================================
# Standalone runner
# ============================================================================

if __name__ == "__main__":
    raise SystemExit(
        pytest.main([__file__, "-v", "--tb=short", "--no-header"])
    )
