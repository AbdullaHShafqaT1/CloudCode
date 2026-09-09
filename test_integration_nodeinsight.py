"""
Integration Smoke Test — LegacyBridge ↔ NodeInsight (Reader)
=============================================================

Verifies the full local integration path:

    BridgeAdapter.reader_call(action, **kwargs)
        → NodeInsightBridge.call(action, **kwargs)
            → FileReaderTool.call(action, **kwargs)
                → ToolResponse dict
        → BridgeResult

All tests use a temporary workspace created by pytest's ``tmp_path``
fixture, so no real project files are required.

Run with:
    pytest test_integration_nodeinsight.py -v --tb=short

From the project root (CloudCode/).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
# Ensure both LegacyBridge (for imports) and NodeInsight (for FileReaderTool)
# are importable from the project root.
_PROJECT_ROOT   = Path(__file__).parent.resolve()
_LEGACYBRIDGE   = _PROJECT_ROOT / "LegacyBridge"
_LEGACYNODE_PKG = _LEGACYBRIDGE / "legacynode"
_NODEINSIGHT    = _PROJECT_ROOT / "NodeInsight"

for _p in [str(_LEGACYBRIDGE), str(_LEGACYNODE_PKG), str(_NODEINSIGHT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult  # noqa: E402
from legacynode.tools.node_insight_bridge import NodeInsightBridge              # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_workspace(tmp_path: Path) -> Path:
    """
    Create a minimal temporary workspace containing:

    workspace/
    ├── src/
    │   └── sample_module.py   ← Python file with classes & functions
    ├── data/
    │   └── config.json        ← Structured JSON file
    └── README.md              ← Markdown file
    """
    ws = tmp_path / "workspace"
    ws.mkdir()

    src = ws / "src"
    src.mkdir()

    # A Python module with a class and two functions (used for AST tests)
    (src / "sample_module.py").write_text(
        '"""Sample module for integration testing."""\n'
        "\n"
        "import os\n"
        "from typing import List\n"
        "\n"
        "\n"
        "class Greeter:\n"
        '    """Greets users by name."""\n'
        "\n"
        "    def __init__(self, prefix: str = 'Hello') -> None:\n"
        "        self.prefix = prefix\n"
        "\n"
        "    def greet(self, name: str) -> str:\n"
        '        """Return a greeting string."""\n'
        "        return f'{self.prefix}, {name}!'\n"
        "\n"
        "\n"
        "def inspect_functions(module_path: str) -> List[str]:\n"
        '    """Return names of functions found at module_path."""\n'
        "    return []\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        '    """Entry point."""\n'
        "    g = Greeter()\n"
        "    print(g.greet('World'))\n",
        encoding="utf-8",
    )

    data = ws / "data"
    data.mkdir()
    (data / "config.json").write_text(
        json.dumps({"project": "integration-test", "version": "0.1.0"}, indent=2),
        encoding="utf-8",
    )

    (ws / "README.md").write_text(
        "# Integration Test Workspace\n\nTemporary workspace for smoke testing.\n",
        encoding="utf-8",
    )

    return ws


@pytest.fixture()
def adapter_local(sample_workspace: Path) -> BridgeAdapter:
    """BridgeAdapter in LOCAL mode pointing at the sample workspace."""
    return BridgeAdapter(mode=BridgeMode.LOCAL, workspace_root=str(sample_workspace))


@pytest.fixture()
def adapter_mock(sample_workspace: Path) -> BridgeAdapter:
    """BridgeAdapter in MOCK mode (does not call real NodeInsight)."""
    return BridgeAdapter(mode=BridgeMode.MOCK, workspace_root=str(sample_workspace))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert_bridge_ok(result: BridgeResult, context: str = "") -> None:
    """Assert that a BridgeResult is successful and print diagnostics on failure."""
    assert isinstance(result, BridgeResult), f"{context}: result is not BridgeResult"
    assert result.ok, (
        f"{context}: BridgeResult.ok is False\n"
        f"  error     = {result.error!r}\n"
        f"  traceback = {result.traceback!r}"
    )
    assert result.data is not None, f"{context}: BridgeResult.data is None on success"


def _assert_bridge_error(result: BridgeResult, expected_code: str, context: str = "") -> None:
    """Assert that a BridgeResult carries a structured error with the expected code prefix."""
    assert isinstance(result, BridgeResult), f"{context}: result is not BridgeResult"
    assert not result.ok, f"{context}: BridgeResult.ok is True but expected error"
    assert result.error is not None, f"{context}: BridgeResult.error is None"
    assert result.error.startswith(expected_code), (
        f"{context}: expected error starting with {expected_code!r}, got: {result.error!r}"
    )


# ---------------------------------------------------------------------------
# 1. NodeInsightBridge — direct adapter tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_node_insight_bridge_is_available() -> None:
    """NodeInsightBridge.is_available() returns True when NodeInsight is on sys.path."""
    assert NodeInsightBridge.is_available(), (
        "NodeInsight is not importable. Check that NodeInsight/ is adjacent to LegacyBridge/."
    )


@pytest.mark.asyncio
async def test_node_insight_bridge_read_file(sample_workspace: Path) -> None:
    """
    NodeInsightBridge.call('read_file') returns the raw ToolResponse dict.
    Proves the direct adapter layer before involving BridgeAdapter.
    """
    bridge = NodeInsightBridge(workspace_root=str(sample_workspace))
    resp = await bridge.call("read_file", path="src/sample_module.py")

    assert resp["status"] == "success", f"Expected success, got: {resp['error_message']}"
    assert resp["error_message"] is None
    data = resp["data"]
    assert "class Greeter" in data["content"]
    assert data["total_lines"] > 0
    assert data["path"] == "src/sample_module.py"


@pytest.mark.asyncio
async def test_node_insight_bridge_parse_ast(sample_workspace: Path) -> None:
    """
    NodeInsightBridge.call('parse_ast') extracts classes and functions from sample_module.py.
    """
    bridge = NodeInsightBridge(workspace_root=str(sample_workspace))
    resp = await bridge.call("parse_ast", path="src/sample_module.py")

    assert resp["status"] == "success", f"AST parse failed: {resp['error_message']}"
    data = resp["data"]

    class_names = {c["name"] for c in data["classes"]}
    assert "Greeter" in class_names

    fn_names = {f["name"] for f in data["functions"]}
    assert "inspect_functions" in fn_names
    assert "main" in fn_names

    # Verify import graph
    assert "os" in data["import_graph"]
    assert "typing" in data["import_graph"]


@pytest.mark.asyncio
async def test_node_insight_bridge_error_missing_file(sample_workspace: Path) -> None:
    """
    NodeInsightBridge propagates FileNotFound as a structured error dict,
    not as a raised exception.
    """
    bridge = NodeInsightBridge(workspace_root=str(sample_workspace))
    resp = await bridge.call("read_file", path="src/does_not_exist.py")

    assert resp["status"] == "error"
    assert resp["data"] is None
    assert resp["error_message"].startswith("FileNotFound")


@pytest.mark.asyncio
async def test_node_insight_bridge_security_path_traversal(sample_workspace: Path) -> None:
    """
    NodeInsight's path-traversal sandbox is intact.
    Malicious paths are rejected with PathTraversal error.
    """
    bridge = NodeInsightBridge(workspace_root=str(sample_workspace))

    for malicious in ["../../etc/passwd", "../../../windows/win.ini", "src/../../secret"]:
        resp = await bridge.call("read_file", path=malicious)
        assert resp["status"] == "error", f"Traversal allowed for: {malicious!r}"
        assert resp["error_message"].startswith("PathTraversal"), (
            f"Expected PathTraversal for {malicious!r}, got: {resp['error_message']}"
        )


# ---------------------------------------------------------------------------
# 2. BridgeAdapter.reader_call() — LOCAL mode (real NodeInsight)
# ---------------------------------------------------------------------------

def test_adapter_reader_call_read_file(adapter_local: BridgeAdapter) -> None:
    """
    BridgeAdapter.reader_call('read_file') → BridgeResult with full file content.
    Full integration path: BridgeAdapter → NodeInsightBridge → FileReaderTool.
    """
    result = adapter_local.reader_call("read_file", path="src/sample_module.py")

    _assert_bridge_ok(result, "read_file")
    tool_resp = result.data
    assert tool_resp["status"] == "success"
    assert "class Greeter" in tool_resp["data"]["content"]
    assert result.latency_ms >= 0


def test_adapter_reader_call_parse_ast(adapter_local: BridgeAdapter) -> None:
    """
    BridgeAdapter.reader_call('parse_ast') extracts AST symbols and returns
    them via BridgeResult.data.

    Simulates: "Inspect functions in sample_module.py"
    """
    result = adapter_local.reader_call("parse_ast", path="src/sample_module.py")

    _assert_bridge_ok(result, "parse_ast")
    data = result.data["data"]
    class_names = {c["name"] for c in data["classes"]}
    fn_names    = {f["name"] for f in data["functions"]}

    assert "Greeter" in class_names
    assert "inspect_functions" in fn_names
    assert "main" in fn_names

    # Method check
    greeter_class = next(c for c in data["classes"] if c["name"] == "Greeter")
    method_names  = {m["name"] for m in greeter_class["methods"]}
    assert "greet" in method_names
    assert "__init__" in method_names


def test_adapter_reader_call_list_dir(adapter_local: BridgeAdapter) -> None:
    """
    BridgeAdapter.reader_call('list_dir') lists the workspace root correctly.
    """
    result = adapter_local.reader_call("list_dir", path=".")

    _assert_bridge_ok(result, "list_dir")
    entry_names = {e["name"] for e in result.data["data"]["entries"]}
    assert "src" in entry_names
    assert "data" in entry_names


def test_adapter_reader_call_search_files(adapter_local: BridgeAdapter) -> None:
    """
    BridgeAdapter.reader_call('search_files') finds Python files.
    """
    result = adapter_local.reader_call("search_files", path=".", query="*.py")

    _assert_bridge_ok(result, "search_files")
    match_names = {m["name"] for m in result.data["data"]["matches"]}
    assert "sample_module.py" in match_names


def test_adapter_reader_call_read_structured(adapter_local: BridgeAdapter) -> None:
    """
    BridgeAdapter.reader_call('read_structured') parses JSON correctly.
    """
    result = adapter_local.reader_call("read_structured", path="data/config.json")

    _assert_bridge_ok(result, "read_structured")
    parsed = result.data["data"]["data"]
    assert parsed["project"] == "integration-test"


def test_adapter_reader_call_search_in_file(adapter_local: BridgeAdapter) -> None:
    """
    BridgeAdapter.reader_call('search_in_file') finds pattern matches.
    """
    result = adapter_local.reader_call(
        "search_in_file", path="src/sample_module.py", pattern="def "
    )
    _assert_bridge_ok(result, "search_in_file")
    assert result.data["data"]["total_matches"] >= 3  # greet, inspect_functions, main


# ---------------------------------------------------------------------------
# 3. Error propagation — LegacyBridge stays operational after NodeInsight errors
# ---------------------------------------------------------------------------

def test_error_missing_file_propagated(adapter_local: BridgeAdapter) -> None:
    """
    Missing file → BridgeResult.ok=False, structured error, adapter stays alive.
    """
    result = adapter_local.reader_call("read_file", path="src/ghost.py")

    _assert_bridge_error(result, "FileNotFound", "missing file")
    # Adapter must still be functional after the error
    second = adapter_local.reader_call("list_dir", path=".")
    _assert_bridge_ok(second, "post-error list_dir")


def test_error_path_traversal_propagated(adapter_local: BridgeAdapter) -> None:
    """
    Path traversal attempt → BridgeResult.ok=False with PathTraversal code.
    NodeInsight's sandbox is intact; LegacyBridge stays operational.
    """
    result = adapter_local.reader_call("read_file", path="../../etc/passwd")

    _assert_bridge_error(result, "PathTraversal", "path traversal")
    # Adapter still works
    second = adapter_local.reader_call("read_file", path="src/sample_module.py")
    _assert_bridge_ok(second, "post-traversal read_file")


def test_error_invalid_action_propagated(adapter_local: BridgeAdapter) -> None:
    """
    Unknown action → BridgeResult.ok=False with InvalidAction code.
    """
    result = adapter_local.reader_call("delete_everything")

    _assert_bridge_error(result, "InvalidAction", "invalid action")


def test_error_ast_syntax_error(adapter_local: BridgeAdapter, sample_workspace: Path) -> None:
    """
    Python file with syntax errors → BridgeResult.ok=False with ParseError.
    """
    bad = sample_workspace / "src" / "broken.py"
    bad.write_text("def malformed(:\n    pass\n", encoding="utf-8")

    result = adapter_local.reader_call("parse_ast", path="src/broken.py")

    _assert_bridge_error(result, "ParseError", "syntax error")


def test_error_binary_file_propagated(adapter_local: BridgeAdapter, sample_workspace: Path) -> None:
    """
    Binary file → BridgeResult.ok=False with BinaryFile code.
    """
    (sample_workspace / "data" / "blob.bin").write_bytes(b"\x00\xff\xfe\x01\x00\x02binary")

    result = adapter_local.reader_call("read_file", path="data/blob.bin")

    _assert_bridge_error(result, "BinaryFile", "binary file")


# ---------------------------------------------------------------------------
# 4. MOCK mode — simulated responses for demo / CI without filesystem
# ---------------------------------------------------------------------------

def test_mock_mode_returns_simulated_response(adapter_mock: BridgeAdapter) -> None:
    """
    MOCK mode returns a simulated BridgeResult.ok=True without real file I/O.
    The response is clearly labelled as simulated.
    """
    result = adapter_mock.reader_call("parse_ast", path="src/sample_module.py")

    assert result.ok is True
    assert result.data is not None
    assert result.data["status"] == "success"
    assert "MOCK" in result.data["data"].get("note", "").upper() or \
           result.data["data"].get("action") == "parse_ast"


# ---------------------------------------------------------------------------
# 5. Remote-mode compatibility — NodeInsight stays local regardless of mode
# ---------------------------------------------------------------------------

def test_remote_mode_reader_still_uses_local_nodeinsight(
    sample_workspace: Path,
) -> None:
    """
    In REMOTE mode, reader_call() still delegates file reading to the local
    NodeInsight instance.  LegacyBridge's remote path is for the LLM/agent
    only — NodeInsight never communicates remotely.
    """
    adapter = BridgeAdapter(
        mode=BridgeMode.REMOTE,
        workspace_root=str(sample_workspace),
        tunnel_url="https://not-a-real-tunnel.example.com",
    )
    # read_file should succeed locally even in REMOTE mode
    result = adapter.reader_call("read_file", path="src/sample_module.py")
    _assert_bridge_ok(result, "remote-mode reader_call")
    assert "class Greeter" in result.data["data"]["content"]


# ---------------------------------------------------------------------------
# 6. BridgeResult structure validation
# ---------------------------------------------------------------------------

def test_bridge_result_structure_on_success(adapter_local: BridgeAdapter) -> None:
    """
    Successful BridgeResult has correct field types and latency > 0.
    """
    result = adapter_local.reader_call("list_dir", path=".")

    assert isinstance(result.ok, bool)
    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.error is None
    assert isinstance(result.latency_ms, float)
    assert result.latency_ms >= 0
    assert isinstance(result.timestamp, str)


def test_bridge_result_structure_on_error(adapter_local: BridgeAdapter) -> None:
    """
    Error BridgeResult has ok=False, structured error string, and null data content.
    """
    result = adapter_local.reader_call("read_file", path="nonexistent.py")

    assert result.ok is False
    assert result.error is not None
    assert isinstance(result.error, str)
    # data still present (contains the ToolResponse with status=error)
    assert result.data is not None
    assert result.data["status"] == "error"
    assert result.data["data"] is None
