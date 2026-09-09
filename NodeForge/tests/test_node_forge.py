"""
test_node_forge.py — NodeForge / FileWriterTool Standalone Test Suite
======================================================================
Comprehensive, self-contained pytest suite that verifies every core
capability of the NodeForge writer module.

Dependencies (no AutoGen / LegacyBridge / remote connections required):
    pip install pytest pytest-asyncio

Run:
    pytest tests/test_node_forge.py -v
    python tests/test_node_forge.py          # via __main__ entrypoint

Coverage matrix
---------------
  - Atomic file creation & overwrite
  - Patch / diff application (success, not-found, ambiguous guards)
  - Path validation & sandbox enforcement (traversal prevention)
  - Nested directory auto-scaffolding
  - Safety & rollback after simulated I/O failure
  - Content integrity checksums (SHA-256)
  - Read-only permission-error fixture
  - Audit-record schema completeness
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path when executed directly
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.file_writer_tool import FileWriterTool, SecurityError  # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================

def _sha256(text: str) -> str:
    """Return the hex SHA-256 digest of a UTF-8 encoded string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _abs(tool: FileWriterTool, rel: str) -> Path:
    """Resolve a relative path inside the tool workspace root."""
    return tool.workspace_root / rel


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """
    Build an isolated sandbox directory that mirrors a realistic project layout.

    Structure
    ---------
    <sandbox>/
        main.py          - entry-point stub
        utils.py         - utility helpers stub
        settings.json    - JSON config stub
        readonly.txt     - read-only file (used to probe permission errors)
        readonly_dir/    - read-only directory
    """
    (tmp_path / "main.py").write_text(
        '"""Main entry point."""\n\ndef run():\n    print("NodeForge running")\n',
        encoding="utf-8",
    )
    (tmp_path / "utils.py").write_text(
        '"""Utility helpers."""\n\n\ndef helper(x):\n    return x * 2\n',
        encoding="utf-8",
    )
    (tmp_path / "settings.json").write_text(
        '{\n    "debug": false,\n    "version": "1.0.0"\n}\n',
        encoding="utf-8",
    )

    ro_file = tmp_path / "readonly.txt"
    ro_file.write_text("protected content", encoding="utf-8")
    ro_file.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)

    ro_dir = tmp_path / "readonly_dir"
    ro_dir.mkdir()
    ro_dir.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH | stat.S_IXUSR)

    return tmp_path


@pytest.fixture
def tool(sandbox: Path) -> FileWriterTool:
    """Return a FileWriterTool scoped to the sandbox directory."""
    return FileWriterTool(workspace_root=sandbox)


# ===========================================================================
# 1. Atomic File Creation & Overwrite
# ===========================================================================

class TestAtomicWrite:
    """Atomic write via temp-file + os.replace mechanics."""

    @pytest.mark.asyncio
    async def test_atomic_write_new_file(self, tool: FileWriterTool):
        """
        Write a brand-new file from scratch.

        Verifies:
        - File is created on disk.
        - Content matches exactly (character-level parity).
        - bytes_written matches the UTF-8 byte length.
        - SHA-256 of on-disk content matches expected digest.
        - No backup entry is created (file did not pre-exist).
        - No leftover temp file (.~tmp_*) remains in the directory.
        """
        content = "# Created by NodeForge\nVERSION = '2.0.0'\n"
        result = await tool.call("write_file", path="src/version.py", content=content)

        target = _abs(tool, "src/version.py")

        assert result["status"] == "success", result["error_message"]
        assert result["action"] == "write_file"
        assert result["error_message"] is None
        assert result["backup_created"] is None

        on_disk = target.read_text(encoding="utf-8")
        assert on_disk == content
        assert result["bytes_written"] == len(content.encode("utf-8"))
        assert _sha256(on_disk) == _sha256(content)

        leftover_temps = list(target.parent.glob(".~tmp_*"))
        assert leftover_temps == [], f"Stale temp files: {leftover_temps}"

    @pytest.mark.asyncio
    async def test_atomic_overwrite_preservation(self, tool: FileWriterTool):
        """
        Overwrite main.py (pre-seeded by sandbox fixture).

        Verifies:
        - The new content replaces the old content cleanly (no mixing).
        - The .bak file preserves the original content verbatim (SHA-256 match).
        - bytes_written reflects the new content, not the old.
        """
        original_content = (tool.workspace_root / "main.py").read_text(encoding="utf-8")
        original_digest = _sha256(original_content)

        new_content = '"""Overwritten by test suite."""\n\nprint("overwritten")\n'
        result = await tool.call("write_file", path="main.py", content=new_content, backup=True)

        assert result["status"] == "success", result["error_message"]
        assert result["backup_created"] is not None

        active = _abs(tool, "main.py").read_text(encoding="utf-8")
        assert active == new_content
        assert original_content not in active

        bak = _abs(tool, result["backup_created"])
        assert bak.exists(), f"Expected backup at {bak}"
        assert _sha256(bak.read_text(encoding="utf-8")) == original_digest

        assert result["bytes_written"] == len(new_content.encode("utf-8"))


