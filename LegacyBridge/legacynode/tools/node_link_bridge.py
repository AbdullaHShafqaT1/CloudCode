"""
LegacyBridge — NodeLink Bridge Adapter (node_link_bridge.py)
============================================================

Thin adapter connecting LegacyBridge to NodeLink's remote execution gateway.
Follows the same pattern as NodeInsightBridge and NodePulseBridge — zero
unnecessary dependencies, clean failure boundaries, and unified BridgeResult
envelope translation.

Responsibilities
----------------
* Manage NodeLink gateway instance lifecycle (`connect`, `run_remote`,
  `fetch_results`, `send_request`, `disconnect`).
* Surface high-level `execute_and_wait` action for seamless dispatch and polling.
* Translate NodeLink responses into unified `BridgeResult` objects.
* Ensure complete fault isolation: failures in remote execution or network timeouts
  never crash LegacyBridge or corrupt session state.

Data Contract
-------------
Request (caller → bridge → NodeLink):
    action : str
        One of: "connect", "send_request", "run_remote", "fetch_results",
        "execute_and_wait", "disconnect".
    **kwargs
        Action-specific arguments.

Response (NodeLink → bridge → caller):
    Dict with:
    {
        "status": "success" | "error",
        "action": <str>,
        "data": <dict | Any>,
        "error_message": <str | None>,
        ...
    }
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import structlog

# ─── sys.path Bootstrap ───────────────────────────────────────────────────────
_THIS              = Path(__file__).resolve()
_LEGACYBRIDGE_ROOT = _THIS.parent.parent.parent          # …/LegacyBridge
_NODELINK_ROOT     = _LEGACYBRIDGE_ROOT.parent / "NodeLink"

for _p in [str(_NODELINK_ROOT), str(_LEGACYBRIDGE_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ─── NodeLink Import ──────────────────────────────────────────────────────────
try:
    from nodelink import (  # type: ignore
        NodeLink as _NodeLink,
        ConnectionHandle as _ConnectionHandle,
        ApiResponse as _ApiResponse,
        RemoteJobHandle as _RemoteJobHandle,
        JobResult as _JobResult,
    )
    _NODELINK_AVAILABLE = True
except ImportError:
    _NODELINK_AVAILABLE = False
    _NodeLink = None  # type: ignore
    _ConnectionHandle = None  # type: ignore
    _ApiResponse = None  # type: ignore
    _RemoteJobHandle = None  # type: ignore
    _JobResult = None  # type: ignore

log = structlog.get_logger(__name__)


# ─── NodeLinkBridge ───────────────────────────────────────────────────────────

class NodeLinkBridge:
    """
    Adapter bridging LegacyBridge requests to the NodeLink remote execution gateway.
    """

    def __init__(self, global_config: Optional[Dict[str, Any]] = None) -> None:
        if not _NODELINK_AVAILABLE:
            raise ImportError(
                "NodeLink is not importable. "
                "Ensure the NodeLink directory is adjacent to LegacyBridge, "
                "or add it to sys.path manually."
            )
        self._nodelink: _NodeLink = _NodeLink(global_config=global_config)
        self._connected_targets: set[str] = set()
        log.debug("NodeLinkBridge initialised")

    # ── Availability check ────────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        """Return True if NodeLink can be imported successfully."""
        return _NODELINK_AVAILABLE

    # ── Primary async dispatcher ──────────────────────────────────────────────

    async def call(self, action: str, **kwargs: Any) -> dict:
        """
        Async dispatcher — routes *action* to the corresponding NodeLink operation.
        Always returns a structured response dict and never raises unhandled exceptions.
        """
        t0 = time.monotonic()
        log.debug("NodeLinkBridge.call", action=action, kwargs=list(kwargs))

        try:
            if action == "connect":
                target_id = kwargs.get("target_id", "default_remote")
                config = kwargs.get("config", {})
                handle = self._nodelink.connect(target_id, config)
                self._connected_targets.add(target_id)
                handle_dict = handle.to_dict() if hasattr(handle, "to_dict") else dict(handle)
                return {
                    "status": "success",
                    "action": "connect",
                    "target_id": target_id,
                    "handle": handle_dict,
                    "error_message": None,
                }

            elif action == "disconnect":
                target_id = kwargs.get("target_id", "default_remote")
                ok = await self._nodelink.disconnect(target_id)
                self._connected_targets.discard(target_id)
                return {
                    "status": "success" if ok else "error",
                    "action": "disconnect",
                    "target_id": target_id,
                    "error_message": None if ok else f"Failed to disconnect {target_id}",
                }

            elif action == "send_request":
                target_id = kwargs.get("target_id", "default_remote")
                endpoint = kwargs.get("endpoint", "")
                method = kwargs.get("method", "POST")
                headers = kwargs.get("headers")
                data = kwargs.get("data")
                files = kwargs.get("files")
                api_res = await self._nodelink.send_request(
                    target_id=target_id,
                    endpoint=endpoint,
                    method=method,
                    headers=headers,
                    data=data,
                    files=files,
                )
                ok = (api_res.status_code < 400)
                err = None
                if not ok:
                    err = (api_res.data.get("error") if isinstance(api_res.data, dict)
                           else f"HTTP {api_res.status_code}")
                return {
                    "status": "success" if ok else "error",
                    "action": "send_request",
                    "target_id": target_id,
                    "status_code": api_res.status_code,
                    "headers": dict(api_res.headers) if api_res.headers else {},
                    "data": api_res.data,
                    "error_message": err,
                }

            elif action == "run_remote":
                target_id = kwargs.get("target_id", "default_remote")
                cmd = kwargs.get("command_or_notebook") or kwargs.get("command") or kwargs.get("script") or ""
                params = kwargs.get("params") or {}
                job_handle = await self._nodelink.run_remote(target_id, cmd, params)
                return {
                    "status": "success",
                    "action": "run_remote",
                    "target_id": job_handle.target_id,
                    "job_id": job_handle.job_id,
                    "job_status": job_handle.status,
                    "started_at": job_handle.started_at.isoformat() if hasattr(job_handle, "started_at") else None,
                    "error_message": None,
                }

            elif action == "fetch_results":
                job_handle_arg = kwargs.get("job_handle")
                target_id = kwargs.get("target_id", "default_remote")
                job_id = kwargs.get("job_id", "")
                download_path = kwargs.get("download_path")

                if isinstance(job_handle_arg, _RemoteJobHandle):
                    jh = job_handle_arg
                elif isinstance(job_handle_arg, dict):
                    jh = _RemoteJobHandle(
                        target_id=job_handle_arg.get("target_id", target_id),
                        job_id=job_handle_arg.get("job_id", job_id),
                        status=job_handle_arg.get("job_status", "SUBMITTED"),
                    )
                else:
                    jh = _RemoteJobHandle(
                        target_id=target_id,
                        job_id=job_id,
                        status="SUBMITTED",
                    )

                result: _JobResult = await self._nodelink.fetch_results(jh, download_path=download_path)
                ok = result.status.upper() in ("COMPLETE", "COMPLETED", "SUCCESS")
                return {
                    "status": "success" if ok else ("error" if result.status.upper() == "ERROR" else "running"),
                    "action": "fetch_results",
                    "job_id": result.job_id,
                    "job_status": result.status,
                    "result_data": result.result_data,
                    "downloaded_path": result.downloaded_path,
                    "error_message": result.error,
                }

            elif action == "execute_and_wait":
                target_id = kwargs.get("target_id", "default_remote")
                config = kwargs.get("config")
                cmd = kwargs.get("command_or_notebook") or kwargs.get("command") or kwargs.get("script") or ""
                params = kwargs.get("params") or {}
                poll_interval = kwargs.get("poll_interval", 0.5)
                timeout = kwargs.get("timeout", 60)

                # Connect if not connected
                if config and target_id not in self._connected_targets:
                    self._nodelink.connect(target_id, config)
                    self._connected_targets.add(target_id)

                # Run remote
                job_handle = await self._nodelink.run_remote(target_id, cmd, params)

                # Poll results
                elapsed = 0.0
                while elapsed < timeout:
                    res = await self._nodelink.fetch_results(job_handle)
                    status_upper = res.status.upper()
                    if status_upper in ("COMPLETE", "COMPLETED", "SUCCESS"):
                        return {
                            "status": "success",
                            "action": "execute_and_wait",
                            "target_id": target_id,
                            "job_id": res.job_id,
                            "job_status": res.status,
                            "result_data": res.result_data,
                            "downloaded_path": res.downloaded_path,
                            "error_message": None,
                        }
                    elif status_upper == "ERROR":
                        return {
                            "status": "error",
                            "action": "execute_and_wait",
                            "target_id": target_id,
                            "job_id": res.job_id,
                            "job_status": res.status,
                            "result_data": res.result_data,
                            "error_message": res.error or "Remote job execution failed",
                        }
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                return {
                    "status": "error",
                    "action": "execute_and_wait",
                    "target_id": target_id,
                    "job_id": job_handle.job_id,
                    "job_status": "TIMEOUT",
                    "result_data": None,
                    "error_message": f"Remote execution timed out after {timeout}s",
                }

            else:
                return {
                    "status": "error",
                    "action": action,
                    "error_message": f"Unsupported action: {action}",
                }

        except Exception as exc:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            err_str = f"{type(exc).__name__}: {exc}"
            log.warn("NodeLinkBridge action failed", action=action, error=err_str, duration_ms=duration_ms)
            return {
                "status": "error",
                "action": action,
                "error_message": err_str,
                "traceback": traceback.format_exc(),
                "duration_ms": duration_ms,
            }

    # ── BridgeResult conversion ───────────────────────────────────────────────

    async def call_as_bridge_result(
        self,
        action: str,
        **kwargs: Any,
    ) -> Any:
        """
        Async dispatcher returning a BridgeResult instead of a raw dict.
        """
        t0 = time.monotonic()
        resp = await self.call(action, **kwargs)
        ms = round((time.monotonic() - t0) * 1000, 1)
        return _resp_to_bridge_result(resp, latency_ms=ms)


# ─── Helper: NodeLink response → BridgeResult ─────────────────────────────────

def _resp_to_bridge_result(resp: dict, latency_ms: float = 0.0) -> Any:
    """Convert a NodeLink response dict to a LegacyBridge BridgeResult."""
    from legacynode.bridge_adapter import BridgeResult  # lazy import

    ok = resp.get("status") == "success"
    return BridgeResult(
        ok=ok,
        data=resp,
        error=resp.get("error_message") if not ok else None,
        traceback=resp.get("traceback") if not ok else None,
        latency_ms=latency_ms,
    )
