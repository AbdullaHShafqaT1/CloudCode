"""
Tests for FileWriterTool
========================
Covers: new file creation, overwrite + backup, parent dir creation,
        diff patching (success / not-found / ambiguous), appending,
        rollback, delete (with / without confirm), path traversal block,
        and directory creation.

Run with:
    pip install pytest pytest-asyncio
    pytest tests/ -v
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tools.file_writer_tool import FileWriterTool, SecurityError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tool(tmp_path):
    """Return a FileWriterTool scoped to a fresh temporary workspace."""
    return FileWriterTool(workspace_root=tmp_path)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _abs(tool: FileWriterTool, rel: str):
    """Resolve a relative path against the tool's workspace root."""
    return tool.workspace_root / rel


# ---------------------------------------------------------------------------
# A. write_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_new_file(tool):
    """Create a brand-new file and verify content and bytes_written."""
    result = await tool.call("write_file", path="src/hello.py", content="print('hello')\n")

    assert result["status"] == "success"
    assert result["action"] == "write_file"
    assert result["error_message"] is None
    assert result["backup_created"] is None  # No pre-existing file

    written = _abs(tool, "src/hello.py").read_text()
    assert written == "print('hello')\n"

    expected_bytes = len("print('hello')\n".encode())
    assert result["bytes_written"] == expected_bytes


@pytest.mark.asyncio
async def test_write_overwrites_with_backup(tool):
    """Overwriting an existing file must create a .bak preserving original content."""
    target = _abs(tool, "main.py")
    target.write_text("original content", encoding="utf-8")

    result = await tool.call("write_file", path="main.py", content="new content", backup=True)

    assert result["status"] == "success"
    assert result["backup_created"] is not None

    # New content is in place
    assert target.read_text() == "new content"

    # Backup preserves original
    bak = _abs(tool, result["backup_created"])
    assert bak.read_text() == "original content"


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(tool):
    """write_file must auto-create nested parent directories."""
    result = await tool.call(
        "write_file",
        path="a/b/c/deep.txt",
        content="deep content",
    )

    assert result["status"] == "success"
    assert _abs(tool, "a/b/c/deep.txt").read_text() == "deep content"


@pytest.mark.asyncio
async def test_write_no_backup_when_flag_false(tool):
    """With backup=False no .bak file should be created on overwrite."""
    target = _abs(tool, "nobackup.txt")
    target.write_text("old", encoding="utf-8")

    result = await tool.call("write_file", path="nobackup.txt", content="new", backup=False)

    assert result["status"] == "success"
    assert result["backup_created"] is None
    assert not _abs(tool, "nobackup.txt.bak").exists()


# ---------------------------------------------------------------------------
# B. patch_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_file_success(tool):
    """Replace a unique block and verify the new content."""
    target = _abs(tool, "config.py")
    target.write_text("DEBUG = False\nVERSION = '1.0'\n", encoding="utf-8")

    result = await tool.call(
        "patch_file",
        path="config.py",
        target_block="DEBUG = False",
        replacement_block="DEBUG = True",
    )

    assert result["status"] == "success"
    assert "DEBUG = True" in target.read_text()
    assert "DEBUG = False" not in target.read_text()
    assert result["lines_affected"] is not None


@pytest.mark.asyncio
async def test_patch_file_not_found(tool):
    """patch_file must return an error when the target block is absent."""
    target = _abs(tool, "script.py")
    target.write_text("x = 1\n", encoding="utf-8")

    result = await tool.call(
        "patch_file",
        path="script.py",
        target_block="NONEXISTENT_BLOCK",
        replacement_block="anything",
    )

    assert result["status"] == "error"
    assert "not found" in result["error_message"].lower()
    # File must be unchanged
    assert target.read_text() == "x = 1\n"


@pytest.mark.asyncio
async def test_patch_file_ambiguous(tool):
    """patch_file must return an error when target_block matches more than once."""
    repeated = "MARKER\n"
    target = _abs(tool, "dup.py")
    target.write_text(repeated * 3, encoding="utf-8")

    result = await tool.call(
        "patch_file",
        path="dup.py",
        target_block="MARKER",
        replacement_block="REPLACED",
    )

    assert result["status"] == "error"
    assert "ambiguous" in result["error_message"].lower() or "3" in result["error_message"]
    # File must be unchanged
    assert target.read_text() == repeated * 3


# ---------------------------------------------------------------------------
# C. append_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_file(tool):
    """Append content and verify cumulative file state."""
    target = _abs(tool, "log.txt")
    target.write_text("Line 1\n", encoding="utf-8")

    result = await tool.call("append_file", path="log.txt", content="Line 2\n")

    assert result["status"] == "success"
    assert target.read_text() == "Line 1\nLine 2\n"
    assert result["bytes_written"] == len("Line 2\n".encode())