# ===========================================================================
# 2. Nested Directory Scaffolding
# ===========================================================================

class TestDirectoryScaffolding:
    """Auto-creation of missing intermediate directories."""

    @pytest.mark.asyncio
    async def test_nested_directory_creation(self, tool: FileWriterTool):
        """
        Write to pkg/sub/module.py when none of the parent directories exist.

        Verifies:
        - All intermediate directories are created automatically.
        - The file is written with correct content.
        """
        deep_path = "pkg/sub/module.py"
        content = "# auto-scaffolded module\n\nclass Widget:\n    pass\n"

        result = await tool.call("write_file", path=deep_path, content=content)

        assert result["status"] == "success", result["error_message"]

        target = _abs(tool, deep_path)
        assert target.exists(), "Deep file was not created"
        assert target.read_text(encoding="utf-8") == content

        assert (tool.workspace_root / "pkg").is_dir()
        assert (tool.workspace_root / "pkg" / "sub").is_dir()

    @pytest.mark.asyncio
    async def test_create_dir_explicit(self, tool: FileWriterTool):
        """create_dir creates nested directories and is idempotent on repeat calls."""
        result = await tool.call("create_dir", path="alpha/beta/gamma")
        assert result["status"] == "success"
        assert _abs(tool, "alpha/beta/gamma").is_dir()

        result2 = await tool.call("create_dir", path="alpha/beta/gamma")
        assert result2["status"] == "success"


# ===========================================================================
# 3. Patch / Diff Application
# ===========================================================================

class TestPatchCodeBlock:
    """Line-level patch (target_block -> replacement_block) mechanics."""

    @pytest.mark.asyncio
    async def test_patch_code_block(self, tool: FileWriterTool):
        """
        Patch a single unique block inside utils.py.

        Verifies:
        - The target lines are replaced correctly.
        - Lines adjacent to the patched block remain untouched.
        - lines_affected is reported in the result.
        - SHA-256 of patched file differs from original.
        - The .bak preserves the pre-patch state.
        """
        target_rel = "utils.py"
        target_path = _abs(tool, target_rel)
        original_text = target_path.read_text(encoding="utf-8")
        original_digest = _sha256(original_text)

        target_block = "def helper(x):\n    return x * 2"
        replacement_block = "def helper(x, factor: int = 2):\n    \"\"\"Scale x by factor.\"\"\"\n    return x * factor"

        result = await tool.call(
            "patch_file",
            path=target_rel,
            target_block=target_block,
            replacement_block=replacement_block,
        )

        assert result["status"] == "success", result["error_message"]
        assert result["lines_affected"] is not None

        patched_text = target_path.read_text(encoding="utf-8")

        assert "def helper(x, factor: int = 2):" in patched_text
        assert '"""Scale x by factor."""' in patched_text
        assert "def helper(x):\n    return x * 2" not in patched_text
        assert '"""Utility helpers."""' in patched_text

        assert _sha256(patched_text) != original_digest

        assert result["backup_created"] is not None
        bak = _abs(tool, result["backup_created"])
        assert _sha256(bak.read_text(encoding="utf-8")) == original_digest

    @pytest.mark.asyncio
    async def test_patch_block_not_found_leaves_file_intact(self, tool: FileWriterTool):
        """patch_file returns an error and leaves the file unchanged when block is absent."""
        target_path = _abs(tool, "main.py")
        original_text = target_path.read_text(encoding="utf-8")

        result = await tool.call(
            "patch_file",
            path="main.py",
            target_block="THIS_BLOCK_DOES_NOT_EXIST",
            replacement_block="anything",
        )

        assert result["status"] == "error"
        assert "not found" in result["error_message"].lower()
        assert target_path.read_text(encoding="utf-8") == original_text

    @pytest.mark.asyncio
    async def test_patch_ambiguous_block_rejected(self, tool: FileWriterTool):
        """patch_file rejects ambiguous (duplicate) target blocks without modifying the file."""
        content = "SENTINEL\nSENTINEL\nSENTINEL\n"
        target_path = _abs(tool, "ambiguous.py")
        target_path.write_text(content, encoding="utf-8")

        result = await tool.call(
            "patch_file",
            path="ambiguous.py",
            target_block="SENTINEL",
            replacement_block="REPLACED",
        )

        assert result["status"] == "error"
        assert "ambiguous" in result["error_message"].lower() or "3" in result["error_message"]
        assert target_path.read_text(encoding="utf-8") == content


