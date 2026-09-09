"""
Standalone Verification & Unit Testing for NodeInsight (Reader Module)
======================================================================
Comprehensive test suite testing:
1. File Reading & Ingestion (text, source code, structured JSON/YAML/Markdown)
2. Codebase AST Parsing (classes, methods, signatures, import graphs)
3. Path Resolution & Sandboxing (directory traversal defense)
4. Error Handling (missing files, binary data, syntax errors, size guards)
5. Unified Dispatcher Protocol (tool/function-calling compatibility)

Execution:
    pytest test_node_insight.py -v -s
    python test_node_insight.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# Ensure local repository root is on sys.path
REPO_ROOT = Path(__file__).parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from node_insight import (
    ASTResult,
    ErrorCode,
    FileReaderTool,
    NodeInsight,
    ToolResponse,
)

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_node_insight")


def log_test_step(action: str, payload_input: Any, parsed_return: Any) -> None:
    """Helper to output detailed assertion logs for inputs and parsed outputs."""
    logger.info("=== ACTION: %s ===", action)
    logger.info("  [PAYLOAD INPUT] : %s", json.dumps(payload_input, default=str))
    if isinstance(parsed_return, dict):
        preview = {
            k: (v if not isinstance(v, str) or len(v) < 120 else v[:120] + "... (truncated)")
            for k, v in parsed_return.items()
        }
        logger.info("  [PARSED RETURN]: %s", json.dumps(preview, default=str))
    else:
        logger.info("  [PARSED RETURN]: %s", str(parsed_return)[:200])


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def dummy_project(tmp_path: Path) -> Path:
    """
    Builds a representative dummy project structure:
    dummy_project/
    ├── src/
    │   ├── calculator.py   (classes, functions, typing, imports)
    │   └── service.py      (async methods, imports)
    ├── tests/
    │   └── test_calc.py    (test functions, imports)
    ├── docs/
    │   └── architecture.md (Markdown sections)
    ├── config.json         (structured JSON)
    ├── settings.yaml       (structured YAML)
    ├── README.md           (project overview)
    ├── corrupted.bin       (binary data with null bytes)
    ├── syntax_error.py     (malformed Python code)
    └── empty.py            (zero-byte file)
    """
    root = tmp_path / "sandbox_project"
    root.mkdir(parents=True, exist_ok=True)

    # 1. /src
    src_dir = root / "src"
    src_dir.mkdir()

    calc_code = (
        '"""Calculator module with arithmetic operations."""\n'
        "import math\n"
        "from typing import List, Optional\n"
        "\n"
        "\n"
        "class Calculator:\n"
        '    """Arithmetic engine supporting basic operations."""\n'
        "\n"
        "    def __init__(self, precision: int = 2) -> None:\n"
        "        self.precision = precision\n"
        "\n"
        "    def add(self, a: int, b: int) -> int:\n"
        '        """Return the sum of a and b."""\n'
        "        return a + b\n"
        "\n"
        "    def divide(self, a: float, b: float) -> float:\n"
        '        """Return division of a by b, or NaN if b is 0."""\n'
        "        if b == 0:\n"
        "            return math.nan\n"
        "        return round(a / b, self.precision)\n"
        "\n"
        "    async def async_compute(self, factor: int = 1) -> int:\n"
        '        """Asynchronously compute scaled value."""\n'
        "        return 100 * factor\n"
        "\n"
        "\n"
        "def create_calculator(precision: int = 2) -> Calculator:\n"
        '    """Factory function returning a Calculator instance."""\n'
        "    return Calculator(precision=precision)\n"
        "\n"
        "\n"
        "def compute_total(values: List[int]) -> int:\n"
        '    """Sum all values in a list."""\n'
        "    return sum(values)\n"
    )
    (src_dir / "calculator.py").write_text(calc_code, encoding="utf-8")

    service_code = (
        '"""Data service module."""\n'
        "import os\n"
        "import sys\n"
        "\n"
        "class DataService:\n"
        "    def fetch(self, key: str) -> dict:\n"
        '        return {"key": key, "status": "active"}\n'
    )
    (src_dir / "service.py").write_text(service_code, encoding="utf-8")

    # 2. /tests
    tests_dir = root / "tests"
    tests_dir.mkdir()
    test_calc_code = (
        "from src.calculator import Calculator, create_calculator\n"
        "\n"
        "def test_addition():\n"
        "    calc = create_calculator()\n"
        "    assert calc.add(2, 3) == 5\n"
        "\n"
        "def test_division():\n"
        "    calc = Calculator()\n"
        "    assert calc.divide(10, 2) == 5.0\n"
    )
    (tests_dir / "test_calc.py").write_text(test_calc_code, encoding="utf-8")

    # 3. /docs
    docs_dir = root / "docs"
    docs_dir.mkdir()
    arch_doc = (
        "# Architecture Overview\n"
        "\n"
        "NodeInsight acts as a standalone code reading subsystem.\n"
        "\n"
        "## Components\n"
        "- File Ingestion\n"
        "- AST Parsing\n"
        "- Sandboxing\n"
        "\n"
        "## Security\n"
        "Strict path boundaries protect against directory traversal.\n"
    )
    (docs_dir / "architecture.md").write_text(arch_doc, encoding="utf-8")

    # 4. Non-Python structured files
    config_data = {
        "project": "NodeInsight Sandbox",
        "version": "1.1.0",
        "settings": {"debug": True, "max_depth": 5, "allowed_extensions": [".py", ".json", ".md"]},
    }
    (root / "config.json").write_text(json.dumps(config_data, indent=2), encoding="utf-8")

    (root / "README.md").write_text(
        "# NodeInsight Sandbox Project\n\nTest dummy workspace for standalone reader validation.\n",
        encoding="utf-8",
    )

    (root / "settings.yaml").write_text(
        "env: test\ntimeout: 30\nworkers: 4\nlogging: verbose\n",
        encoding="utf-8",
    )

    # 5. Boundary / corrupted files
    (root / "corrupted.bin").write_bytes(
        b"\x00\xff\xfe\x00\x01\x02\x03\x00\x00NodeInsightBinaryCorruptedPayload\x00"
    )

    (root / "syntax_error.py").write_text(
        "def malformed_syntax(:\n    return 'syntax error'\n",
        encoding="utf-8",
    )

    (root / "empty.py").write_text("", encoding="utf-8")

    return root


@pytest.fixture
def reader(dummy_project: Path) -> NodeInsight:
    """Instantiate a NodeInsight reader targeted at the dummy project root."""
    return NodeInsight(workspace_root=str(dummy_project))


# ─── 1. Core Required Tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_success(reader: NodeInsight, dummy_project: Path) -> None:
    """
    Verifies exact string extraction and payload metadata from an existing file.
    """
    payload_input = {"action": "read_file", "path": "src/calculator.py", "line_numbers": False}
    resp = await reader.call(**payload_input)
    log_test_step("read_file_success", payload_input, resp)

    assert resp["status"] == "success", f"Expected success, got error: {resp.get('error_message')}"
    data = resp["data"]
    assert data["path"] == "src/calculator.py"
    assert "class Calculator:" in data["content"]
    assert "def add(self, a: int, b: int) -> int:" in data["content"]
    assert data["start_line"] == 1
    assert data["end_line"] == data["total_lines"]
    assert data["total_lines"] > 20
    assert data["size_bytes"] > 0
    assert data["encoding"].lower() in ("utf-8", "ascii")

    # Read with line numbers
    num_resp = await reader.read_file("src/calculator.py", start_line=5, end_line=7, line_numbers=True)
    assert num_resp.status == "success"
    lines = num_resp.data["content"].splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("5: ")


@pytest.mark.asyncio
async def test_file_not_found(reader: NodeInsight) -> None:
    """
    Validates that attempting to read a non-existent file returns a structured
    error object with ErrorCode.FILE_NOT_FOUND rather than crashing or throwing unhandled exception.
    """
    payload_input = {"action": "read_file", "path": "src/non_existent_module.py"}
    resp = await reader.call(**payload_input)
    log_test_step("file_not_found", payload_input, resp)

    assert resp["status"] == "error", "Expected error for missing file, but got success"
    assert resp["data"] is None
    assert resp["error_message"] is not None
    assert resp["error_message"].startswith(ErrorCode.FILE_NOT_FOUND)
    assert "src/non_existent_module.py" in resp["error_message"]

    # Direct method call check
    direct_resp = await reader.read_file("ghost_path.txt")
    assert direct_resp.status == "error"
    assert direct_resp.error_message.startswith(ErrorCode.FILE_NOT_FOUND)


@pytest.mark.asyncio
async def test_ast_symbol_extraction(reader: NodeInsight) -> None:
    """
    Confirms classes and functions are parsed via AST and returned with
    correct line numbers, signatures, docstrings, and import graphs.
    """
    payload_input = {"action": "parse_ast", "path": "src/calculator.py"}
    resp = await reader.call(**payload_input)
    log_test_step("ast_symbol_extraction", payload_input, resp)

    assert resp["status"] == "success", f"AST parsing failed: {resp.get('error_message')}"
    data = resp["data"]
    assert data["path"] == "src/calculator.py"

    # Verify Classes
    classes = {c["name"]: c for c in data["classes"]}
    assert "Calculator" in classes
    calc_class = classes["Calculator"]
    assert calc_class["line_number"] == 6
    assert calc_class["docstring"] == "Arithmetic engine supporting basic operations."

    # Verify Methods inside Calculator
    methods = {m["name"]: m for m in calc_class["methods"]}
    assert "__init__" in methods
    assert "add" in methods
    assert "divide" in methods
    assert "async_compute" in methods

    # Verify method signatures and line numbers
    add_method = methods["add"]
    assert add_method["line_number"] == 12
    assert "a: int" in add_method["signature"]
    assert "b: int" in add_method["signature"]
    assert "-> int" in add_method["signature"]
    assert add_method["docstring"] == "Return the sum of a and b."
    assert add_method["is_async"] is False

    async_method = methods["async_compute"]
    assert async_method["is_async"] is True
    assert "factor: int = 1" in async_method["signature"]

    # Verify Top-level Functions
    functions = {f["name"]: f for f in data["functions"]}
    assert "create_calculator" in functions
    assert "compute_total" in functions
    factory_fn = functions["create_calculator"]
    assert "precision: int = 2" in factory_fn["signature"]
    assert "-> Calculator" in factory_fn["signature"]

    # Verify Imports and Import Graph
    import_modules = set(data["import_graph"])
    assert "math" in import_modules
    assert "typing" in import_modules

    import_entries = data["imports"]
    imported_names = [name for imp in import_entries for name in imp["names"]]
    assert "math" in imported_names
    assert "List" in imported_names
    assert "Optional" in imported_names


@pytest.mark.asyncio
async def test_directory_traversal_guard(reader: NodeInsight) -> None:
    """
    Verifies that relative paths pointing outside the project root
    (e.g., ../../etc/passwd, ../../../boot.ini) are strictly caught and blocked.
    """
    malicious_paths = [
        "../../etc/passwd",
        "../../../windows/win.ini",
        "src/../../outside.txt",
        "..\\..\\boot.ini",
        "src/../../../secret_keys.env",
    ]

    for malicious_path in malicious_paths:
        payload_input = {"action": "read_file", "path": malicious_path}
        resp = await reader.call(**payload_input)
        log_test_step(f"directory_traversal_guard: {malicious_path}", payload_input, resp)

        assert resp["status"] == "error", f"Traversal allowed for '{malicious_path}'"
        assert resp["data"] is None
        assert resp["error_message"] is not None
        assert resp["error_message"].startswith(ErrorCode.PATH_TRAVERSAL), (
            f"Expected PathTraversal code, got: {resp['error_message']}"
        )

    # Traversal in parse_ast
    ast_resp = await reader.parse_ast("../../etc/shadow")
    assert ast_resp.status == "error"
    assert ast_resp.error_message.startswith(ErrorCode.PATH_TRAVERSAL)

    # Traversal in list_dir
    list_resp = await reader.list_dir("../../../")
    assert list_resp.status == "error"
    assert list_resp.error_message.startswith(ErrorCode.PATH_TRAVERSAL)


@pytest.mark.asyncio
async def test_search_symbols(reader: NodeInsight) -> None:
    """
    Tests regex and symbol lookup across files in the dummy project directory.
    """
    # 1. Search for Calculator class
    payload_input = {"action": "search_symbols", "query": "Calculator", "path": "."}
    resp = await reader.call(**payload_input)
    log_test_step("search_symbols: Calculator", payload_input, resp)

    assert resp["status"] == "success"
    matches = resp["data"]["matches"]
    class_matches = [m for m in matches if m["symbol_type"] == "class"]
    assert any(m["name"] == "Calculator" and m["path"] == "src/calculator.py" for m in class_matches)

    # 2. Search for method 'divide'
    div_resp = await reader.search_symbols("divide", path="src", symbol_type="method")
    assert div_resp.status == "success"
    div_matches = div_resp.data["matches"]
    assert len(div_matches) == 1
    assert div_matches[0]["name"] == "divide"
    assert div_matches[0]["parent_class"] == "Calculator"

    # 3. Regex search for functions starting with 'test_'
    regex_resp = await reader.search_symbols("^test_", path="tests", symbol_type="function")
    assert regex_resp.status == "success"
    test_matches = {m["name"] for m in regex_resp.data["matches"]}
    assert "test_addition" in test_matches
    assert "test_division" in test_matches

    # 4. Search for non-existent symbol returns empty list without error
    empty_resp = await reader.search_symbols("NonExistentSymbolXYZ", path=".")
    assert empty_resp.status == "success"
    assert empty_resp.data["total_matches"] == 0
    assert len(empty_resp.data["matches"]) == 0


# ─── 2. Exhaustive Boundary, Structured & Error Handling Tests ────────────────

@pytest.mark.asyncio
async def test_structured_formats_ingestion(reader: NodeInsight) -> None:
    """
    Tests safe parsing and structured ingestion for JSON, YAML, and Markdown formats.
    """
    # JSON ingestion
    json_resp = await reader.read_structured("config.json")
    log_test_step("read_structured: config.json", {"path": "config.json"}, json_resp.to_dict())
    assert json_resp.status == "success"
    assert json_resp.data["format"] == "json"
    assert json_resp.data["data"]["project"] == "NodeInsight Sandbox"
    assert json_resp.data["data"]["settings"]["debug"] is True

    # YAML ingestion
    yaml_resp = await reader.read_structured("settings.yaml")
    log_test_step("read_structured: settings.yaml", {"path": "settings.yaml"}, yaml_resp.to_dict())
    assert yaml_resp.status == "success"
    assert yaml_resp.data["format"] == "yaml"
    assert "env" in yaml_resp.data["data"]

    # Markdown ingestion (extracts sections)
    md_resp = await reader.read_structured("docs/architecture.md")
    log_test_step("read_structured: docs/architecture.md", {"path": "docs/architecture.md"}, md_resp.to_dict())
    assert md_resp.status == "success"
    assert md_resp.data["format"] == "markdown"
    assert "sections" in md_resp.data["data"]
    headings = [s["heading"] for s in md_resp.data["data"]["sections"] if s["heading"]]
    assert any("Architecture Overview" in h for h in headings)


@pytest.mark.asyncio
async def test_binary_file_boundary_handling(reader: NodeInsight) -> None:
    """
    Verifies that binary / corrupted files with null bytes are detected
    and safely refused without garbling stdout or raising exceptions.
    """
    payload_input = {"action": "read_file", "path": "corrupted.bin"}
    resp = await reader.call(**payload_input)
    log_test_step("binary_file_refusal", payload_input, resp)

    assert resp["status"] == "error"
    assert resp["error_message"].startswith(ErrorCode.BINARY_FILE)

    # AST parsing on binary file must also be refused
    ast_resp = await reader.parse_ast("corrupted.bin")
    assert ast_resp.status == "error"
    assert ast_resp.error_message.startswith(ErrorCode.BINARY_FILE)


@pytest.mark.asyncio
async def test_syntax_error_ast_resilience(reader: NodeInsight) -> None:
    """
    Validates that AST parser gracefully handles corrupted/invalid Python syntax
    and returns a structured ParseError with line number details.
    """
    payload_input = {"action": "parse_ast", "path": "syntax_error.py"}
    resp = await reader.call(**payload_input)
    log_test_step("syntax_error_ast_handling", payload_input, resp)

    assert resp["status"] == "error"
    assert resp["error_message"].startswith(ErrorCode.PARSE_ERROR)
    assert "line 1" in resp["error_message"].lower() or "syntax" in resp["error_message"].lower()


@pytest.mark.asyncio
async def test_file_too_large_guard(dummy_project: Path) -> None:
    """
    Validates that attempting to read a file exceeding max_file_size limit
    returns FileTooLarge structured error.
    """
    # Create reader with tiny limit (64 bytes)
    restricted_reader = NodeInsight(workspace_root=str(dummy_project), max_file_size=64)
    payload_input = {"action": "read_file", "path": "src/calculator.py"}
    resp = await restricted_reader.call(**payload_input)
    log_test_step("file_too_large_guard", payload_input, resp)

    assert resp["status"] == "error"
    assert resp["error_message"].startswith(ErrorCode.FILE_TOO_LARGE)
    assert "exceeding" in resp["error_message"].lower()


@pytest.mark.asyncio
async def test_directory_passed_as_file(reader: NodeInsight) -> None:
    """
    Passing a directory path to read_file or parse_ast returns IsADirectory error.
    """
    read_resp = await reader.read_file("src")
    assert read_resp.status == "error"
    assert read_resp.error_message.startswith(ErrorCode.IS_A_DIRECTORY)

    ast_resp = await reader.parse_ast("src")
    assert ast_resp.status == "error"
    assert ast_resp.error_message.startswith(ErrorCode.IS_A_DIRECTORY)


@pytest.mark.asyncio
async def test_unified_dispatcher_interface(reader: NodeInsight) -> None:
    """
    Ensures that tool.call() accepts all valid actions and adheres strictly
    to the ToolResponse JSON envelope: {status, data, error_message}.
    """
    valid_actions = [
        ("list_dir", {"path": "src"}),
        ("search_files", {"query": "*.py"}),
        ("read_file", {"path": "src/calculator.py", "start_line": 1, "end_line": 5}),
        ("read_file_chunked", {"path": "src/calculator.py", "chunk_size": 10, "offset_line": 1}),
        ("search_in_file", {"path": "src/calculator.py", "pattern": "def "}),
        ("parse_ast", {"path": "src/calculator.py"}),
        ("search_symbols", {"query": "Calculator"}),
        ("read_structured", {"path": "config.json"}),
    ]

    for action, kwargs in valid_actions:
        resp = await reader.call(action, **kwargs)
        assert isinstance(resp, dict), f"Action '{action}' did not return dict"
        assert resp["status"] == "success", f"Action '{action}' failed: {resp.get('error_message')}"
        assert resp["data"] is not None, f"Action '{action}' returned None data on success"
        assert resp["error_message"] is None

    # Invalid action
    invalid_resp = await reader.call("unsupported_action_123")
    assert invalid_resp["status"] == "error"
    assert invalid_resp["error_message"].startswith(ErrorCode.INVALID_ACTION)


# ─── 3. Standalone Runnable Entrypoint ────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("  NODEINSIGHT STANDALONE TEST SUITE — EXECUTING VIA PYTEST RUNNER")
    print("=" * 80 + "\n")

    # Run pytest directly on this file with verbose output and full stdout capture
    exit_code = pytest.main([__file__, "-v", "-s", "--tb=short"])
    if exit_code == 0:
        print("\n" + "=" * 80)
        print("  ALL NODEINSIGHT TESTS PASSED SUCCESSFULLY (100% PASS RATE)")
        print("=" * 80 + "\n")
    else:
        print(f"\n[FAIL] Test suite failed with exit code {exit_code}")
    sys.exit(exit_code)
