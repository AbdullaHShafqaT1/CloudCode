"""
LegacyBridge — NodeInsight Bridge Adapter (node_insight_bridge.py)
===================================================================

Thin adapter connecting LegacyBridge to NodeInsight's FileReaderTool
dispatcher.  Provides both an async interface (for use inside coroutines)
and a synchronous interface (for use from Streamlit/BridgeAdapter callbacks).

Responsibilities
----------------
* Accept a ``workspace_root`` and forward ``call(action, **kwargs)`` to
  NodeInsight's ``FileReaderTool`` dispatcher.
* Translate the NodeInsight ``ToolResponse`` dict into a ``BridgeResult``
  when callers need the unified LegacyBridge envelope.
* Perform **zero** path resolution — all sandbox and path-traversal
  security checks remain exclusively inside NodeInsight.
* Never import cloud, AutoGen, or LLM dependencies.

Data Contract
-------------
Request (caller → bridge → NodeInsight):

    action : str
        One of: list_dir, search_files, read_file, read_file_chunked,
        search_in_file, parse_ast, search_symbols, read_structured.
    **kwargs
        Action-specific keyword arguments forwarded verbatim.

Response (NodeInsight → bridge → caller) — NodeInsight ToolResponse dict:

    {
        "status":        "success" | "error",
        "data":          <payload dict> | null,
        "error_message": "<ErrorCode: detail>" | null
    }

When wrapped in a BridgeResult (via ``to_bridge_result``):

    BridgeResult(
        ok          = True | False,
        data        = <ToolResponse dict>,   # contains status/data/error_message
        error       = "<ErrorCode: detail>" | None,
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
# Make NodeInsight importable when running from the LegacyBridge directory.
# Priority order:
#   1. The NodeInsight package root (../../../NodeInsight relative to this file)
#   2. The LegacyBridge package root (grandparent of this file)
_THIS = Path(__file__).resolve()
_LEGACYBRIDGE_ROOT = _THIS.parent.parent.parent          # …/LegacyBridge
_NODEINSIGHT_ROOT  = _LEGACYBRIDGE_ROOT.parent / "NodeInsight"

for _p in [str(_NODEINSIGHT_ROOT), str(_LEGACYBRIDGE_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ─── NodeInsight Import ───────────────────────────────────────────────────────
try:
    from file_reader_tool import FileReaderTool as _FileReaderTool  # type: ignore
    _NODEINSIGHT_AVAILABLE = True
except ImportError:
    _NODEINSIGHT_AVAILABLE = False
    _FileReaderTool = None  # type: ignore

log = structlog.get_logger(__name__)


# ─── NodeInsightBridge ────────────────────────────────────────────────────────

class NodeInsightBridge:
    """
    Adapter that forwards reader requests from LegacyBridge to NodeInsight.

    Parameters
    ----------
    workspace_root : str
        Absolute or relative path to the workspace root.  Passed verbatim to
        ``FileReaderTool``; all path-traversal checks happen inside NodeInsight.
    max_file_size : int, optional
        Override NodeInsight's default 10 MB file-size limit.

    Raises
    ------
    ImportError
        If NodeInsight's ``file_reader_tool`` package cannot be imported.
        Check that NodeInsight is on ``sys.path`` or call
        ``NodeInsightBridge.is_available()`` before constructing.
    """

    def __init__(
        self,
        workspace_root: str = ".",
        max_file_size: Optional[int] = None,
    ) -> None:
        if not _NODEINSIGHT_AVAILABLE:
            raise ImportError(
                "NodeInsight's 'file_reader_tool' package is not importable. "
                "Ensure the NodeInsight directory is adjacent to LegacyBridge, "
                "or add it to sys.path manually."
            )
        kwargs: dict[str, Any] = {"workspace_root": workspace_root}
        if max_file_size is not None:
            kwargs["max_file_size"] = max_file_size
        self._tool: _FileReaderTool = _FileReaderTool(**kwargs)  # type: ignore
        self._workspace_root = workspace_root
        log.debug(
            "NodeInsightBridge initialised",
            workspace_root=workspace_root,
        )

    # ── Availability check ────────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        """Return True if NodeInsight can be imported successfully."""
        return _NODEINSIGHT_AVAILABLE

    # ── Primary async interface ───────────────────────────────────────────────

    async def call(self, action: str, **kwargs: Any) -> dict:
        """
        Async dispatcher — forwards *action* + kwargs to NodeInsight and
        returns the raw ``ToolResponse`` dict.

        Always returns a dict with keys ``status``, ``data``,
        ``error_message``; never raises.

        Parameters
        ----------
        action : str
            One of the NodeInsight actions (see module docstring).
        **kwargs
            Arguments forwarded to NodeInsight verbatim.

        Returns
        -------
        dict
            NodeInsight ``ToolResponse`` serialised to a plain dict.
        """
        log.debug("NodeInsightBridge.call", action=action, kwargs=list(kwargs))
        return await self._tool.call(action, **kwargs)

    # ── BridgeResult conversion ───────────────────────────────────────────────

    async def call_as_bridge_result(
        self,
        action: str,
        **kwargs: Any,
    ) -> "BridgeResult":  # type: ignore[name-defined]   # imported lazily below
        """
        Async dispatcher that returns a ``BridgeResult`` instead of the raw
        ToolResponse dict.  Convenient for callers that already work with
        ``BridgeResult``.
        """
        t0 = time.monotonic()
        tool_resp = await self.call(action, **kwargs)
        ms = round((time.monotonic() - t0) * 1000, 1)
        return _tool_resp_to_bridge_result(tool_resp, latency_ms=ms)

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def workspace_root(self) -> str:
        return self._workspace_root


# ─── Helper: ToolResponse → BridgeResult ─────────────────────────────────────

def _tool_resp_to_bridge_result(tool_resp: dict, latency_ms: float = 0.0) -> Any:
    """
    Convert a NodeInsight ToolResponse dict to a LegacyBridge BridgeResult.

    Imported lazily to avoid circular imports between bridge_adapter and this
    module at load time.
    """
    from legacynode.bridge_adapter import BridgeResult  # lazy import

    ok = tool_resp.get("status") == "success"
    return BridgeResult(
        ok=ok,
        data=tool_resp,          # full ToolResponse dict preserved in data
        error=tool_resp.get("error_message") if not ok else None,
        latency_ms=latency_ms,
    )
