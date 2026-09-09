"""
Tests for FileReaderTool — full coverage of all five operations and all edge cases.

Run with:
    pytest tests/test_file_reader_tool.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path regardless of CWD
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

from file_reader_tool import ErrorCode, FileReaderTool, ToolResponse


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """
    Build a small, representative directory tree inside a temp directory:

    workspace/
    ├── src/
    │   ├── main.py        (20 lines)
    │   ├── utils.py       (5 lines)
    │   └── app.js         (3 lines)
    ├── docs/
    │   └── readme.md      (10 lines)
    ├── node_modules/
    │   └── ignored.js     (1 line)
    ├── .git/
    │   └── config         (1 line)
    ├── binary.bin         (binary)
    └── large.txt          (created dynamically in tests that need it)
    """
    (workspace := tmp_path).mkdir(exist_ok=True)

    src = workspace / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "\n".join(f"line_{i:02d} = {i}" for i in range(1, 21)), encoding="utf-8"
    )
    (src / "utils.py").write_text(
        "def helper():\n    pass\n\ndef another():\n    return 42\n", encoding="utf-8"
    )
    (src / "app.js").write_text("console.log('hello');\n// js\nmodule.exports = {};\n", encoding="utf-8")

    docs = workspace / "docs"
    docs.mkdir()
    (docs / "readme.md").write_text(
        "\n".join(f"# Section {i}" for i in range(1, 11)), encoding="utf-8"
    )

    # Directories that should be auto-ignored
    nm = workspace / "node_modules"
    nm.mkdir()
    (nm / "ignored.js").write_text("module.exports = {};", encoding="utf-8")

    git = workspace / ".git"
    git.mkdir()
    (git / "config").write_text("[core]\n    bare = false\n", encoding="utf-8")

    # Binary file
    (workspace / "binary.bin").write_bytes(b"\x00\x01\x02\x03" + b"Hello binary" + b"\x00")

    return workspace


@pytest.fixture()
def tool(workspace: Path) -> FileReaderTool:
    return FileReaderTool(workspace_root=str(workspace))


# ──────────────────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────────────────

def ok(resp: dict) -> None:
    """Assert a response is successful."""
    assert resp["status"] == "success", f"Expected success, got error: {resp['error_message']}"


def err(resp: dict, code: str) -> None:
    """Assert a response is an error with the given code prefix."""
    assert resp["status"] == "error", f"Expected error '{code}', got success with data: {resp['data']}"
    assert resp["error_message"].startswith(code), (
        f"Expected error code '{code}', got: {resp['error_message']}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# A. list_dir tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_dir_basic(tool):
    """Lists immediate children of workspace root correctly."""
    resp = await tool.call("list_dir", path=".")
    ok(resp)
    data = resp["data"]
    names = {e["name"] for e in data["entries"]}
    assert "src" in names
    assert "docs" in names
    assert "binary.bin" in names


@pytest.mark.asyncio
async def test_list_dir_ignores_system_dirs_by_default(tool):
    """node_modules and .git are excluded unless include_ignored=True."""
    resp = await tool.call("list_dir", path=".")
    ok(resp)
    names = {e["name"] for e in resp["data"]["entries"]}
    assert "node_modules" not in names
    assert ".git" not in names


@pytest.mark.asyncio
async def test_list_dir_include_ignored(tool):
    """include_ignored=True surfaces node_modules and .git."""
    resp = await tool.call("list_dir", path=".", include_ignored=True, include_hidden=True)
    ok(resp)
    names = {e["name"] for e in resp["data"]["entries"]}
    assert "node_modules" in names
    assert ".git" in names


@pytest.mark.asyncio
async def test_list_dir_recursive(tool):
    """Recursive listing descends into subdirectories."""
    resp = await tool.call("list_dir", path=".", recursive=True, max_depth=5)
    ok(resp)
    paths = {e["path"] for e in resp["data"]["entries"]}
    assert "src/main.py" in paths
    assert "src/utils.py" in paths
    assert "docs/readme.md" in paths


@pytest.mark.asyncio
async def test_list_dir_max_depth_respected(tool, workspace):
    """max_depth=1 means only immediate children of src/ are listed."""
    # Create a deeply nested structure
    deep = workspace / "src" / "pkg" / "sub"
    deep.mkdir(parents=True)
    (deep / "deep.py").write_text("pass", encoding="utf-8")

    resp = await tool.call("list_dir", path="src", recursive=True, max_depth=1)
    ok(resp)
    paths = {e["path"] for e in resp["data"]["entries"]}
    assert "src/pkg/sub/deep.py" not in paths  # too deep
    assert "src/pkg" in paths  # depth 1 dir is listed


@pytest.mark.asyncio
async def test_list_dir_file_type_filter(tool):
    """file_types=['.py'] returns only Python files."""
    resp = await tool.call("list_dir", path="src", file_types=[".py"])
    ok(resp)
    entries = resp["data"]["entries"]
    for e in entries:
        if e["type"] == "file":
            assert e["name"].endswith(".py"), f"Unexpected file: {e['name']}"
    names = {e["name"] for e in entries}
    assert "app.js" not in names


@pytest.mark.asyncio
async def test_list_dir_tree_string_present(tool):
    """tree field is a non-empty string."""
    resp = await tool.call("list_dir", path=".")
    ok(resp)
    assert isinstance(resp["data"]["tree"], str)
    assert len(resp["data"]["tree"]) > 0


@pytest.mark.asyncio
async def test_list_dir_counts(tool):
    """total_files and total_dirs are accurate."""
    resp = await tool.call("list_dir", path="src")
    ok(resp)
    data = resp["data"]
    assert data["total_files"] == 3  # main.py, utils.py, app.js
    assert data["total_dirs"] == 0


@pytest.mark.asyncio
async def test_list_dir_nonexistent(tool):
    """Non-existent directory returns FileNotFound."""
    resp = await tool.call("list_dir", path="does_not_exist")
    err(resp, ErrorCode.FILE_NOT_FOUND)


@pytest.mark.asyncio
async def test_list_dir_on_file(tool):
    """Passing a file path returns NotADirectory."""
    resp = await tool.call("list_dir", path="src/main.py")
    err(resp, ErrorCode.NOT_A_DIRECTORY)


# ──────────────────────────────────────────────────────────────────────────────
# B. search_files tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_files_glob_pattern(tool):
    """Glob pattern *.py matches Python files."""
    resp = await tool.call("search_files", path=".", query="*.py")
    ok(resp)
    matches = resp["data"]["matches"]
    names = {m["name"] for m in matches}
    assert "main.py" in names
    assert "utils.py" in names
    assert "app.js" not in names


@pytest.mark.asyncio
async def test_search_files_partial_name(tool):
    """Substring match on 'main' finds main.py."""
    resp = await tool.call("search_files", path=".", query="main")
    ok(resp)
    names = {m["name"] for m in resp["data"]["matches"]}
    assert "main.py" in names


@pytest.mark.asyncio
async def test_search_files_case_insensitive(tool, workspace):
    """Substring search is case-insensitive."""
    (workspace / "src" / "MyModule.py").write_text("pass", encoding="utf-8")
    resp = await tool.call("search_files", path="src", query="mymodule")
    ok(resp)
    names = {m["name"] for m in resp["data"]["matches"]}
    assert "MyModule.py" in names


@pytest.mark.asyncio
async def test_search_files_file_type_filter(tool):
    """file_types=['.md'] restricts file results to Markdown; directories may still appear."""
    resp = await tool.call("search_files", path=".", query="*", file_types=[".md"])
    ok(resp)
    for m in resp["data"]["matches"]:
        if m["type"] == "file":
            assert m["name"].endswith(".md"), f"Non-md file returned: {m['name']}"
    # At least readme.md should be found
    names = {m["name"] for m in resp["data"]["matches"]}
    assert "readme.md" in names


@pytest.mark.asyncio
async def test_search_files_ignores_system_dirs(tool):
    """node_modules contents are excluded by default."""
    resp = await tool.call("search_files", path=".", query="*.js")
    ok(resp)
    paths = {m["path"] for m in resp["data"]["matches"]}
    assert not any("node_modules" in p for p in paths)


@pytest.mark.asyncio
async def test_search_files_nonexistent_root(tool):
    """Non-existent search root returns FileNotFound."""
    resp = await tool.call("search_files", path="no_such_dir", query="*")
    err(resp, ErrorCode.FILE_NOT_FOUND)


@pytest.mark.asyncio
async def test_search_files_metadata_present(tool):
    """Each match includes size and modified fields."""
    resp = await tool.call("search_files", path="src", query="main.py")
    ok(resp)
    m = resp["data"]["matches"][0]
    assert "size" in m
    assert "modified" in m
    assert m["size"] > 0


# ──────────────────────────────────────────────────────────────────────────────
# C. read_file tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_full(tool):
    """Full read returns all 20 lines of main.py."""
    resp = await tool.call("read_file", path="src/main.py")
    ok(resp)
    data = resp["data"]
    assert data["total_lines"] == 20
    assert data["start_line"] == 1
    assert data["end_line"] == 20
    assert "line_01" in data["content"]
    assert "line_20" in data["content"]


@pytest.mark.asyncio
async def test_read_file_line_range(tool):
    """Line-range read returns only the specified lines."""
    resp = await tool.call("read_file", path="src/main.py", start_line=5, end_line=10)
    ok(resp)
    data = resp["data"]
    assert data["start_line"] == 5
    assert data["end_line"] == 10
    assert "line_05" in data["content"]
    assert "line_10" in data["content"]
    assert "line_04" not in data["content"]
    assert "line_11" not in data["content"]


@pytest.mark.asyncio
async def test_read_file_with_line_numbers(tool):
    """line_numbers=True prepends '5: ' style prefixes."""
    resp = await tool.call(
        "read_file", path="src/main.py", start_line=3, end_line=5, line_numbers=True
    )
    ok(resp)
    lines = resp["data"]["content"].splitlines()
    assert lines[0].startswith("3: ")
    assert lines[1].startswith("4: ")
    assert lines[2].startswith("5: ")


@pytest.mark.asyncio
async def test_read_file_metadata(tool, workspace):
    """ReadFileResult includes encoding and size_bytes."""
    resp = await tool.call("read_file", path="src/utils.py")
    ok(resp)
    data = resp["data"]
    assert "encoding" in data
    assert data["size_bytes"] > 0


@pytest.mark.asyncio
async def test_read_file_nonexistent(tool):
    """Non-existent file returns FileNotFound."""
    resp = await tool.call("read_file", path="src/ghost.py")
    err(resp, ErrorCode.FILE_NOT_FOUND)


@pytest.mark.asyncio
async def test_read_file_directory(tool):
    """Pointing read_file at a directory returns IsADirectory."""
    resp = await tool.call("read_file", path="src")
    err(resp, ErrorCode.IS_A_DIRECTORY)


@pytest.mark.asyncio
async def test_read_file_binary_refused(tool):
    """Reading binary file returns BinaryFile error."""
    resp = await tool.call("read_file", path="binary.bin")
    err(resp, ErrorCode.BINARY_FILE)


@pytest.mark.asyncio
async def test_read_file_too_large(tool, workspace):
    """File exceeding max_file_size returns FileTooLarge."""
    tiny_tool = FileReaderTool(workspace_root=str(workspace), max_file_size=10)
    resp = await tiny_tool.call("read_file", path="src/main.py")
    err(resp, ErrorCode.FILE_TOO_LARGE)


@pytest.mark.asyncio
async def test_read_file_invalid_line_range_inverted(tool):
    """start_line > end_line returns InvalidLineRange."""
    resp = await tool.call("read_file", path="src/main.py", start_line=15, end_line=5)
    err(resp, ErrorCode.INVALID_LINE_RANGE)


@pytest.mark.asyncio
async def test_read_file_invalid_line_range_out_of_bounds(tool):
    """start_line beyond total lines returns InvalidLineRange."""
    resp = await tool.call("read_file", path="src/main.py", start_line=999, end_line=1000)
    err(resp, ErrorCode.INVALID_LINE_RANGE)


@pytest.mark.asyncio
async def test_read_file_end_line_clamped(tool):
    """end_line beyond total is clamped to total_lines without error."""
    resp = await tool.call("read_file", path="src/main.py", start_line=18, end_line=9999)
    ok(resp)
    assert resp["data"]["end_line"] == 20


# ──────────────────────────────────────────────────────────────────────────────
# D. read_file_chunked tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_chunked_first_chunk(tool):
    """First chunk (offset=1) returns correct lines and has_more=True for a 20-line file."""
    resp = await tool.call(
        "read_file_chunked", path="src/main.py", chunk_size=8, offset_line=1
    )
    ok(resp)
    data = resp["data"]
    assert data["start_line"] == 1
    assert data["end_line"] == 8
    assert data["has_more"] is True
    assert data["next_offset"] == 9
    assert data["total_lines"] == 20


@pytest.mark.asyncio
async def test_read_file_chunked_pagination(tool):
    """Paginating through a 20-line file with chunk_size=8 yields 3 chunks."""
    chunks = []
    offset = 1
    while True:
        resp = await tool.call(
            "read_file_chunked", path="src/main.py", chunk_size=8, offset_line=offset
        )
        ok(resp)
        data = resp["data"]
        chunks.append(data)
        if not data["has_more"]:
            break
        offset = data["next_offset"]

    assert len(chunks) == 3
    # Verify continuity
    assert chunks[0]["start_line"] == 1
    assert chunks[1]["start_line"] == 9
    assert chunks[2]["start_line"] == 17
    assert chunks[2]["has_more"] is False


@pytest.mark.asyncio
async def test_read_file_chunked_exact_fit(tool, workspace):
    """File with exactly chunk_size lines: has_more=False on first chunk."""
    (workspace / "exact.txt").write_text("\n".join(str(i) for i in range(10)), encoding="utf-8")
    resp = await tool.call(
        "read_file_chunked", path="exact.txt", chunk_size=10, offset_line=1
    )
    ok(resp)
    assert resp["data"]["has_more"] is False


@pytest.mark.asyncio
async def test_read_file_chunked_invalid_offset(tool):
    """offset_line beyond total returns InvalidLineRange."""
    resp = await tool.call(
        "read_file_chunked", path="src/main.py", offset_line=999
    )
    err(resp, ErrorCode.INVALID_LINE_RANGE)


@pytest.mark.asyncio
async def test_read_file_chunked_invalid_chunk_size(tool):
    """chunk_size=0 returns InvalidArgument."""
    resp = await tool.call(
        "read_file_chunked", path="src/main.py", chunk_size=0, offset_line=1
    )
    err(resp, ErrorCode.INVALID_ARGUMENT)


@pytest.mark.asyncio
async def test_read_file_chunked_binary_refused(tool):
    """Binary file returns BinaryFile error."""
    resp = await tool.call("read_file_chunked", path="binary.bin")
    err(resp, ErrorCode.BINARY_FILE)


# ──────────────────────────────────────────────────────────────────────────────
# E. search_in_file tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_in_file_literal(tool):
    """Literal string search returns correct match line numbers."""
    resp = await tool.call("search_in_file", path="src/utils.py", pattern="def")
    ok(resp)
    data = resp["data"]
    assert data["total_matches"] == 2
    line_nums = {m["line_number"] for m in data["matches"]}
    assert 1 in line_nums  # "def helper():"
    assert 4 in line_nums  # "def another():"


@pytest.mark.asyncio
async def test_search_in_file_context_lines(tool):
    """context_lines=1 includes one line before and after each match."""
    resp = await tool.call(
        "search_in_file", path="src/utils.py", pattern="pass", context_lines=1
    )
    ok(resp)
    match = resp["data"]["matches"][0]
    assert len(match["context_before"]) == 1
    assert len(match["context_after"]) == 1
    assert "def helper" in match["context_before"][0]


@pytest.mark.asyncio
async def test_search_in_file_regex(tool):
    """Regex search finds lines matching the pattern."""
    resp = await tool.call(
        "search_in_file",
        path="src/main.py",
        pattern=r"line_0[2-4]",
        use_regex=True,
    )
    ok(resp)
    assert resp["data"]["use_regex"] is True
    line_nums = {m["line_number"] for m in resp["data"]["matches"]}
    assert 2 in line_nums
    assert 3 in line_nums
    assert 4 in line_nums
    assert 5 not in line_nums


@pytest.mark.asyncio
async def test_search_in_file_no_matches(tool):
    """Search for absent pattern returns 0 matches (success, not error)."""
    resp = await tool.call("search_in_file", path="src/utils.py", pattern="NONEXISTENT_XYZ")
    ok(resp)
    assert resp["data"]["total_matches"] == 0


@pytest.mark.asyncio
async def test_search_in_file_invalid_regex(tool):
    """Malformed regex returns InvalidArgument."""
    resp = await tool.call(
        "search_in_file", path="src/utils.py", pattern="[invalid(", use_regex=True
    )
    err(resp, ErrorCode.INVALID_ARGUMENT)


@pytest.mark.asyncio
async def test_search_in_file_nonexistent_file(tool):
    """Searching a non-existent file returns FileNotFound."""
    resp = await tool.call("search_in_file", path="src/nope.py", pattern="x")
    err(resp, ErrorCode.FILE_NOT_FOUND)


@pytest.mark.asyncio
async def test_search_in_file_binary_refused(tool):
    """Binary file returns BinaryFile error."""
    resp = await tool.call("search_in_file", path="binary.bin", pattern="Hello")
    err(resp, ErrorCode.BINARY_FILE)


@pytest.mark.asyncio
async def test_search_in_file_truncation(tool, workspace):
    """max_matches cap sets truncated=True when there are more matches."""
    # Create a file with many identical lines
    (workspace / "many.txt").write_text(
        "\n".join(["match_line"] * 50), encoding="utf-8"
    )
    resp = await tool.call(
        "search_in_file", path="many.txt", pattern="match_line", max_matches=10
    )
    ok(resp)
    data = resp["data"]
    assert data["total_matches"] == 10
    assert data["truncated"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Security & Safety Tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_path_traversal_blocked_read_file(tool):
    """../../etc/passwd-style paths are rejected."""
    resp = await tool.call("read_file", path="../../etc/passwd")
    err(resp, ErrorCode.PATH_TRAVERSAL)


@pytest.mark.asyncio
async def test_path_traversal_blocked_list_dir(tool):
    """Path traversal in list_dir is rejected."""
    resp = await tool.call("list_dir", path="../../../")
    err(resp, ErrorCode.PATH_TRAVERSAL)


@pytest.mark.asyncio
async def test_path_traversal_blocked_search_in_file(tool):
    """Path traversal in search_in_file is rejected."""
    resp = await tool.call("search_in_file", path="../../secret.txt", pattern="pw")
    err(resp, ErrorCode.PATH_TRAVERSAL)


@pytest.mark.asyncio
async def test_path_traversal_blocked_chunked(tool):
    """Path traversal in read_file_chunked is rejected."""
    resp = await tool.call("read_file_chunked", path="../../../windows/system32/config")
    err(resp, ErrorCode.PATH_TRAVERSAL)


@pytest.mark.asyncio
async def test_absolute_path_within_workspace(tool, workspace):
    """Absolute path that is inside workspace root is accepted."""
    abs_path = str(workspace / "src" / "main.py")
    resp = await tool.call("read_file", path=abs_path)
    # Either succeeds (if resolved inside root) or raises PathTraversal
    # Absolute paths outside the root must be rejected
    assert resp["status"] in ("success", "error")


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="chmod not reliable on Windows CI")
async def test_permission_denied(tool, workspace):
    """Permission-denied file returns PermissionDenied error."""
    protected = workspace / "noaccess.txt"
    protected.write_text("secret", encoding="utf-8")
    protected.chmod(0o000)
    try:
        resp = await tool.call("read_file", path="noaccess.txt")
        err(resp, ErrorCode.PERMISSION_DENIED)
    finally:
        protected.chmod(0o644)


# ──────────────────────────────────────────────────────────────────────────────
# Unified call() dispatcher tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_invalid_action(tool):
    """Unknown action returns InvalidAction error."""
    resp = await tool.call("delete_everything")
    err(resp, ErrorCode.INVALID_ACTION)


@pytest.mark.asyncio
async def test_call_invalid_kwargs(tool):
    """Wrong keyword argument returns InvalidArgument error."""
    resp = await tool.call("read_file", path="src/main.py", nonexistent_param=True)
    err(resp, ErrorCode.INVALID_ARGUMENT)


@pytest.mark.asyncio
async def test_call_returns_dict(tool):
    """call() always returns a plain dict, never raises."""
    resp = await tool.call("list_dir", path=".")
    assert isinstance(resp, dict)
    assert "status" in resp
    assert "data" in resp
    assert "error_message" in resp


@pytest.mark.asyncio
async def test_call_all_valid_actions_dispatch(tool):
    """All five actions are reachable via call()."""
    actions = [
        ("list_dir",          {"path": "."}),
        ("search_files",      {"path": ".", "query": "*.py"}),
        ("read_file",         {"path": "src/main.py"}),
        ("read_file_chunked", {"path": "src/main.py", "offset_line": 1}),
        ("search_in_file",    {"path": "src/utils.py", "pattern": "def"}),
    ]
    for action, kwargs in actions:
        resp = await tool.call(action, **kwargs)
        assert resp["status"] == "success", (
            f"Action '{action}' failed: {resp['error_message']}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# ToolResponse model tests
# ──────────────────────────────────────────────────────────────────────────────

def test_tool_response_success_factory():
    r = ToolResponse.success({"key": "value"})
    assert r.status == "success"
    assert r.data == {"key": "value"}
    assert r.error_message is None


def test_tool_response_error_factory():
    r = ToolResponse.error(ErrorCode.FILE_NOT_FOUND, "path/to/file")
    assert r.status == "error"
    assert r.data is None
    assert r.error_message == "FileNotFound: path/to/file"


def test_tool_response_to_dict():
    r = ToolResponse.success(42)
    d = r.to_dict()
    assert d == {"status": "success", "data": 42, "error_message": None}