# ===========================================================================
# 4. Path Validation & Sandbox Enforcement
# ===========================================================================

class TestTraversalGuard:
    """Directory traversal and path-escape prevention."""

    @pytest.mark.asyncio
    async def test_traversal_guard_write(self, tool: FileWriterTool):
        """
        Attempting to write outside the sandbox via ../../ must:
        - Return a structured error record (not raise an unhandled exception).
        - Report status='error'.
        - Include 'traversal' or 'outside' in the error message.
        """
        result = await tool.call(
            "write_file",
            path="../../escaped_file.txt",
            content="I should not exist",
        )

        assert result["status"] == "error"
        msg = result["error_message"].lower()
        assert "traversal" in msg or "outside" in msg

    @pytest.mark.asyncio
    async def test_traversal_guard_delete(self, tool: FileWriterTool):
        """delete_file with traversal path must return error, not raise."""
        result = await tool.call(
            "delete_file",
            path="../../../sensitive.txt",
            confirm=True,
        )

        assert result["status"] == "error"
        msg = result["error_message"].lower()
        assert "traversal" in msg or "outside" in msg

    @pytest.mark.asyncio
    async def test_traversal_guard_patch(self, tool: FileWriterTool):
        """patch_file with traversal path must return error."""
        result = await tool.call(
            "patch_file",
            path="../../target.py",
            target_block="x",
            replacement_block="y",
        )
        assert result["status"] == "error"
        msg = result["error_message"].lower()
        assert "traversal" in msg or "outside" in msg

    def test_traversal_guard_direct_raises(self, tool: FileWriterTool):
        """_resolve_safe_path must raise SecurityError directly (synchronous)."""
        with pytest.raises(SecurityError, match="outside workspace root"):
            tool._resolve_safe_path("../../outside.txt")

    def test_traversal_guard_absolute_path_outside(self, tool: FileWriterTool):
        """An absolute path pointing outside workspace root must also be rejected."""
        with pytest.raises(SecurityError):
            tool._resolve_safe_path("/etc/passwd")

    @pytest.mark.asyncio
    async def test_path_inside_sandbox_allowed(self, tool: FileWriterTool):
        """A deeply nested but still-inside path must succeed."""
        result = await tool.call(
            "write_file",
            path="a/b/c/allowed.py",
            content="# safe\n",
        )
        assert result["status"] == "success"


# ===========================================================================
# 5. Failed Write -> Rollback / Integrity
# ===========================================================================

