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
        if not str(resolved).startswith(str(self._root)):
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

        # Use shell on Windows, parse args on POSIX for safety
        use_shell = sys.platform == "win32"

        try:
            if use_shell:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(safe_cwd),
                )
            else:
                args = shlex.split(command)
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(safe_cwd),
                )

            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []
            timed_out = False

            async def _read_stream(stream: asyncio.StreamReader, bucket: list[bytes], prefix: str):
                total = 0
                async for line in stream:
                    total += len(line)
                    if total <= self._max_output:
                        bucket.append(line)
                    decoded = line.decode("utf-8", errors="replace").rstrip()
                    if self._line_callback:
                        try:
                            self._line_callback(f"[{prefix}] {decoded}")
                        except Exception:
                            pass

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(proc.stdout, stdout_chunks, "stdout"),
                        _read_stream(proc.stderr, stderr_chunks, "stderr"),
                        proc.wait(),
                    ),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
                log.warning("Command timed out", command=command[:80], timeout=effective_timeout)
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass

            stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")

            if timed_out:
                stderr_str += f"\n[LegacyNode] Command timed out after {effective_timeout}s."

            result = {
                "stdout": stdout_str,
                "stderr": stderr_str,
                "returncode": proc.returncode if not timed_out else -9,
                "timed_out": timed_out,
                "blocked": False,
                "command": command,
            }
            log.info(
                "Command finished",
                returncode=result["returncode"],
                stdout_len=len(stdout_str),
                stderr_len=len(stderr_str),
            )
            return result

        except FileNotFoundError as e:
            return {
                "stdout": "",
                "stderr": f"Command not found: {e}",
                "returncode": 127,
                "timed_out": False,
                "blocked": False,
                "command": command,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": f"Execution error: {e}",
                "returncode": -1,
                "timed_out": False,
                "blocked": False,
                "command": command,
            }
