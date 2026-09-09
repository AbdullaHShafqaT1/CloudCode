import asyncio
import os
import re
import time
from datetime import datetime, timezone

class TerminalExecutorTool:
    """
    TerminalExecutorTool provides a controlled, secure, and monitored execution runtime 
    environment for executing shell commands.
    """
    
    # Simple regex for some definitely destructive or fork bomb patterns.
    FORBIDDEN_PATTERNS = [
        re.compile(r"rm\s+-r[fF]?\s+(/|/\*)"),  # block rm -rf /
        re.compile(r"mkfs"),                   # block mkfs
        re.compile(r":\(\)\{ :\|:& \};:"),     # block classical fork bomb
        re.compile(r"\b(shutdown|reboot|halt|poweroff)\b") # block system state commands
    ]
    
    def __init__(self, allowed_project_root: str):
        """
        :param allowed_project_root: The absolute path to the directory where commands are allowed to run.
        """
        # Normalize the path to ensure reliable comparisons
        self.allowed_project_root = os.path.normpath(os.path.abspath(allowed_project_root))

    async def call(self, action: str, **kwargs) -> dict:
        """
        Unified async entrypoint.
        """
        if action == "run_command":
            return await self.run_command(**kwargs)
        else:
            return self._build_error_response(action, kwargs.get("command", ""), "Unsupported action")

    async def run_command(self, command: str, cwd: str = None, timeout: int = 60, env: dict = None) -> dict:
        """
        Execute a one-off CLI command.
        """
        start_time = time.time()
        start_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # 1. Check forbidden commands
        if not self._is_command_allowed(command):
            return self._build_error_response(
                "run_command", command, "Command violates safety guardrails (forbidden pattern)."
            )
            
        # 2. Resolve and verify CWD
        try:
            target_cwd = self._resolve_and_verify_cwd(cwd)
        except ValueError as e:
            return self._build_error_response("run_command", command, str(e))
            
        # 3. Prepare Environment variables
        target_env = os.environ.copy()
        if env:
            target_env.update(env)
            
        # Ensure non-interactive if node/debian is involved (best effort heuristic)
        if "DEBIAN_FRONTEND" not in target_env:
            target_env["DEBIAN_FRONTEND"] = "noninteractive"
        if "CI" not in target_env:
            target_env["CI"] = "true"
            
        process = None
        stdout_text = ""
        stderr_text = ""
        truncated = False
        exit_code = None
        status = "error"
        error_message = None

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=target_cwd,
                env=target_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout_task = asyncio.create_task(self._read_stream_with_truncation(process.stdout))
            stderr_task = asyncio.create_task(self._read_stream_with_truncation(process.stderr))
            
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
                exit_code = process.returncode
                status = "success"
                if exit_code != 0:
                    status = "error"
                    
            except asyncio.TimeoutError:
                status = "timeout"
                error_message = f"Command exceeded timeout of {timeout} seconds."
                
                # Try graceful termination
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    # Force kill if graceful termination fails
                    try:
                        process.kill()
                        await process.wait()
                    except ProcessLookupError:
                        pass
                except ProcessLookupError:
                    pass
                
                exit_code = process.returncode
                
            stdout_result = await stdout_task
            stderr_result = await stderr_task
            
            stdout_text, stdout_trunc = stdout_result
            stderr_text, stderr_trunc = stderr_result
            truncated = stdout_trunc or stderr_trunc

        except Exception as e:
            status = "error"
            error_message = f"Failed to execute command: {str(e)}"
            if process and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        finally:
            end_time = time.time()
            duration_ms = int((end_time - start_time) * 1000)

            return {
                "status": status,
                "action": "run_command",
                "command": command,
                "cwd": target_cwd,
                "exit_code": exit_code if exit_code is not None else -1,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "duration_ms": duration_ms,
                "timestamp": start_timestamp,
                "error_message": error_message,
                "truncated": truncated
            }

    def _is_command_allowed(self, command: str) -> bool:
        """
        Check against forbidden patterns.
        """
        for pattern in self.FORBIDDEN_PATTERNS:
            if pattern.search(command):
                return False
        return True

    def _resolve_and_verify_cwd(self, cwd: str) -> str:
        """
        Resolve the working directory and ensure it falls within the allowed project root.
        """
        if not cwd:
            return self.allowed_project_root
            
        # Construct absolute path
        target_cwd = os.path.normpath(os.path.abspath(os.path.join(self.allowed_project_root, cwd)))
        
        # Verify it is a sub-directory of allowed_project_root
        common = os.path.commonpath([self.allowed_project_root, target_cwd])
        if common != self.allowed_project_root:
            raise ValueError(f"Target directory {target_cwd} is outside the allowed project root.")
            
        return target_cwd

    async def _read_stream_with_truncation(self, stream: asyncio.StreamReader, max_chars: int = 100000) -> tuple[str, bool]:
        """
        Reads from a stream, truncating if it exceeds max_chars.
        Returns a tuple of (captured_string, is_truncated).
        """
        if stream is None:
            return "", False
            
        output_chunks = []
        total_chars = 0
        truncated = False

        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
                
            if not truncated:
                # Replace decoding errors to prevent crash on bad encodings
                decoded = chunk.decode('utf-8', errors='replace')
                if total_chars + len(decoded) > max_chars:
                    remaining = max_chars - total_chars
                    output_chunks.append(decoded[:remaining])
                    truncated = True
                else:
                    output_chunks.append(decoded)
                    total_chars += len(decoded)
                    
        return "".join(output_chunks), truncated

    def _build_error_response(self, action: str, command: str, error_msg: str) -> dict:
        return {
            "status": "error",
            "action": action,
            "command": command,
            "cwd": self.allowed_project_root,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "error_message": error_msg,
            "truncated": False
        }