@pytest.mark.asyncio
async def test_append_creates_file_if_missing(tool):
    """append_file should work on a non-existent file (creates it fresh)."""
    result = await tool.call("append_file", path="new_log.txt", content="First line\n")

    assert result["status"] == "success"
    assert _abs(tool, "new_log.txt").read_text() == "First line\n"


# ---------------------------------------------------------------------------
# D. create_backup / rollback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_restores_original(tool):
    """Write → create backup → overwrite → rollback → content matches original."""
    target = _abs(tool, "restore_me.py")
    original_content = "# original\n"
    target.write_text(original_content, encoding="utf-8")

    # Explicitly back up
    bak_result = await tool.call("create_backup", path="restore_me.py")
    assert bak_result["status"] == "success"

    # Overwrite (without backup to keep things clean for this test)
    await tool.call("write_file", path="restore_me.py", content="# modified\n", backup=False)
    assert target.read_text() == "# modified\n"

    # Rollback
    rb_result = await tool.call("rollback", path="restore_me.py")
    assert rb_result["status"] == "success"
    assert target.read_text() == original_content


@pytest.mark.asyncio
async def test_rollback_no_bak_returns_error(tool):
    """rollback with no .bak present must return an error."""
    _abs(tool, "orphan.py").write_text("data", encoding="utf-8")

    result = await tool.call("rollback", path="orphan.py")
    assert result["status"] == "error"
    assert "no backup" in result["error_message"].lower()


# ---------------------------------------------------------------------------
# E. delete_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_without_confirm_rejected(tool):
    """delete_file without confirm=True must be rejected."""
    target = _abs(tool, "keep_me.txt")
    target.write_text("important", encoding="utf-8")

    result = await tool.call("delete_file", path="keep_me.txt")  # confirm defaults to False

    assert result["status"] == "error"
    assert "confirm" in result["error_message"].lower()
    assert target.exists()  # file untouched


@pytest.mark.asyncio
async def test_delete_with_confirm(tool):
    """delete_file with confirm=True must remove the file and create a backup."""
    target = _abs(tool, "bye.txt")
    target.write_text("goodbye", encoding="utf-8")

    result = await tool.call("delete_file", path="bye.txt", confirm=True)

    assert result["status"] == "success"
    assert not target.exists()

    # Backup must exist
    bak = _abs(tool, result["backup_created"])
    assert bak.exists()
    assert bak.read_text() == "goodbye"


# ---------------------------------------------------------------------------
# Security: path traversal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_path_traversal_blocked_write(tool):
    """write_file with a traversal path must return a security error."""
    result = await tool.call("write_file", path="../../etc/passwd", content="hacked")

    assert result["status"] == "error"
    assert "traversal" in result["error_message"].lower() or "outside" in result["error_message"].lower()


@pytest.mark.asyncio
async def test_path_traversal_blocked_delete(tool):
    """delete_file with a traversal path must return a security error."""
    result = await tool.call("delete_file", path="../../../sensitive.txt", confirm=True)

    assert result["status"] == "error"
    assert "traversal" in result["error_message"].lower() or "outside" in result["error_message"].lower()


@pytest.mark.asyncio
async def test_path_traversal_direct_raises(tool):
    """_resolve_safe_path must raise SecurityError directly for traversal paths."""
    with pytest.raises(SecurityError):
        tool._resolve_safe_path("../../outside.txt")


# ---------------------------------------------------------------------------
# E. create_dir
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_dir(tool):
    """create_dir must create nested directories."""
    result = await tool.call("create_dir", path="nested/deep/folder")

    assert result["status"] == "success"
    assert _abs(tool, "nested/deep/folder").is_dir()


@pytest.mark.asyncio
async def test_create_dir_idempotent(tool):
    """create_dir called twice on the same path must not error."""
    await tool.call("create_dir", path="existing_dir")
    result = await tool.call("create_dir", path="existing_dir")

    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Audit record schema validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_record_has_required_fields(tool):
    """Every successful response must contain all required telemetry fields."""
    required_fields = {
        "status", "action", "path", "backup_created",
        "bytes_written", "lines_affected", "timestamp", "data", "error_message",
    }

    result = await tool.call("write_file", path="audit_test.py", content="x=1")
    assert required_fields.issubset(result.keys()), (
        f"Missing fields: {required_fields - result.keys()}"
    )


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_action_returns_error(tool):
    """An unrecognised action string must return a structured error, not raise."""
    result = await tool.call("explode_everything", path="x.py")
    assert result["status"] == "error"
    assert "unknown action" in result["error_message"].lower()