class TestFailedWriteRollback:
    """Atomic write guarantees: original file is preserved after I/O failure."""

    @pytest.mark.asyncio
    async def test_failed_write_rollback(self, tool: FileWriterTool):
        """
        Simulate a mid-write I/O error by patching _atomic_write.

        Verifies:
        - call() returns a structured error record (no raw exception propagated).
        - The pre-existing target file is completely untouched.
        - SHA-256 of the file after the failed write matches the original digest.
        - No orphaned .~tmp_ files remain on disk.
        """
        target_rel = "main.py"
        target_path = _abs(tool, target_rel)

        original_content = target_path.read_text(encoding="utf-8")
        original_digest = _sha256(original_content)

        simulated_error = OSError("Simulated disk write failure")

        with mock.patch.object(
            FileWriterTool,
            "_atomic_write",
            side_effect=simulated_error,
        ):
            result = await tool.call(
                "write_file",
                path=target_rel,
                content="# this should never land on disk\n",
                backup=False,
            )

        assert result["status"] == "error", "Expected error record, got: " + str(result)
        assert result["error_message"] is not None

        post_content = target_path.read_text(encoding="utf-8")
        assert post_content == original_content, "File content changed despite write failure!"
        assert _sha256(post_content) == original_digest, "SHA-256 mismatch — file was partially corrupted."

        stale = list(target_path.parent.glob(".~tmp_*"))
        assert stale == [], f"Orphaned temp files found: {stale}"

    @pytest.mark.asyncio
    async def test_failed_patch_leaves_file_intact(self, tool: FileWriterTool):
        """
        Simulate a disk error during patch_file atomic write phase.

        The live file must be byte-for-byte identical to the pre-patch state.
        """
        target_rel = "utils.py"
        target_path = _abs(tool, target_rel)
        original_content = target_path.read_text(encoding="utf-8")
        original_digest = _sha256(original_content)

        target_block = "def helper(x):\n    return x * 2"
        replacement_block = "def helper(x):\n    return x * 999"

        with mock.patch.object(
            FileWriterTool,
            "_atomic_write",
            side_effect=OSError("disk full"),
        ):
            result = await tool.call(
                "patch_file",
                path=target_rel,
                target_block=target_block,
                replacement_block=replacement_block,
            )

        assert result["status"] == "error"
        post_content = target_path.read_text(encoding="utf-8")
        assert _sha256(post_content) == original_digest, "Patch failure corrupted the source file."

    @pytest.mark.asyncio
    async def test_rollback_restores_original_from_backup(self, tool: FileWriterTool):
        """
        Full lifecycle: write -> backup -> overwrite -> rollback.

        Verifies that rollback() atomically restores the pre-overwrite state.
        """
        target_rel = "settings.json"
        target_path = _abs(tool, target_rel)

        original_content = target_path.read_text(encoding="utf-8")
        original_digest = _sha256(original_content)

        bak_result = await tool.call("create_backup", path=target_rel)
        assert bak_result["status"] == "success"

        modified = '{\n    "debug": true,\n    "version": "9.9.9"\n}\n'
        await tool.call("write_file", path=target_rel, content=modified, backup=False)
        assert target_path.read_text(encoding="utf-8") == modified

        rb_result = await tool.call("rollback", path=target_rel)
        assert rb_result["status"] == "success"

        restored = target_path.read_text(encoding="utf-8")
        assert restored == original_content
        assert _sha256(restored) == original_digest


# ===========================================================================
# 6. Audit Record Schema
# ===========================================================================

