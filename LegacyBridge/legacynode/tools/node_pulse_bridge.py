"""
LegacyBridge — NodePulse Bridge Adapter (node_pulse_bridge.py)
==============================================================

Thin adapter connecting LegacyBridge to NodePulse's TerminalExecutorTool.
Follows the same pattern as NodeInsightBridge — zero path manipulation,
zero cloud/LLM imports.

Responsibilities
----------------
* Accept ``allowed_project_root`` and forward ``call(action, **kwargs)``
  to NodePulse's ``TerminalExecutorTool``.
* Translate the NodePulse response dict into a ``BridgeResult`` when callers
  need the unified LegacyBridge envelope (via ``call_as_bridge_result``).
* Never import cloud, AutoGen, or LLM dependencies.
* All security checks (forbidden-pattern blocklist, CWD confinement) remain
  exclusively inside NodePulse's ``TerminalExecutorTool``.

Data Contract
-------------
Request (caller → bridge → NodePulse):

    action : str
        Currently only ``"run_command"`` is supported.
    **kwargs
        ``command`` (required), ``cwd``, ``timeout``, ``env`` (optional).

Response (NodePulse → bridge → caller) — TerminalExecutorTool response dict:

    {
        "status":        "success" | "error" | "timeout",
        "action":        "run_command",
        "command":       <str>,
        "cwd":           <str>,
        "exit_code":     <int>,
        "stdout":        <str>,
        "stderr":        <str>,
        "duration_ms":   <int>,
        "timestamp":     <ISO-8601 str>,
        "error_message": <str | None>,
        "truncated":     <bool>
    }

When wrapped in a BridgeResult (via ``call_as_bridge_result``):

    BridgeResult(
        ok          = True  (status == "success"),
        data        = <NodePulse response dict>,
        error       = <error_message> | None,
        latency_ms  = <float>
    )
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Optional

import structlog

# ─── sys.path Bootstrap ───────────────────────────────────────────────────────
# NodePulse lives at  <project_root>/NodePulse/terminal_executor.py
# This file lives at  <project_root>/LegacyBridge/legacynode/tools/node_pulse_bridge.py
# So project_root = Path(__file__).parent.parent.parent.parent
_THIS             = Path(__file__).resolve()
_LEGACYBRIDGE_ROOT = _THIS.parent.parent.parent          # …/LegacyBridge
_NODEPULSE_ROOT   = _LEGACYBRIDGE_ROOT.parent / "NodePulse"

for _p in [str(_NODEPULSE_ROOT), str(_LEGACYBRIDGE_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ─── NodePulse Import ─────────────────────────────────────────────────────────
try:
    from terminal_executor import TerminalExecutorTool as _TerminalExecutorTool  # type: ignore
    _NODEPULSE_AVAILABLE = True
except ImportError:
    _NODEPULSE_AVAILABLE = False
    _TerminalExecutorTool = None  # type: ignore

log = structlog.get_logger(__name__)


# ─── NodePulseBridge ──────────────────────────────────────────────────────────

class NodePulseBridge:
    """
    Adapter that forwards terminal execution requests from LegacyBridge to
    NodePulse's ``TerminalExecutorTool``.

    Parameters
    ----------
    allowed_project_root : str
        Absolute or relative path to the workspace root.  Passed verbatim to
        ``TerminalExecutorTool``; all CWD-confinement and forbidden-pattern
        checks happen inside NodePulse.

    Raises
    ------
    ImportError
        If NodePulse's ``terminal_executor`` module cannot be imported.
        Check that NodePulse is on ``sys.path`` or call
        ``NodePulseBridge.is_available()`` before constructing.
    """

    def __init__(self, allowed_project_root: str = ".") -> None:
        if not _NODEPULSE_AVAILABLE:
            raise ImportError(
                "NodePulse's 'terminal_executor' module is not importable. "
                "Ensure the NodePulse directory is adjacent to LegacyBridge, "
                "or add it to sys.path manually."
            )
        self._tool: _TerminalExecutorTool = _TerminalExecutorTool(  # type: ignore
            allowed_project_root=allowed_project_root
        )
        self._project_root = allowed_project_root
        log.debug(
            "NodePulseBridge initialised",
            allowed_project_root=allowed_project_root,
        )

    # ── Availability check ────────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        """Return True if NodePulse can be imported successfully."""
        return _NODEPULSE_AVAILABLE

    # ── Primary async interface ───────────────────────────────────────────────

    async def call(self, action: str, **kwargs: Any) -> dict:
        """
        Async dispatcher — forwards *action* + kwargs to NodePulse and
        returns the raw response dict.

        Always returns a dict with the full NodePulse schema; never raises.

        Parameters
        ----------
        action : str
            NodePulse action name.  Currently ``"run_command"`` only.
        **kwargs
            Arguments forwarded to NodePulse verbatim.

        Returns
        -------
        dict
            NodePulse ``TerminalExecutorTool`` response dict.
        """
        log.debug("NodePulseBridge.call", action=action, kwargs=list(kwargs))
        return await self._tool.call(action, **kwargs)

    # ── BridgeResult conversion ───────────────────────────────────────────────

    async def call_as_bridge_result(
        self,
        action: str,
        **kwargs: Any,
    ) -> "BridgeResult":  # type: ignore[name-defined]   # imported lazily below
        """
        Async dispatcher that returns a ``BridgeResult`` instead of the raw
        response dict.  Convenient for callers that already work with
        ``BridgeResult``.
        """
        t0 = time.monotonic()
        resp = await self.call(action, **kwargs)
        ms = round((time.monotonic() - t0) * 1000, 1)
        return _resp_to_bridge_result(resp, latency_ms=ms)

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def project_root(self) -> str:
        return self._project_root


# ─── Helper: NodePulse response → BridgeResult ───────────────────────────────

def _resp_to_bridge_result(resp: dict, latency_ms: float = 0.0) -> Any:
    """
    Convert a NodePulse response dict to a LegacyBridge BridgeResult.

    Imported lazily to avoid circular imports at load time.
    """
    from legacynode.bridge_adapter import BridgeResult  # lazy import

    ok = resp.get("status") == "success"
    return BridgeResult(
        ok=ok,
        data=resp,          # full response dict preserved in data
        error=resp.get("error_message") if not ok else None,
        latency_ms=latency_ms,
    )
