"""
Real Local Integration Verification — Stage 2
LegacyBridge ↔ NodeInsightBridge ↔ NodeInsight/FileReaderTool
==============================================================

This test suite verifies the REAL local integration path using actual modules
with no mocking of NodeInsight, NodeInsightBridge, or BridgeAdapter.

Covers requirements not yet addressed by test_integration_nodeinsight.py:

  1. search_symbols through BridgeAdapter (not just NodeInsightBridge)
  2. Explicit sequential multi-call resilience through the SAME adapter instance
  3. BridgeAdapter._log_lines populated by reader_call()
  4. Full ToolResponse data contract field verification (every field)
  5. Path traversal blocked across multiple action types (not just read_file)
  6. Data integrity: NodeInsight payload arrives intact through the bridge chain

Run from the project root (CloudCode/):
    pytest test_real_local_integration.py -v --tb=short

No Cloudflare, no Ollama, no mocks. Real modules, temp workspace.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_PROJECT_ROOT   = Path(__file__).parent.resolve()
_LEGACYBRIDGE   = _PROJECT_ROOT / "LegacyBridge"
_LEGACYNODE_PKG = _LEGACYBRIDGE / "legacynode"
_NODEINSIGHT    = _PROJECT_ROOT / "NodeInsight"

for _p in [str(_LEGACYBRIDGE), str(_LEGACYNODE_PKG), str(_NODEINSIGHT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from legacynode.bridge_adapter import BridgeAdapter, BridgeMode, BridgeResult
from legacynode.tools.node_insight_bridge import NodeInsightBridge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_MODULE_CODE = '''\
"""Sample module for integration verification."""

import os
import sys
from typing import List, Optional

MAX_ITEMS = 100


class Analyser:
    """Analyses a list of items."""

    def __init__(self, name: str, limit: int = MAX_ITEMS) -> None:
        """Initialise with a name and item limit."""
        self.name = name
        self.limit = limit

    def run(self, items: List[str]) -> dict:
        """Run analysis and return a summary dict."""
        return {"name": self.name, "count": len(items)}

    async def run_async(self, items: List[str]) -> dict:
        """Async variant of run()."""
        return self.run(items)


class Reporter:
    """Formats analysis results."""

    def format(self, result: dict) -> str:
        """Return a human-readable string from result."""
        return f"{result['name']}: {result['count']} items"


def build_analyser(name: str, limit: Optional[int] = None) -> Analyser:
    """Factory function that returns a configured Analyser."""
    return Analyser(name=name, limit=limit or MAX_ITEMS)


def process_batch(items: List[str]) -> List[dict]:
    """Process a batch of items and return results."""
    analyser = build_analyser("batch")
    return [analyser.run(items)]
'''

HELPER_MODULE_CODE = '''\
"""Helper utilities."""

from typing import Any


def flatten(nested: list) -> list:
    """Recursively flatten a nested list."""
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value between lo and hi."""
    return max(lo, min(hi, value))