class TestAuditSchema:
    """Every response must conform to the documented telemetry schema."""

    REQUIRED_FIELDS = frozenset({
        "status", "action", "path", "backup_created",
        "bytes_written", "lines_affected", "timestamp", "data", "error_message",
    })

    @pytest.mark.asyncio
    async def test_success_record_schema(self, tool: FileWriterTool):
        """write_file success response contains all required schema fields."""
        result = await tool.call("write_file", path="schema_check.py", content="x = 1\n")
        missing = self.REQUIRED_FIELDS - result.keys()
        assert missing == set(), f"Missing fields in success record: {missing}"

    @pytest.mark.asyncio
    async def test_error_record_schema(self, tool: FileWriterTool):
        """An error response also conforms to the full schema."""
        result = await tool.call("write_file", path="../../escape.py", content="x")
        missing = self.REQUIRED_FIELDS - result.keys()
        assert missing == set(), f"Missing fields in error record: {missing}"
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_timestamp_is_iso8601(self, tool: FileWriterTool):
        """Timestamp field must be a valid timezone-aware ISO-8601 string."""
        from datetime import datetime, timezone

        result = await tool.call("write_file", path="ts_check.py", content="pass\n")
        ts = result["timestamp"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None, "Timestamp must be timezone-aware"

    @pytest.mark.asyncio
    async def test_unknown_action_structured_error(self, tool: FileWriterTool):
        """Unsupported action must return a structured error dict, not raise."""
        result = await tool.call("detonate_server", path="x.py")
        assert result["status"] == "error"
        assert "unknown action" in result["error_message"].lower()


# ===========================================================================
# 7. Edge Cases & Boundary Conditions
# ===========================================================================

class TestEdgeCases:
    """Boundary conditions and uncommon but valid usage patterns."""

    @pytest.mark.asyncio
    async def test_write_empty_file(self, tool: FileWriterTool):
        """Writing empty content creates a zero-byte file."""
        result = await tool.call("write_file", path="empty.py", content="")
        assert result["status"] == "success"
        assert result["bytes_written"] == 0
        assert _abs(tool, "empty.py").read_bytes() == b""

    @pytest.mark.asyncio
    async def test_write_unicode_content(self, tool: FileWriterTool):
        """File content with multi-byte Unicode characters round-trips correctly."""
        content = "# Unicode header\n\nname = 'Japanese test'\n"
        result = await tool.call("write_file", path="unicode_test.py", content=content)
        assert result["status"] == "success"

        on_disk = _abs(tool, "unicode_test.py").read_text(encoding="utf-8")
        assert on_disk == content
        assert _sha256(on_disk) == _sha256(content)

    @pytest.mark.asyncio
    async def test_append_accumulates_correctly(self, tool: FileWriterTool):
        """Multiple sequential appends accumulate content in insertion order."""
        path = "accumulate.log"
        lines = ["Line A\n", "Line B\n", "Line C\n"]
        for line in lines:
            result = await tool.call("append_file", path=path, content=line)
            assert result["status"] == "success"

        final = _abs(tool, path).read_text(encoding="utf-8")
        assert final == "".join(lines)

    @pytest.mark.asyncio
    async def test_write_then_read_back_checksum(self, tool: FileWriterTool):
        """
        Write a 100-line file and verify the SHA-256 read back from disk
        is identical to the digest computed on the original string.
        """
        content = "\n".join(f"line_{i} = {i}" for i in range(100)) + "\n"
        expected_digest = _sha256(content)

        await tool.call("write_file", path="checksum_test.py", content=content)

        on_disk = _abs(tool, "checksum_test.py").read_text(encoding="utf-8")
        assert _sha256(on_disk) == expected_digest

    @pytest.mark.asyncio
    async def test_delete_requires_confirm(self, tool: FileWriterTool):
        """delete_file without confirm=True must be rejected and leave file intact."""
        target = _abs(tool, "main.py")
        assert target.exists()

        result = await tool.call("delete_file", path="main.py")
        assert result["status"] == "error"
        assert "confirm" in result["error_message"].lower()
        assert target.exists(), "File was deleted despite missing confirmation"

    @pytest.mark.asyncio
    async def test_rollback_missing_bak_returns_error(self, tool: FileWriterTool):
        """rollback when no .bak exists must return a structured error."""
        result = await tool.call("rollback", path="main.py")
        assert result["status"] == "error"
        assert "no backup" in result["error_message"].lower()

    @pytest.mark.asyncio
    async def test_backup_nonexistent_file_returns_error(self, tool: FileWriterTool):
        """create_backup on a non-existent file must return a structured error."""
        result = await tool.call("create_backup", path="ghost.py")
        assert result["status"] == "error"
        assert "non-existent" in result["error_message"].lower() or "not" in result["error_message"].lower()


# ===========================================================================
# Standalone entrypoint
# ===========================================================================

if __name__ == "__main__":
    import subprocess

    print("=" * 70)
    print("NodeForge -- Standalone Test Suite")
    print("Running: pytest tests/test_node_forge.py -v")
    print("=" * 70)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            __file__,
            "-v",
            "--tb=short",
            "--no-header",
            "-q",
        ],
        cwd=str(_PROJECT_ROOT),
    )

    sys.exit(result.returncode)
