"""
LegacyBridge — Streamlit Bridge Adapter (bridge_adapter.py)

Wrapper layer that mediates between the Streamlit session thread and the
async LegacyBridge core (StateManager, LLMClient, AgentController,
NotificationHub).  All public methods are synchronous so Streamlit can
call them directly without knowing about asyncio.

Key responsibilities:
  * Manage a persistent asyncio event loop in a daemon background thread
    so that async LegacyBridge calls never block the Streamlit UI thread.
  * Surface Simulation / Mock, Local Runtime, and Remote Link modes.
  * Provide health-check, ping, environment-validation helpers.
  * Wrap task dispatch (submit_task), status polling, live-output streaming,
    and session lifecycle (initialize / reset / terminate).
  * Return structured result dicts so callers (testbed_app.py) never need
    to touch raw exceptions.
"""

from __future__ import annotations

import asyncio
import os
import random
import string
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---- Path Bootstrap ---------------------------------------------------------
_BASE = Path(__file__).resolve().parent
_REPO_ROOT = _BASE.parent
for _p in [str(_BASE), str(_REPO_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---- Enums & Constants ------------------------------------------------------

class BridgeMode(str, Enum):
    MOCK   = "Simulation / Mock"
    LOCAL  = "Local Runtime"
    REMOTE = "Remote Link"


class SessionStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    INITIALIZING = "INITIALIZING"
    READY        = "READY"
    RUNNING      = "RUNNING"
    ERROR        = "ERROR"
    TERMINATED   = "TERMINATED"


# ---- Result Wrapper ---------------------------------------------------------

@dataclass
class BridgeResult:
    """Uniform return type for every adapter call."""
    ok: bool
    data: Any = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    latency_ms: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Background Event Loop --------------------------------------------------

class _LoopThread:
    """
    Singleton background thread owning a persistent asyncio event loop.
    Used to safely run coroutines from synchronous Streamlit callbacks.
    """
    _instance: Optional["_LoopThread"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "_LoopThread":
        with cls._lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._loop = asyncio.new_event_loop()
                obj._thread = threading.Thread(
                    target=obj._loop.run_forever,
                    name="LegacyBridgeLoop",
                    daemon=True,
                )
                obj._thread.start()
                cls._instance = obj
        return cls._instance

    def run(self, coro) -> Any:
        """Submit a coroutine to the background loop and block until done."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=300)


_loop_thread = _LoopThread()


def _run(coro) -> Any:
    return _loop_thread.run(coro)


# ---- Mock Helpers -----------------------------------------------------------

def _mock_session_id() -> str:
    return "MOCK-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


_MOCK_LOG_TEMPLATES = [
    "[CoderAgent] Scanning workspace directory tree...",
    "[CoderAgent] Found {n} Python files in /src",
    "[tools] Calling: scan_tree",
    "[tools] Calling: read_file",
    "[CoderAgent] Reading existing implementation...",
    "[stdout] Successfully completed step {n}",
    "[CoderAgent] Writing updated module to disk...",
    "[tools] Calling: write_file",
    "[tools] Calling: execute_command",
    "[stdout] All tests passed",
    "[CriticAgent] Reviewing coder output...",
    "[CriticAgent] APPROVED -- implementation looks correct and complete.",
    "[CoderAgent] TASK_COMPLETE -- task finished successfully.",
]


def _check_autogen() -> str:
    try:
        import autogen_agentchat
        return "v" + getattr(autogen_agentchat, "__version__", "?")
    except ImportError:
        return "Not installed"


def _check_streamlit() -> str:
    try:
        import streamlit
        return "v" + getattr(streamlit, "__version__", "?")
    except ImportError:
        return "Not installed"


def _check_pkg(name: str) -> str:
    try:
        mod = __import__(name.replace("-", "_"))
        ver = getattr(mod, "__version__", "?")
        return "v" + ver
    except ImportError:
        return "Not installed"


# ---- BridgeAdapter ----------------------------------------------------------

class BridgeAdapter:
    """
    Single-instance adapter binding Streamlit session state to LegacyBridge.
    """

    def __init__(
        self,
        mode: BridgeMode = BridgeMode.MOCK,
        workspace_root: str = ".",
        tunnel_url: str = "",
        llm_model: str = "qwen2.5-coder:32b",
        timeout: int = 180,
        max_iterations: int = 25,
    ):
        self.mode            = mode
        self.workspace_root  = workspace_root
        self.tunnel_url      = tunnel_url
        self.llm_model       = llm_model
        self.timeout         = timeout
        self.max_iterations  = max_iterations

        self.session_id: Optional[str] = None
        self.session_status: SessionStatus = SessionStatus.DISCONNECTED
        self.session_started_at: Optional[str] = None

        self._log_lines: List[str] = []
        self._last_payload: dict   = {}
        self._task_running: bool   = False
        self._dispatch_error: Optional[str] = None

        self._state_manager    = None
        self._notification_hub = None
        self._llm_client       = None
        self._controller       = None
        self._link_bridge      = None

    # -- Factory --------------------------------------------------------------

    @classmethod
    def from_session(
        cls,
        mode: BridgeMode = BridgeMode.MOCK,
        workspace_root: str = ".",
        tunnel_url: str = "",
        llm_model: str = "qwen2.5-coder:32b",
        timeout: int = 180,
        max_iterations: int = 25,
    ) -> "BridgeAdapter":
        import streamlit as st
        key = "__bridge_adapter__"
        if key not in st.session_state:
            st.session_state[key] = cls(
                mode=mode,
                workspace_root=workspace_root,
                tunnel_url=tunnel_url,
                llm_model=llm_model,
                timeout=timeout,
                max_iterations=max_iterations,
            )
        adapter: BridgeAdapter = st.session_state[key]
        adapter.mode           = mode
        adapter.workspace_root = workspace_root
        adapter.tunnel_url     = tunnel_url
        adapter.llm_model      = llm_model
        adapter.timeout        = timeout
        adapter.max_iterations = max_iterations
        return adapter

    # -- Session Lifecycle ----------------------------------------------------

    def initialize_session(self) -> BridgeResult:
        t0 = time.monotonic()
        try:
            self.session_status = SessionStatus.INITIALIZING
            self._log_lines.clear()
            self._last_payload   = {}
            self._dispatch_error = None

            if self.mode == BridgeMode.MOCK:
                time.sleep(0.4)
                self.session_id = _mock_session_id()
                self.session_started_at = datetime.now(timezone.utc).isoformat()
                self.session_status = SessionStatus.READY
                self._emit("OK [session] Mock session initialized")
                self._emit("   Session ID : " + self.session_id)
                self._emit("   Mode       : " + self.mode.value)
                self._emit("   Workspace  : " + self.workspace_root)
                payload = {
                    "session_id": self.session_id,
                    "mode": self.mode.value,
                    "workspace": self.workspace_root,
                    "status": "ready",
                }
            else:
                payload = _run(self._async_init_live())

            ms = round((time.monotonic() - t0) * 1000, 1)
            self._last_payload = payload
            return BridgeResult(ok=True, data=payload, latency_ms=ms)

        except Exception as exc:
            self.session_status = SessionStatus.ERROR
            tb = traceback.format_exc()
            self._emit("ERROR [session] " + str(exc))
            return BridgeResult(ok=False, error=str(exc), traceback=tb,
                                latency_ms=round((time.monotonic() - t0) * 1000, 1))

    async def _async_init_live(self) -> dict:
        from legacynode.core.state_manager import StateManager
        from legacynode.core.notification_hub import NotificationHub
        from legacynode.core.llm_client import LLMClient, LLMConfig

        ws = self.workspace_root
        base_url = (
            self.tunnel_url.rstrip("/") + "/v1"
            if self.mode == BridgeMode.REMOTE and self.tunnel_url
            else "http://localhost:11434/v1"
        )

        state = StateManager(
            db_path=str(Path(ws) / ".legacynode" / "testbed_state.db"),
            history_size=200,
        )
        await state.initialize()
        self._state_manager = state

        hub = NotificationHub(
            db_path=str(Path(ws) / ".legacynode" / "testbed_notifications.db"),
        )
        await hub.initialize()
        self._notification_hub = hub

        llm = LLMClient(LLMConfig(
            base_url=base_url,
            model=self.llm_model,
            api_key=os.environ.get("LLM_API_KEY", "ollama"),
            timeout_seconds=float(self.timeout),
        ))
        await llm.connect()
        self._llm_client = llm

        self.session_id = str(uuid.uuid4())[:8].upper()
        self.session_started_at = datetime.now(timezone.utc).isoformat()
        self.session_status = SessionStatus.READY
        self._emit("OK [session] Live session initialized -- " + self.session_id)
        return {
            "session_id": self.session_id,
            "mode": self.mode.value,
            "workspace": ws,
            "base_url": base_url,
            "model": self.llm_model,
            "status": "ready",
        }

    def reset_state(self) -> BridgeResult:
        t0 = time.monotonic()
        try:
            self._log_lines.clear()
            self._last_payload   = {}
            self._dispatch_error = None
            self._task_running   = False
            if self._state_manager and self.mode != BridgeMode.MOCK:
                self._state_manager.clear_execution_state()
            self._emit("RESET [session] State reset")
            return BridgeResult(ok=True, data={"reset": True},
                                latency_ms=round((time.monotonic() - t0) * 1000, 1))
        except Exception as exc:
            return BridgeResult(ok=False, error=str(exc),
                                traceback=traceback.format_exc())

    def terminate_session(self) -> BridgeResult:
        t0 = time.monotonic()
        try:
            if self.mode != BridgeMode.MOCK:
                _run(self._async_terminate())
            self.session_id     = None
            self.session_status = SessionStatus.TERMINATED
            self._task_running  = False
            self._emit("STOP [session] Session terminated")
            return BridgeResult(ok=True, data={"terminated": True},
                                latency_ms=round((time.monotonic() - t0) * 1000, 1))
        except Exception as exc:
            self.session_status = SessionStatus.ERROR
            return BridgeResult(ok=False, error=str(exc),
                                traceback=traceback.format_exc())

    async def _async_terminate(self) -> None:
        if self._controller:
            await self._controller.stop()
        if self._llm_client:
            await self._llm_client.close()
        if self._notification_hub:
            await self._notification_hub.close()
        if self._state_manager:
            await self._state_manager.close()

    # -- Health / Connection Tests --------------------------------------------

    def health_check(self) -> BridgeResult:
        t0 = time.monotonic()
        try:
            if self.mode == BridgeMode.MOCK:
                time.sleep(0.1)
                payload = {
                    "endpoint": "http://localhost:11434/v1",
                    "status": "healthy",
                    "model": "qwen2.5-coder:32b",
                    "latency_ms": round(random.uniform(30, 90), 1),
                    "models_available": ["qwen2.5-coder:32b", "llama3.1:8b"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._emit("HEALTH [health-check] Endpoint probed (mock) -- healthy")
                self._last_payload = payload
                return BridgeResult(ok=True, data=payload,
                                    latency_ms=round((time.monotonic() - t0) * 1000, 1))

            if not self._llm_client:
                raise RuntimeError("Session not initialized.")

            healthy = _run(self._llm_client.probe_health())
            models  = _run(self._llm_client.list_models()) if healthy else []
            payload = {
                "endpoint": self._llm_client._config.base_url,
                "status": "healthy" if healthy else "unreachable",
                "model": self._llm_client._config.model,
                "models_available": models,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._emit("HEALTH [health-check] " + ("healthy" if healthy else "UNREACHABLE"))
            self._last_payload = payload
            return BridgeResult(ok=healthy, data=payload,
                                latency_ms=round((time.monotonic() - t0) * 1000, 1))
        except Exception as exc:
            return BridgeResult(ok=False, error=str(exc),
                                traceback=traceback.format_exc())

    def ping(self) -> BridgeResult:
        t0 = time.monotonic()
        try:
            if self.mode == BridgeMode.MOCK:
                rtt = round(random.uniform(5, 30), 1)
                time.sleep(rtt / 1000)
                payload = {
                    "pong": True,
                    "round_trip_ms": rtt,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._emit("PING [ping] Pong received -- " + str(rtt) + " ms (mock)")
                self._last_payload = payload
                return BridgeResult(ok=True, data=payload, latency_ms=rtt)

            if not self._llm_client:
                raise RuntimeError("Session not initialized.")
            _run(self._llm_client.list_models())
            rtt = round((time.monotonic() - t0) * 1000, 1)
            payload = {
                "pong": True,
                "round_trip_ms": rtt,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._emit("PING [ping] Pong -- " + str(rtt) + " ms")
            self._last_payload = payload
            return BridgeResult(ok=True, data=payload, latency_ms=rtt)
        except Exception as exc:
            return BridgeResult(ok=False, error=str(exc),
                                traceback=traceback.format_exc())

    def env_check(self) -> BridgeResult:
        t0 = time.monotonic()
        try:
            payload = {
                "python_version": sys.version.split()[0],
                "platform": sys.platform,
                "workspace_root": str(Path(self.workspace_root).resolve()),
                "workspace_exists": Path(self.workspace_root).exists(),
                "TUNNEL_URL": os.environ.get("TUNNEL_URL", "Not set"),
                "LLM_API_KEY": "Set" if os.environ.get("LLM_API_KEY") else "Not set",
                "LLM_MODEL": os.environ.get("LLM_MODEL", self.llm_model),
                "autogen": _check_autogen(),
                "streamlit": _check_streamlit(),
                "aiosqlite": _check_pkg("aiosqlite"),
                "httpx": _check_pkg("httpx"),
                "structlog": _check_pkg("structlog"),
                "openai": _check_pkg("openai"),
            }
            self._emit("ENV [env-check] Environment validated")
            for k, v in payload.items():
                self._emit("   " + k + ": " + str(v))
            self._last_payload = payload
            return BridgeResult(ok=True, data=payload,
                                latency_ms=round((time.monotonic() - t0) * 1000, 1))
        except Exception as exc:
            return BridgeResult(ok=False, error=str(exc),
                                traceback=traceback.format_exc())

    # -- Task Dispatch --------------------------------------------------------

    def submit_task(
        self,
        description: str,
        timeout_override: Optional[int] = None,
        verbose: bool = False,
    ) -> BridgeResult:
        t0 = time.monotonic()
        if self._task_running:
            return BridgeResult(ok=False,
                                error="A task is already running. Stop it first.")
        self._task_running   = True
        self._dispatch_error = None
        try:
            if self.mode == BridgeMode.MOCK:
                result = self._mock_task(description, verbose)
            else:
                result = _run(self._async_submit_task(description, timeout_override))

            self._task_running = False
            ms = round((time.monotonic() - t0) * 1000, 1)
            self._last_payload = result
            return BridgeResult(ok=True, data=result, latency_ms=ms)
        except Exception as exc:
            self._task_running   = False
            self._dispatch_error = str(exc)
            tb = traceback.format_exc()
            self._emit("ERROR [task] " + str(exc))
            return BridgeResult(ok=False, error=str(exc), traceback=tb,
                                latency_ms=round((time.monotonic() - t0) * 1000, 1))

    def _mock_task(self, description: str, verbose: bool) -> dict:
        task_id = str(uuid.uuid4())[:8]
        self._emit("TASK [task] Dispatching -- ID: " + task_id)
        self._emit("   Description: " + description[:120])
        steps = random.randint(5, len(_MOCK_LOG_TEMPLATES))
        step_delay = 0.06 if verbose else 0.02
        for i, template in enumerate(_MOCK_LOG_TEMPLATES[:steps]):
            time.sleep(step_delay)
            self._emit(template.format(n=i + 1))
        summary = "TASK_COMPLETE -- Mock task '" + description[:60] + "' finished in " + str(steps) + " steps."
        self._emit("OK " + summary)
        return {
            "task_id": task_id,
            "status": "COMPLETED",
            "summary": summary,
            "steps": steps,
            "mode": "mock",
            "description": description,
        }

    async def _async_submit_task(
        self, description: str, timeout_override: Optional[int]
    ) -> dict:
        from legacynode.core.agent_controller import AgentController

        if not self._state_manager or not self._llm_client or not self._notification_hub:
            raise RuntimeError("Session not initialized. Call initialize_session() first.")

        if not self._controller:
            self._controller = AgentController(
                llm_client=self._llm_client,
                state_manager=self._state_manager,
                notification_hub=self._notification_hub,
                workspace_root=self.workspace_root,
                max_iterations=self.max_iterations,
            )

        self.session_status = SessionStatus.RUNNING
        try:
            summary = await self._controller.run_task(description)
            self.session_status = SessionStatus.READY
            snap = self._state_manager.snapshot()
            return {
                "task_id": "live",
                "status": "COMPLETED",
                "summary": summary,
                "steps": snap.get("step_count", 0),
                "mode": self.mode.value,
                "description": description,
            }
        except Exception:
            self.session_status = SessionStatus.ERROR
            raise

    # -- Live Output & Polling ------------------------------------------------

    def get_live_output(self) -> List[str]:
        lines = list(self._log_lines)
        if self._state_manager and self.mode != BridgeMode.MOCK:
            lines.extend(self._state_manager.get_live_output())
        return lines

    def get_snapshot(self) -> dict:
        base = {
            "session_id": self.session_id,
            "session_status": self.session_status.value,
            "mode": self.mode.value,
            "workspace": self.workspace_root,
            "task_running": self._task_running,
            "log_line_count": len(self._log_lines),
            "last_payload": self._last_payload,
            "dispatch_error": self._dispatch_error,
        }
        if self._state_manager and self.mode != BridgeMode.MOCK:
            base.update(self._state_manager.snapshot())
        return base

    def get_notifications(self, limit: int = 30) -> List[dict]:
        if self._notification_hub and self.mode != BridgeMode.MOCK:
            try:
                notifs = _run(self._notification_hub.get_all(limit=limit))
                return [n.to_dict() for n in notifs]
            except Exception:
                pass
        return []

    def get_task_history(self, limit: int = 50) -> List[dict]:
        if self._state_manager and self.mode != BridgeMode.MOCK:
            try:
                return _run(self._state_manager.get_task_history(limit=limit))
            except Exception:
                pass
        return []

    # -- Internals ------------------------------------------------------------

    def _emit(self, line: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._log_lines.append("[" + ts + "] " + line)

    @property
    def is_ready(self) -> bool:
        return self.session_status in (SessionStatus.READY, SessionStatus.RUNNING)

    # -- NodeInsight Reader Integration ---------------------------------------

    def reader_call(
        self,
        action: str,
        workspace_root: Optional[str] = None,
        **kwargs: Any,
    ) -> BridgeResult:
        """
        Dispatch a read/analysis request to NodeInsight (FileReaderTool).

        LegacyBridge remains responsible for session management and dispatch;
        NodeInsight remains responsible for all file reading, AST parsing, and
        symbol extraction.  NodeInsight never touches cloud or LLM services.

        Parameters
        ----------
        action : str
            NodeInsight action name.  One of:
            ``list_dir``, ``search_files``, ``read_file``,
            ``read_file_chunked``, ``search_in_file``,
            ``parse_ast``, ``search_symbols``, ``read_structured``.
        workspace_root : str | None
            Workspace root for NodeInsight's sandbox.  Defaults to
            ``self.workspace_root``.
        **kwargs
            Action-specific arguments forwarded verbatim to NodeInsight.
            All path-traversal security checks are handled inside NodeInsight.

        Returns
        -------
        BridgeResult
            ``ok=True``  — ``data`` contains the NodeInsight ToolResponse dict
                           (keys: ``status``, ``data``, ``error_message``).
            ``ok=False`` — ``error`` contains the structured error string from
                           NodeInsight (e.g. ``"FileNotFound: src/missing.py"``).
                           LegacyBridge remains operational after an error.

        Examples
        --------
        Read a file::

            result = adapter.reader_call("read_file", path="src/main.py")
            if result.ok:
                content = result.data["data"]["content"]

        Parse AST symbols::

            result = adapter.reader_call("parse_ast", path="src/main.py")
            if result.ok:
                functions = result.data["data"]["functions"]

        Notes
        -----
        * In MOCK mode a representative simulated response is returned so that
          integration tests and UI demos work without a real workspace.
        * In LOCAL and REMOTE modes the request is always dispatched to the
          local NodeInsight instance — NodeInsight never communicates remotely.
        """
        import time as _time
        t0 = _time.monotonic()
        ws = workspace_root or self.workspace_root

        # ---- MOCK mode: return a simulated ToolResponse ---------------------
        if self.mode == BridgeMode.MOCK:
            mock_resp = {
                "status": "success",
                "data": {
                    "action": action,
                    "path":   kwargs.get("path", "(mock)"),
                    "note":   "Simulated NodeInsight response (MOCK mode). "
                              "Switch to LOCAL or REMOTE for real file analysis.",
                },
                "error_message": None,
            }
            self._emit(
                f"READER [mock] NodeInsight.{action}("
                + ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
                + ")"
            )
            ms = round((_time.monotonic() - t0) * 1000, 1)
            self._last_payload = mock_resp
            return BridgeResult(ok=True, data=mock_resp, latency_ms=ms)

        # ---- LOCAL / REMOTE mode: delegate to real NodeInsight --------------
        try:
            from legacynode.tools.node_insight_bridge import NodeInsightBridge

            bridge = NodeInsightBridge(workspace_root=ws)
            self._emit(f"READER [nodeinsight] Dispatching: {action}")

            tool_resp: dict = _run(bridge.call(action, **kwargs))

            ms = round((_time.monotonic() - t0) * 1000, 1)
            ok = tool_resp.get("status") == "success"

            if not ok:
                err_msg = tool_resp.get("error_message", "Unknown error from NodeInsight")
                self._emit(f"READER [nodeinsight] Error: {err_msg}")
            else:
                self._emit(f"READER [nodeinsight] OK — action={action}")

            self._last_payload = tool_resp
            return BridgeResult(
                ok=ok,
                data=tool_resp,
                error=tool_resp.get("error_message") if not ok else None,
                latency_ms=ms,
            )

        except Exception as exc:
            import traceback as _tb
            ms = round((_time.monotonic() - t0) * 1000, 1)
            err_msg = f"NodeInsightBridge raised {type(exc).__name__}: {exc}"
            self._emit(f"ERROR [nodeinsight] {err_msg}")
            return BridgeResult(
                ok=False,
                error=err_msg,
                traceback=_tb.format_exc(),
                latency_ms=ms,
            )

    # -- NodePulse Terminal Integration ---------------------------------------

    def terminal_call(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: int = 60,
        env: Optional[Dict[str, str]] = None,
        allowed_project_root: Optional[str] = None,
    ) -> BridgeResult:
        """
        Dispatch a terminal command to NodePulse (TerminalExecutorTool).

        LegacyBridge remains responsible for session management; NodePulse
        remains responsible for all subprocess execution, output capture,
        forbidden-pattern blocking, and CWD confinement.

        Parameters
        ----------
        command : str
            Shell command string to execute.
        cwd : str | None
            Working directory for the command.  Relative paths are resolved
            inside NodePulse against ``allowed_project_root``.
        timeout : int
            Per-command timeout in seconds (default 60).
        env : dict | None
            Additional environment variables injected into the subprocess.
        allowed_project_root : str | None
            NodePulse sandbox root.  Defaults to ``self.workspace_root``.

        Returns
        -------
        BridgeResult
            ``ok=True``  — ``data`` contains the NodePulse response dict
                           (keys: ``status``, ``action``, ``command``, ``cwd``,
                           ``exit_code``, ``stdout``, ``stderr``,
                           ``duration_ms``, ``timestamp``, ``error_message``,
                           ``truncated``).
            ``ok=False`` — ``error`` contains the error_message from NodePulse
                           or the exception description.
                           LegacyBridge remains operational after an error.

        Examples
        --------
        Run a command::

            result = adapter.terminal_call("python --version")
            if result.ok:
                print(result.data["stdout"])

        Notes
        -----
        * In MOCK mode a safe simulated response is returned so that
          integration tests and UI demos work without NodePulse installed.
        * In LOCAL and REMOTE modes the request is always dispatched to the
          local NodePulse instance — NodePulse never communicates remotely.
        * All forbidden-pattern and CWD-confinement checks are enforced
          exclusively inside NodePulse's ``TerminalExecutorTool``.
        """
        import time as _time
        t0 = _time.monotonic()
        root = allowed_project_root or self.workspace_root

        # ---- MOCK mode: return a simulated response -------------------------
        if self.mode == BridgeMode.MOCK:
            import datetime as _dt
            mock_resp = {
                "status": "success",
                "action": "run_command",
                "command": command,
                "cwd": root,
                "exit_code": 0,
                "stdout": f"[MOCK] Simulated output for: {command}",
                "stderr": "",
                "duration_ms": 0,
                "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                "error_message": None,
                "truncated": False,
            }
            self._emit(
                f"TERMINAL [mock] NodePulse.run_command(command={command!r})"
            )
            ms = round((_time.monotonic() - t0) * 1000, 1)
            self._last_payload = mock_resp
            return BridgeResult(ok=True, data=mock_resp, latency_ms=ms)

        # ---- LOCAL / REMOTE mode: delegate to real NodePulse ---------------
        try:
            from legacynode.tools.node_pulse_bridge import NodePulseBridge

            bridge = NodePulseBridge(allowed_project_root=root)
            self._emit(f"TERMINAL [nodepulse] Dispatching: {command!r}")

            kwargs: Dict[str, Any] = {"command": command}
            if cwd is not None:
                kwargs["cwd"] = cwd
            if timeout != 60:
                kwargs["timeout"] = timeout
            if env is not None:
                kwargs["env"] = env

            resp: dict = _run(bridge.call("run_command", **kwargs))

            ms = round((_time.monotonic() - t0) * 1000, 1)
            ok = resp.get("status") == "success"

            if not ok:
                err_msg = resp.get("error_message", "NodePulse returned non-success status")
                self._emit(f"TERMINAL [nodepulse] Error: {err_msg}")
            else:
                self._emit(
                    f"TERMINAL [nodepulse] OK — exit_code={resp.get('exit_code')} "
                    f"duration={resp.get('duration_ms')} ms"
                )

            self._last_payload = resp
            return BridgeResult(
                ok=ok,
                data=resp,
                error=resp.get("error_message") if not ok else None,
                latency_ms=ms,
            )

        except Exception as exc:
            import traceback as _tb
            ms = round((_time.monotonic() - t0) * 1000, 1)
            err_msg = f"NodePulseBridge raised {type(exc).__name__}: {exc}"
            self._emit(f"ERROR [nodepulse] {err_msg}")
            return BridgeResult(
                ok=False,
                error=err_msg,
                traceback=_tb.format_exc(),
                latency_ms=ms,
            )

    # -- NodeForge Writer Integration -----------------------------------------

    def writer_call(
        self,
        action: str,
        workspace_root: Optional[str] = None,
        **kwargs: Any,
    ) -> BridgeResult:
        """
        Dispatch a file write, patch, or modification request to NodeForge (FileWriterTool).

        LegacyBridge remains responsible for session management; NodeForge
        remains responsible for atomic writes, rollback backups, diff-patching,
        and workspace path confinement.

        Parameters
        ----------
        action : str
            NodeForge action name. One of:
            ``write_file``, ``patch_file``, ``append_file``, ``create_dir``,
            ``delete_file``, ``rollback``, ``create_backup``.
        workspace_root : str | None
            Workspace root for NodeForge's sandbox. Defaults to ``self.workspace_root``.
        **kwargs
            Action-specific arguments forwarded verbatim to NodeForge.

        Returns
        -------
        BridgeResult
            ``ok=True``  — ``data`` contains the NodeForge response dict.
            ``ok=False`` — ``error`` contains the error message.
                           LegacyBridge remains operational after an error.
        """
        import time as _time
        t0 = _time.monotonic()
        ws = workspace_root or self.workspace_root

        # ---- MOCK mode: return a simulated response -------------------------
        if self.mode == BridgeMode.MOCK:
            mock_resp = {
                "status": "success",
                "action": action,
                "path": kwargs.get("path", "(mock)"),
                "bytes_written": len(str(kwargs.get("content", ""))),
                "error_message": None,
                "note": "Simulated NodeForge response (MOCK mode).",
            }
            self._emit(
                f"WRITER [mock] NodeForge.{action}("
                + ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
                + ")"
            )
            ms = round((_time.monotonic() - t0) * 1000, 1)
            self._last_payload = mock_resp
            return BridgeResult(ok=True, data=mock_resp, latency_ms=ms)

        # ---- LOCAL / REMOTE mode: delegate to real NodeForge ----------------
        try:
            from pathlib import Path as _Path
            import sys as _sys
            _root = _Path(ws).resolve()
            try:
                from tools.file_writer_tool import FileWriterTool
            except ImportError:
                _nf_path = str(_root / "NodeForge")
                if _nf_path not in _sys.path:
                    _sys.path.insert(0, _nf_path)
                from tools.file_writer_tool import FileWriterTool

            writer = FileWriterTool(workspace_root=ws)
            self._emit(f"WRITER [nodeforge] Dispatching: {action}")

            tool_resp: dict = _run(writer.call(action, **kwargs))

            ms = round((_time.monotonic() - t0) * 1000, 1)
            ok = tool_resp.get("status") == "success"

            if not ok:
                err_msg = tool_resp.get("error_message", "Unknown error from NodeForge")
                self._emit(f"WRITER [nodeforge] Error: {err_msg}")
            else:
                self._emit(f"WRITER [nodeforge] OK — action={action}")

            self._last_payload = tool_resp
            return BridgeResult(
                ok=ok,
                data=tool_resp,
                error=tool_resp.get("error_message") if not ok else None,
                latency_ms=ms,
            )

        except Exception as exc:
            import traceback as _tb
            ms = round((_time.monotonic() - t0) * 1000, 1)
            err_msg = f"NodeForge raised {type(exc).__name__}: {exc}"
            self._emit(f"ERROR [nodeforge] {err_msg}")
            return BridgeResult(
                ok=False,
                error=err_msg,
                traceback=_tb.format_exc(),
                latency_ms=ms,
            )

    # -- NodeLink Remote Integration ------------------------------------------

    def link_call(
        self,
        action: str,
        target_id: str = "default_remote",
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> BridgeResult:
        """
        Dispatch a remote execution or communication request to NodeLink.

        LegacyBridge remains responsible for session state and dispatching;
        NodeLink orchestrates credentials, transport adapters, tunnels,
        and remote runtimes.

        Parameters
        ----------
        action : str
            NodeLink action name. One of:
            ``connect``, ``send_request``, ``run_remote``, ``fetch_results``,
            ``execute_and_wait``, ``disconnect``.
        target_id : str
            Identifier for the target runtime session.
        config : dict | None
            Connection configuration (endpoint, auth_type, token, adapter, etc.).
        **kwargs
            Action-specific arguments.

        Returns
        -------
        BridgeResult
            ``ok=True``  — ``data`` contains the NodeLink response dict.
            ``ok=False`` — ``error`` contains the error message and ``traceback``.
                           LegacyBridge remains operational after an error.
        """
        import time as _time
        t0 = _time.monotonic()

        # ---- MOCK mode: return a simulated response -------------------------
        if self.mode == BridgeMode.MOCK:
            if action in ("run_remote", "execute_and_wait"):
                job_id = kwargs.get("job_id", "mock_job_001")
                mock_resp = {
                    "status": "success",
                    "action": action,
                    "target_id": target_id,
                    "job_id": job_id,
                    "job_status": "COMPLETED",
                    "result_data": {
                        "output": "[MOCK Remote] Execution finished successfully.",
                        "exit_code": 0,
                        "telemetry": {"duration_ms": 120, "bytes_transferred": 2048},
                    },
                    "error_message": None,
                }
            elif action == "connect":
                mock_resp = {
                    "status": "success",
                    "action": "connect",
                    "target_id": target_id,
                    "handle": {
                        "target_id": target_id,
                        "status": "ESTABLISHED",
                        "endpoint": (config or {}).get("endpoint", "http://mock-cloud"),
                        "auth_type": (config or {}).get("auth_type", "NONE"),
                    },
                    "error_message": None,
                }
            elif action == "fetch_results":
                mock_resp = {
                    "status": "success",
                    "action": "fetch_results",
                    "job_id": kwargs.get("job_id", "mock_job_001"),
                    "job_status": "COMPLETED",
                    "result_data": {"exit_code": 0, "output": "[MOCK] Job completed."},
                    "error_message": None,
                }
            else:
                mock_resp = {
                    "status": "success",
                    "action": action,
                    "target_id": target_id,
                    "data": {"echo": kwargs},
                    "error_message": None,
                }

            self._emit(f"LINK [mock] NodeLink.{action}(target_id={target_id!r})")
            ms = round((_time.monotonic() - t0) * 1000, 1)
            self._last_payload = mock_resp
            return BridgeResult(ok=True, data=mock_resp, latency_ms=ms)

        # ---- LOCAL / REMOTE mode: delegate to real NodeLink -----------------
        try:
            if getattr(self, "_link_bridge", None) is None:
                from legacynode.tools.node_link_bridge import NodeLinkBridge
                self._link_bridge = NodeLinkBridge()

            bridge = self._link_bridge
            self._emit(f"LINK [nodelink] Dispatching: {action} (target={target_id})")

            call_kwargs: Dict[str, Any] = dict(kwargs)
            call_kwargs["target_id"] = target_id
            if config is not None:
                call_kwargs["config"] = config

            resp: dict = _run(bridge.call(action, **call_kwargs))

            ms = round((_time.monotonic() - t0) * 1000, 1)
            ok = resp.get("status") == "success"

            if not ok:
                err_msg = resp.get("error_message", "NodeLink returned non-success status")
                self._emit(f"LINK [nodelink] Error: {err_msg}")
            else:
                self._emit(f"LINK [nodelink] OK — action={action}")

            self._last_payload = resp
            return BridgeResult(
                ok=ok,
                data=resp,
                error=resp.get("error_message") if not ok else None,
                traceback=resp.get("traceback") if not ok else None,
                latency_ms=ms,
            )

        except Exception as exc:
            import traceback as _tb
            ms = round((_time.monotonic() - t0) * 1000, 1)
            err_msg = f"NodeLinkBridge raised {type(exc).__name__}: {exc}"
            self._emit(f"ERROR [nodelink] {err_msg}")
            return BridgeResult(
                ok=False,
                error=err_msg,
                traceback=_tb.format_exc(),
                latency_ms=ms,
            )

    # -- NodeCore Orchestration Integration ------------------------------------

    def core_call(
        self,
        action: str,
        workspace_root: Optional[str] = None,
        **kwargs: Any,
    ) -> BridgeResult:
        """
        Dispatch an orchestration or diagnosis request to NodeCore.

        LegacyBridge remains responsible for session state and dispatching;
        NodeCore acts as the central multi-agent/multi-project orchestrator.

        Parameters
        ----------
        action : str
            NodeCore action name. One of:
            ``diagnose_and_plan``, ``diagnose``, ``generate_plan``,
            ``initialize_session``, ``dispatch_tool``.
        workspace_root : str | None
            Workspace root for the operation. Defaults to ``self.workspace_root``.
        **kwargs
            Action-specific parameters.

        Returns
        -------
        BridgeResult
            Standard bridge result with data, error, latency.
        """
        import time as _time
        t0 = _time.monotonic()
        ws = workspace_root or self.workspace_root

        # ---- MOCK mode: return simulated orchestration response -------------
        if self.mode == BridgeMode.MOCK:
            mock_resp = {
                "status": "success",
                "action": action,
                "orchestrator": "NodeCore",
                "state": "SUCCESS",
                "details": {"workspace": ws, "action": action, "echo": kwargs},
                "error_message": None,
            }
            self._emit(f"CORE [mock] NodeCore.{action}(ws={ws!r})")
            ms = round((_time.monotonic() - t0) * 1000, 1)
            self._last_payload = mock_resp
            return BridgeResult(ok=True, data=mock_resp, latency_ms=ms)

        # ---- LOCAL / REMOTE mode: delegate to NodeCore ----------------------
        try:
            from node_core.core import NodeCore, ErrorParser, RemediationGenerator, initialize_session
            from node_core.tools import configure_tools

            configure_tools(ws)
            core = NodeCore(workspace_root=ws)
            self._emit(f"CORE [nodecore] Dispatching: {action} (ws={ws})")

            if action == "diagnose":
                trace = kwargs.get("trace", kwargs.get("error", ""))
                diag = ErrorParser.parse(trace)
                data = diag.model_dump()
            elif action in ("diagnose_and_plan", "remediate"):
                trace = kwargs.get("trace", kwargs.get("error", ""))
                dispatch_call = core.handle_execution_failure(trace)
                plan = core.generate_remediation_plan()
                patch_call = core.prepare_patch_dispatch(plan)
                data = {
                    "diagnosis": core.current_diagnosis.model_dump() if core.current_diagnosis else {},
                    "plan": plan.model_dump(),
                    "inspection_dispatch": dispatch_call.model_dump(),
                    "patch_dispatch": patch_call.model_dump(),
                    "state": core.state.value,
                }
            elif action == "initialize_session":
                sess = initialize_session(
                    project_id=kwargs.get("project_id", "proj_default"),
                    workspace_path=ws,
                    domain_type=kwargs.get("domain_type", "SOFTWARE_DEV"),
                    env_config=kwargs.get("env_config", {}),
                    max_rounds=kwargs.get("max_rounds", 30),
                )
                data = sess.model_dump()
            else:
                raise ValueError(f"Unsupported NodeCore action: {action}")

            ms = round((_time.monotonic() - t0) * 1000, 1)
            self._emit(f"CORE [nodecore] OK — action={action}")
            resp = {"status": "success", "data": data}
            self._last_payload = resp
            return BridgeResult(ok=True, data=resp, latency_ms=ms)

        except Exception as exc:
            import traceback as _tb
            ms = round((_time.monotonic() - t0) * 1000, 1)
            err_msg = f"NodeCore raised {type(exc).__name__}: {exc}"
            self._emit(f"ERROR [nodecore] {err_msg}")
            return BridgeResult(
                ok=False,
                error=err_msg,
                traceback=_tb.format_exc(),
                latency_ms=ms,
            )