'''


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """
    Build a representative temporary workspace:

    workspace/
    ├── src/
    │   ├── analyser.py      (2 classes, 2 functions, imports)
    │   └── helpers.py       (2 functions, imports)
    ├── config/
    │   ├── settings.json    (structured JSON)
    │   └── rules.yaml       (structured YAML)
    ├── docs/
    │   └── overview.md      (Markdown with headings)
    ├── corrupt.bin          (binary with null bytes)
    └── bad_syntax.py        (Python with syntax error)
    """
    root = tmp_path / "workspace"
    root.mkdir()

    src = root / "src"
    src.mkdir()
    (src / "analyser.py").write_text(SAMPLE_MODULE_CODE, encoding="utf-8")
    (src / "helpers.py").write_text(HELPER_MODULE_CODE, encoding="utf-8")

    cfg = root / "config"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps({"app": "verifier", "version": "2.0", "debug": False}, indent=2),
        encoding="utf-8",
    )
    (cfg / "rules.yaml").write_text(
        "max_depth: 10\ntimeout: 30\nretries: 3\n",
        encoding="utf-8",
    )

    docs = root / "docs"
    docs.mkdir()
    (docs / "overview.md").write_text(
        "# Project Overview\n\nThis is the main documentation.\n\n"
        "## Architecture\n\nThe system is divided into components.\n\n"
        "## Security\n\nAll paths are sandboxed.\n",
        encoding="utf-8",
    )

    # Binary file — triggers BinaryFile error
    (root / "corrupt.bin").write_bytes(
        b"\x00\xff\xfe\x00\x01\x02\x03\x00\x00BinaryPayload\x00"
    )

    # Syntax error file — triggers ParseError from AST
    (root / "bad_syntax.py").write_text(
        "def broken(:\n    return 42\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture()
def adapter(workspace: Path) -> BridgeAdapter:
    """Single BridgeAdapter instance in LOCAL mode — used across sequential tests."""
    return BridgeAdapter(mode=BridgeMode.LOCAL, workspace_root=str(workspace))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ok(result: BridgeResult, label: str = "") -> dict:
    """Assert success and return the inner data payload dict."""
    assert result.ok, (
        f"[{label}] Expected ok=True\n"
        f"  error     = {result.error!r}\n"
        f"  traceback = {result.traceback!r}"
    )
    assert result.data is not None, f"[{label}] data is None on success"
    assert result.data["status"] == "success", f"[{label}] inner status != 'success'"
    assert result.data["error_message"] is None, f"[{label}] error_message not None on success"
    return result.data["data"]


def err(result: BridgeResult, code: str, label: str = "") -> None:
    """Assert error with expected code prefix and correct structure."""
    assert not result.ok, f"[{label}] Expected ok=False but got success"
    assert result.error is not None, f"[{label}] error is None on failure"
    assert result.error.startswith(code), (
        f"[{label}] Expected error starting with {code!r}, got: {result.error!r}"
    )
    # Inner ToolResponse structure must still be present and correct
    assert result.data is not None, f"[{label}] data is None on error"
    assert result.data["status"] == "error", f"[{label}] inner status != 'error'"
    assert result.data["data"] is None, f"[{label}] inner data not None on error"
    assert result.data["error_message"] is not None, f"[{label}] inner error_message is None"


# ===========================================================================
# 1. REAL READER OPERATIONS through BridgeAdapter
# ===========================================================================

class TestRealReaderOperations:
    """
    All four required reader operations tested through BridgeAdapter → NodeInsightBridge
    → FileReaderTool with no mocking.
    """

    def test_read_file_content_intact(self, adapter: BridgeAdapter) -> None:
        """
        read_file: file content arrives at caller unchanged.
        Verifies the full chain: BridgeAdapter → NodeInsightBridge → FileReaderTool → caller.
        """
        result = adapter.reader_call("read_file", path="src/analyser.py")
        data = ok(result, "read_file")

        # Content integrity — check actual source text
        assert "class Analyser:" in data["content"]
        assert "class Reporter:" in data["content"]
        assert "def build_analyser" in data["content"]
        assert "def process_batch" in data["content"]
        assert "import os" in data["content"]

        # Metadata integrity
        assert data["path"] == "src/analyser.py"
        assert data["total_lines"] > 0
        assert data["start_line"] == 1
        assert data["end_line"] == data["total_lines"]
        assert data["size_bytes"] > 0
        assert isinstance(data["encoding"], str)

    def test_read_file_line_range_preserved(self, adapter: BridgeAdapter) -> None:
        """
        read_file with line range: only the requested lines returned, metadata correct.
        """
        result = adapter.reader_call(
            "read_file", path="src/helpers.py", start_line=1, end_line=5, line_numbers=True
        )
        data = ok(result, "read_file line range")

        assert data["start_line"] == 1
        assert data["end_line"] == 5
        lines = data["content"].splitlines()
        assert lines[0].startswith("1: "), f"First line should be prefixed '1: ', got: {lines[0]!r}"

    def test_parse_ast_symbols_intact(self, adapter: BridgeAdapter) -> None:
        """
        parse_ast: classes, methods, functions, imports all reach caller intact.
        Simulates: 'Inspect functions in analyser.py'
        """
        result = adapter.reader_call("parse_ast", path="src/analyser.py")
        data = ok(result, "parse_ast")

        # Classes
        class_map = {c["name"]: c for c in data["classes"]}
        assert "Analyser" in class_map, f"Analyser not found. Classes: {list(class_map)}"
        assert "Reporter" in class_map, f"Reporter not found. Classes: {list(class_map)}"

        # Methods inside Analyser
        analyser_methods = {m["name"]: m for m in class_map["Analyser"]["methods"]}
        assert "__init__" in analyser_methods
        assert "run" in analyser_methods
        assert "run_async" in analyser_methods
        assert analyser_methods["run_async"]["is_async"] is True
        assert analyser_methods["run"]["is_async"] is False

        # Method signatures travel intact
        init_sig = analyser_methods["__init__"]["signature"]
        assert "name: str" in init_sig
        assert "limit: int" in init_sig

        # Top-level functions
        fn_map = {f["name"]: f for f in data["functions"]}
        assert "build_analyser" in fn_map
        assert "process_batch" in fn_map

        # Import graph
        assert "os" in data["import_graph"]
        assert "sys" in data["import_graph"]
        assert "typing" in data["import_graph"]

        # Counts
        assert data["total_classes"] == 2
        assert data["total_functions"] == 2

    def test_search_symbols_through_bridge_adapter(self, adapter: BridgeAdapter) -> None:
        """
        search_symbols through BridgeAdapter: symbol search across workspace Python files.
        This is the operation not yet tested at the BridgeAdapter level.
        """
        # Search for the Analyser class
        result = adapter.reader_call("search_symbols", query="Analyser", path=".")
        data = ok(result, "search_symbols Analyser")

        matches = data["matches"]
        class_matches = [m for m in matches if m["symbol_type"] == "class"]
        assert any(
            m["name"] == "Analyser" and "analyser.py" in m["path"]
            for m in class_matches
        ), f"Analyser class not found in: {class_matches}"

        # Search for a specific method
        result2 = adapter.reader_call(
            "search_symbols", query="run_async", path="src", symbol_type="method"
        )
        data2 = ok(result2, "search_symbols run_async")
        method_names = {m["name"] for m in data2["matches"]}
        assert "run_async" in method_names

        # Search for a function in helpers
        result3 = adapter.reader_call(
            "search_symbols", query="flatten", path="src", symbol_type="function"
        )
        data3 = ok(result3, "search_symbols flatten")
        assert any(m["name"] == "flatten" for m in data3["matches"])

        # Non-existent symbol returns empty list, not error
        result4 = adapter.reader_call("search_symbols", query="XYZ_NONEXISTENT_999", path=".")
        data4 = ok(result4, "search_symbols nonexistent")
        assert data4["total_matches"] == 0

    def test_read_structured_json(self, adapter: BridgeAdapter) -> None:
        """
        read_structured (JSON): parsed data arrives intact through the bridge.
        """
        result = adapter.reader_call("read_structured", path="config/settings.json")
        data = ok(result, "read_structured json")

        assert data["format"] == "json"
        parsed = data["data"]
        assert parsed["app"] == "verifier"
        assert parsed["version"] == "2.0"
        assert parsed["debug"] is False

    def test_read_structured_yaml(self, adapter: BridgeAdapter) -> None:
        """
        read_structured (YAML): YAML file parsed and returned intact.
        """
        result = adapter.reader_call("read_structured", path="config/rules.yaml")
        data = ok(result, "read_structured yaml")

        assert data["format"] == "yaml"
        assert "max_depth" in data["data"] or "timeout" in data["data"]

    def test_read_structured_markdown(self, adapter: BridgeAdapter) -> None:
        """
        read_structured (Markdown): sections extracted correctly.
        """
        result = adapter.reader_call("read_structured", path="docs/overview.md")
        data = ok(result, "read_structured markdown")

        assert data["format"] == "markdown"
        headings = [s["heading"] for s in data["data"]["sections"] if s["heading"]]
        assert any("Overview" in h for h in headings)
        assert any("Architecture" in h for h in headings)


# ===========================================================================
# 2. EXPLICIT SEQUENTIAL MULTI-CALL RESILIENCE
#    Same BridgeAdapter instance: read_file → parse_ast → error → search_symbols
# ===========================================================================

class TestSequentialMultiCall:
    """
    Verifies that a SINGLE BridgeAdapter instance remains operational
    across multiple calls including failures in the middle.
    """

    def test_sequential_read_parse_error_symbols(
        self, adapter: BridgeAdapter, workspace: Path
    ) -> None:
        """
        Explicit sequence through the SAME adapter instance:
          1. read_file      → success
          2. parse_ast      → success
          3. read_file      → error (missing file)
          4. search_symbols → success (bridge must still work)

        Confirms the bridge does not enter a broken state after an error.
        """
        # Call 1: read_file — success
        r1 = adapter.reader_call("read_file", path="src/analyser.py")
        data1 = ok(r1, "call-1 read_file")
        assert "class Analyser" in data1["content"]

        # Call 2: parse_ast — success
        r2 = adapter.reader_call("parse_ast", path="src/helpers.py")
        data2 = ok(r2, "call-2 parse_ast")
        fn_names = {f["name"] for f in data2["functions"]}
        assert "flatten" in fn_names
        assert "clamp" in fn_names

        # Call 3: missing file — error
        r3 = adapter.reader_call("read_file", path="src/does_not_exist.py")
        err(r3, "FileNotFound", "call-3 missing file")

        # Call 4: search_symbols — success (bridge must still function)
        r4 = adapter.reader_call("search_symbols", query="Reporter", path=".")
        data4 = ok(r4, "call-4 search_symbols post-error")
        assert any(m["name"] == "Reporter" for m in data4["matches"])

    def test_sequential_error_does_not_corrupt_state(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        Multiple consecutive errors must not corrupt the bridge.
        After three different errors, a success must still work.
        """
        # Error 1: path traversal
        e1 = adapter.reader_call("read_file", path="../../etc/passwd")
        err(e1, "PathTraversal", "err-1 path traversal")

        # Error 2: binary file
        e2 = adapter.reader_call("read_file", path="corrupt.bin")
        err(e2, "BinaryFile", "err-2 binary file")

        # Error 3: invalid action
        e3 = adapter.reader_call("wipe_everything")
        err(e3, "InvalidAction", "err-3 invalid action")

        # Recovery: a legitimate call after three errors must succeed
        r = adapter.reader_call("list_dir", path="src")
        data = ok(r, "recovery list_dir after 3 errors")
        entry_names = {e["name"] for e in data["entries"]}
        assert "analyser.py" in entry_names
        assert "helpers.py" in entry_names

    def test_latency_recorded_on_every_call(self, adapter: BridgeAdapter) -> None:
        """
        Every BridgeResult (success or error) must carry a non-negative latency_ms.
        """
        calls = [
            adapter.reader_call("read_file", path="src/analyser.py"),
            adapter.reader_call("read_file", path="nonexistent.py"),
            adapter.reader_call("parse_ast", path="src/helpers.py"),
        ]
        for i, result in enumerate(calls):
            assert isinstance(result.latency_ms, float), (
                f"Call {i}: latency_ms is not float: {type(result.latency_ms)}"
            )
            assert result.latency_ms >= 0, (
                f"Call {i}: latency_ms is negative: {result.latency_ms}"
            )

    def test_adapter_log_lines_populated_by_reader_calls(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        BridgeAdapter._log_lines must be updated by reader_call() so that
        callers can trace what happened via get_live_output().
        """
        initial_count = len(adapter._log_lines)

        adapter.reader_call("read_file", path="src/analyser.py")
        adapter.reader_call("read_file", path="src/nonexistent.py")

        assert len(adapter._log_lines) > initial_count, (
            "reader_call() did not add any log lines to adapter._log_lines"
        )

        # get_live_output() must include the reader log entries
        output = adapter.get_live_output()
        assert any("READER" in line or "nodeinsight" in line.lower() for line in output), (
            f"No READER log lines found in get_live_output(). Lines: {output[:5]}"
        )


# ===========================================================================
# 3. ERROR PROPAGATION — all five error types
# ===========================================================================

class TestErrorPropagation:
    """
    Verifies that all NodeInsight error types propagate correctly through
    NodeInsightBridge → BridgeAdapter → caller without crashing the bridge.
    """

    def test_missing_file(self, adapter: BridgeAdapter) -> None:
        """FileNotFound propagated as structured BridgeResult error."""
        result = adapter.reader_call("read_file", path="src/ghost.py")
        err(result, "FileNotFound", "missing file")
        # Adapter operational
        ok(adapter.reader_call("list_dir", path="."), "post-error list_dir")

    def test_invalid_action(self, adapter: BridgeAdapter) -> None:
        """InvalidAction propagated for unknown action names."""
        result = adapter.reader_call("destroy_workspace")
        err(result, "InvalidAction", "invalid action")
        ok(adapter.reader_call("list_dir", path="."), "post-invalid-action list_dir")

    def test_directory_traversal_read_file(self, adapter: BridgeAdapter) -> None:
        """PathTraversal rejected by NodeInsight's _safe_path() for read_file."""
        result = adapter.reader_call("read_file", path="../../etc/passwd")
        err(result, "PathTraversal", "traversal read_file")

    def test_directory_traversal_parse_ast(self, adapter: BridgeAdapter) -> None:
        """PathTraversal rejected for parse_ast too (not only read_file)."""
        result = adapter.reader_call("parse_ast", path="../../../boot.ini")
        err(result, "PathTraversal", "traversal parse_ast")

    def test_directory_traversal_list_dir(self, adapter: BridgeAdapter) -> None:
        """PathTraversal rejected for list_dir."""
        result = adapter.reader_call("list_dir", path="../../../")
        err(result, "PathTraversal", "traversal list_dir")

    def test_directory_traversal_search_in_file(self, adapter: BridgeAdapter) -> None:
        """PathTraversal rejected for search_in_file."""
        result = adapter.reader_call(
            "search_in_file", path="../../secret.txt", pattern="password"
        )
        err(result, "PathTraversal", "traversal search_in_file")

    def test_syntax_error(self, adapter: BridgeAdapter) -> None:
        """ParseError propagated for Python file with syntax error."""
        result = adapter.reader_call("parse_ast", path="bad_syntax.py")
        err(result, "ParseError", "syntax error")
        ok(adapter.reader_call("read_file", path="src/analyser.py"), "post-syntax-error read")

    def test_binary_file(self, adapter: BridgeAdapter) -> None:
        """BinaryFile propagated for files with null bytes."""
        result = adapter.reader_call("read_file", path="corrupt.bin")
        err(result, "BinaryFile", "binary file")
        ok(adapter.reader_call("list_dir", path="."), "post-binary list_dir")

    def test_directory_as_file(self, adapter: BridgeAdapter) -> None:
        """IsADirectory propagated when read_file targets a directory."""
        result = adapter.reader_call("read_file", path="src")
        err(result, "IsADirectory", "dir as file")


# ===========================================================================
# 4. DATA CONTRACT VERIFICATION
# ===========================================================================

class TestDataContract:
    """
    Verifies that the NodeInsight ToolResponse dict travels through
    the bridge chain completely intact and that BridgeResult exposes
    all required fields correctly.
    """

    def test_tool_response_envelope_fields_on_success(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        Every successful BridgeResult.data must contain exactly:
          status, data, error_message
        with correct types and values.
        """
        result = adapter.reader_call("read_file", path="src/analyser.py")
        assert result.ok is True

        tool_resp = result.data
        # Envelope keys
        assert "status" in tool_resp
        assert "data" in tool_resp
        assert "error_message" in tool_resp
        # Values
        assert tool_resp["status"] == "success"
        assert isinstance(tool_resp["data"], dict)
        assert tool_resp["error_message"] is None

    def test_tool_response_envelope_fields_on_error(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        Every failed BridgeResult.data must contain:
          status="error", data=null, error_message="<Code>: <detail>"
        """
        result = adapter.reader_call("read_file", path="nonexistent.py")
        assert result.ok is False

        tool_resp = result.data
        assert tool_resp["status"] == "error"
        assert tool_resp["data"] is None
        assert tool_resp["error_message"] is not None
        assert "FileNotFound" in tool_resp["error_message"]

    def test_bridge_result_fields_on_success(self, adapter: BridgeAdapter) -> None:
        """
        BridgeResult must expose ok, data, error, latency_ms, timestamp
        with the correct types.
        """
        result = adapter.reader_call("list_dir", path=".")
        assert isinstance(result.ok, bool)
        assert result.ok is True
        assert isinstance(result.data, dict)
        assert result.error is None
        assert isinstance(result.latency_ms, float)
        assert result.latency_ms >= 0
        assert isinstance(result.timestamp, str)
        assert len(result.timestamp) > 0

    def test_bridge_result_fields_on_error(self, adapter: BridgeAdapter) -> None:
        """
        BridgeResult on error must have ok=False, error string set,
        data still present (contains ToolResponse), latency_ms recorded.
        """
        result = adapter.reader_call("read_file", path="nonexistent.py")
        assert isinstance(result.ok, bool)
        assert result.ok is False
        assert isinstance(result.error, str)
        assert len(result.error) > 0
        assert isinstance(result.data, dict)      # ToolResponse dict still present
        assert isinstance(result.latency_ms, float)
        assert result.latency_ms >= 0

    def test_ast_result_fields_complete(self, adapter: BridgeAdapter) -> None:
        """
        parse_ast payload must contain all ASTResult fields:
        path, classes, functions, imports, import_graph,
        total_classes, total_functions.
        """
        result = adapter.reader_call("parse_ast", path="src/analyser.py")
        data = ok(result, "ast fields")

        required = {
            "path", "classes", "functions", "imports",
            "import_graph", "total_classes", "total_functions",
        }
        missing = required - set(data.keys())
        assert not missing, f"ASTResult missing fields: {missing}"

        assert isinstance(data["classes"], list)
        assert isinstance(data["functions"], list)
        assert isinstance(data["imports"], list)
        assert isinstance(data["import_graph"], list)
        assert isinstance(data["total_classes"], int)
        assert isinstance(data["total_functions"], int)

    def test_read_file_result_fields_complete(self, adapter: BridgeAdapter) -> None:
        """
        read_file payload must contain all ReadFileResult fields:
        path, content, start_line, end_line, total_lines, encoding, size_bytes.
        """
        result = adapter.reader_call("read_file", path="src/helpers.py")
        data = ok(result, "read file fields")

        required = {
            "path", "content", "start_line", "end_line",
            "total_lines", "encoding", "size_bytes",
        }
        missing = required - set(data.keys())
        assert not missing, f"ReadFileResult missing fields: {missing}"

        assert isinstance(data["content"], str)
        assert isinstance(data["start_line"], int)
        assert isinstance(data["end_line"], int)
        assert isinstance(data["total_lines"], int)
        assert isinstance(data["size_bytes"], int)
        assert data["size_bytes"] > 0

    def test_tool_response_is_json_serializable(self, adapter: BridgeAdapter) -> None:
        """
        The full BridgeResult.data (ToolResponse dict) must be JSON-serializable.
        This confirms the data contract is wire-compatible.
        """
        for action, kwargs in [
            ("read_file", {"path": "src/analyser.py"}),
            ("parse_ast", {"path": "src/helpers.py"}),
            ("list_dir",  {"path": "."}),
            ("read_file", {"path": "nonexistent.py"}),   # error case
        ]:
            result = adapter.reader_call(action, **kwargs)
            try:
                serialized = json.dumps(result.data)
            except (TypeError, ValueError) as exc:
                pytest.fail(
                    f"BridgeResult.data for '{action}' is not JSON-serializable: {exc}\n"
                    f"data = {result.data!r}"
                )
            # Round-trip sanity check
            restored = json.loads(serialized)
            assert restored["status"] == result.data["status"]


# ===========================================================================
# 5. SECURITY — path traversal blocked across ALL relevant actions
# ===========================================================================

class TestSecurity:
    """
    Verifies NodeInsight's _safe_path() sandbox is intact across all
    actions that accept a path argument.  No new security mechanism
    is introduced — only existing NodeInsight guards are verified.
    """

    TRAVERSAL_PATHS = [
        "../../etc/passwd",
        "../../../windows/system32/drivers/etc/hosts",
        "src/../../outside.txt",
        ".." + "/" * 3 + "secret",
    ]

    def test_read_file_traversal_all_paths(self, adapter: BridgeAdapter) -> None:
        """All traversal patterns are rejected for read_file."""
        for path in self.TRAVERSAL_PATHS:
            result = adapter.reader_call("read_file", path=path)
            assert not result.ok, f"Traversal allowed for read_file: {path!r}"
            assert result.error.startswith("PathTraversal"), (
                f"Wrong error for {path!r}: {result.error!r}"
            )

    def test_parse_ast_traversal(self, adapter: BridgeAdapter) -> None:
        """Traversal rejected for parse_ast."""
        result = adapter.reader_call("parse_ast", path="../../etc/shadow")
        err(result, "PathTraversal", "parse_ast traversal")

    def test_list_dir_traversal(self, adapter: BridgeAdapter) -> None:
        """Traversal rejected for list_dir."""
        result = adapter.reader_call("list_dir", path="../../../")
        err(result, "PathTraversal", "list_dir traversal")

    def test_search_in_file_traversal(self, adapter: BridgeAdapter) -> None:
        """Traversal rejected for search_in_file."""
        result = adapter.reader_call(
            "search_in_file", path="../../boot.ini", pattern="password"
        )
        err(result, "PathTraversal", "search_in_file traversal")

    def test_read_file_chunked_traversal(self, adapter: BridgeAdapter) -> None:
        """Traversal rejected for read_file_chunked."""
        result = adapter.reader_call(
            "read_file_chunked", path="../../../windows/win.ini"
        )
        err(result, "PathTraversal", "read_file_chunked traversal")

    def test_traversal_blocked_then_legitimate_call_works(
        self, adapter: BridgeAdapter
    ) -> None:
        """
        After a traversal rejection, a legitimate call must succeed.
        The bridge must not enter a locked or broken state.
        """
        # Attempt traversal
        bad = adapter.reader_call("read_file", path="../../etc/passwd")
        err(bad, "PathTraversal", "traversal before legitimate call")

        # Legitimate call must still work
        good = adapter.reader_call("read_file", path="src/analyser.py")
        data = ok(good, "legitimate call after traversal")
        assert "class Analyser" in data["content"]

    def test_nodeinsight_sandbox_not_bypassed(
        self, adapter: BridgeAdapter, workspace: Path
    ) -> None:
        """
        Confirm that files genuinely outside the workspace cannot be read.
        The bridge does NOT add any path resolution — it delegates entirely
        to NodeInsight's _safe_path() which uses Path.resolve() + relative_to().
        """
        # Place a file one level above the workspace
        outside = workspace.parent / "outside_secret.txt"
        outside.write_text("TOP SECRET", encoding="utf-8")

        try:
            result = adapter.reader_call("read_file", path="../outside_secret.txt")
            assert not result.ok, (
                "File outside workspace was readable — sandbox is broken!"
            )
            assert result.error.startswith("PathTraversal"), (
                f"Expected PathTraversal, got: {result.error!r}"
            )
        finally:
            outside.unlink(missing_ok=True)
