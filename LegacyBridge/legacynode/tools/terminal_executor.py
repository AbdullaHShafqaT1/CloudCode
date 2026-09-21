"""
LegacyNode — Terminal Executor Tool
Safe async subprocess executor with timeout, output capture,
blocklist enforcement, and live streaming to StateManager.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

DEFAULT_BLOCKLIST = [
    "rm -rf /",
    "del /f /s /q c:\\",
    "format c:",
    ":(){:|:&};:",   # Fork bomb
    "shutdown",
    "reboot",
    "mkfs",
    "dd if=/dev/zero",
    "chmod -R 777 /",
    "chown -R",
]

MAX_OUTPUT_BYTES = 512 * 1024  # 512 KB


class CommandBlockedError(RuntimeError):
    """Raised when a command matches the safety blocklist."""


class TerminalExecutor:
    """
    Executes shell commands as async subprocesses in a sandboxed manner.

    Safety features:
    - Command blocklist checked before execution
    - Working directory validated to be within workspace root
    - stdout/stderr capped at MAX_OUTPUT_BYTES
    - Per-command timeout with clean process termination
    - Output streamed line-by-line for live dashboard display
    """

    def __init__(
        self,
        workspace_root: str = ".",
        default_timeout: int = 30,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        blocklist: Optional[list[str]] = None,
    ):
        self._root = Path(workspace_root).resolve()
        self._default_timeout = default_timeout
        self._max_output = max_output_bytes
        self._blocklist = [b.lower() for b in (blocklist or DEFAULT_BLOCKLIST)]

        # Line callback for live streaming (set by AgentController)
        self._line_callback: Optional[callable] = None
        self.should_stop = None

    def set_line_callback(self, callback: callable) -> None:
        """Register a callback(line: str) called for each output line."""
        self._line_callback = callback

    # ─── Safety Check ────────────────────────────────────────────────────────

    def _check_blocklist(self, command: str) -> None:
        cmd_lower = command.lower()
        for blocked in self._blocklist:
            if blocked in cmd_lower:
                raise CommandBlockedError(
                    f"Command blocked by safety policy. "
                    f"Matched pattern: '{blocked}'\n"
                    f"Command: {command[:200]}"
                )

    def _safe_cwd(self, cwd: str) -> Path:
        """Resolve and validate that the working directory is within workspace root."""
        if cwd == ".":
            return self._root
        resolved = (self._root / cwd).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError(
                f"Working directory '{cwd}' resolves outside workspace root '{self._root}'"
            )
        return resolved

    # ─── Execution ───────────────────────────────────────────────────────────

    async def execute(
        self,
        command: str,
        cwd: str = ".",
        timeout: int = 0,
    ) -> dict:
        """
        Execute a shell command asynchronously.

        Args:
            command: Shell command string.
            cwd: Working directory (relative to workspace root or absolute).
            timeout: Timeout in seconds. 0 = use default from constructor.

        Returns:
            Dict with keys: stdout, stderr, returncode, timed_out, blocked.

        Raises:
            CommandBlockedError: If the command matches the blocklist.
        """
        effective_timeout = timeout if timeout > 0 else self._default_timeout

        # Safety check
        try:
            self._check_blocklist(command)
        except CommandBlockedError as e:
            log.warning("Command blocked", command=command[:100])
            return {
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "timed_out": False,
                "blocked": True,
                "command": command,
            }

        safe_cwd = self._safe_cwd(cwd)
        if not safe_cwd.exists():
            safe_cwd.mkdir(parents=True, exist_ok=True)

        log.info("Executing command", command=command[:120], cwd=str(safe_cwd))

        from NodePulse.terminal_executor import TerminalExecutorTool
        def output(chunk):
            if self._line_callback:
                for line in chunk.splitlines():
                    try:
                        self._line_callback(line)
                    except Exception:
                        pass
        result = await TerminalExecutorTool(str(self._root)).run_command(
            command, cwd=str(safe_cwd), timeout=effective_timeout,
            should_stop=self.should_stop, on_output=output,
            max_output_chars=self._max_output,
        )
        return {"stdout": result["stdout"],
                "stderr": result["stderr"] + ("\n" + result["error_message"] if result.get("error_message") else ""),
                "returncode": result["exit_code"], "timed_out": result["status"] == "timeout",
                "cancelled": result["status"] == "cancelled", "status": result["status"],
                "blocked": False, "command": command, "truncated": result["truncated"]}
