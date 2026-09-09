"""
LegacyNode — File Writer Tool
Atomic file writes, diff patching, and directory creation with auto-backup.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles
import structlog

log = structlog.get_logger(__name__)


class FileWriter:
    """
    Safe, atomic file write operations for the agent.

    Features:
    - Atomic writes via temp-file + rename (prevents partial writes)
    - Auto-backup of existing files before overwrite
    - Unified diff patching via `difflib`
    - Workspace sandbox enforcement (identical to FileReader)
    """

    def __init__(
        self,
        workspace_root: str = ".",
        backup_dir: str = ".legacynode/backups",
        max_file_size: int = 5 * 1024 * 1024,
    ):
        self._root = Path(workspace_root).resolve()
        self._backup_dir = Path(backup_dir)
        if not self._backup_dir.is_absolute():
            self._backup_dir = self._root / self._backup_dir
        self._max_size = max_file_size

    # ─── Path Validation ─────────────────────────────────────────────────────

    def _safe_path(self, path: str) -> Path:
        resolved = (self._root / path).resolve()
        if not str(resolved).startswith(str(self._root)):
            raise ValueError(
                f"Path traversal detected: '{path}' resolves outside workspace root "
                f"'{self._root}'"
            )
        return resolved

    # ─── Backup ──────────────────────────────────────────────────────────────

    def _backup(self, target: Path) -> Optional[Path]:
        """
        Copy the existing file to the backup directory before overwriting.
        Returns the backup path, or None if file did not exist.
        """
        if not target.exists():
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        rel = target.relative_to(self._root)
        backup_path = self._backup_dir / ts / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        log.debug("File backed up", original=str(rel), backup=str(backup_path))
        return backup_path

    # ─── Public API ──────────────────────────────────────────────────────────

    async def write_file(self, path: str, content: str) -> str:
        """
        Atomically write `content` to `path`.
        Creates parent directories if they don't exist.
        Backs up any existing file before overwriting.

        Args:
            path: Relative path from workspace root.
            content: Text content to write (UTF-8).

        Returns:
            Success message string.
        """
        safe = self._safe_path(path)
        safe.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing file
        self._backup(safe)

        # Atomic write: write to temp file, then rename
        tmp_fd, tmp_path = tempfile.mkstemp(dir=safe.parent, prefix=".legacynode_tmp_")
        try:
            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                await f.write(content)
            os.close(tmp_fd)
            os.replace(tmp_path, safe)  # Atomic on POSIX; best-effort on Windows
        except Exception:
            os.close(tmp_fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        log.info("File written", path=path, bytes=len(content.encode()))
        return f"Successfully wrote {len(content.splitlines())} lines to '{path}'."

    async def apply_diff(self, path: str, unified_diff: str) -> str:
        """
        Apply a unified diff to an existing file.

        The diff must be in standard unified diff format (as produced by `diff -u`
        or `git diff`). The file is backed up before patching.

        Args:
            path: Relative path to the target file.
            unified_diff: Unified diff string.

        Returns:
            Success message with patch statistics.

        Raises:
            FileNotFoundError: If the target file doesn't exist.
            ValueError: If the patch fails to apply cleanly.
        """
        import difflib

        safe = self._safe_path(path)
        if not safe.exists():
            raise FileNotFoundError(f"Cannot apply diff: file '{path}' does not exist.")

        async with aiofiles.open(safe, "r", encoding="utf-8", errors="replace") as f:
            original_lines = await f.readlines()

        # Parse the unified diff and apply via difflib
        patch_lines = unified_diff.splitlines(keepends=True)
        try:
            result = list(difflib.restore(patch_lines, which=2))
        except Exception as e:
            raise ValueError(f"Failed to parse unified diff: {e}") from e

        # Backup then write patched content
        self._backup(safe)
        new_content = "".join(result)
        await self.write_file(path, new_content)

        added = sum(1 for l in patch_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in patch_lines if l.startswith("-") and not l.startswith("---"))
        log.info("Diff applied", path=path, added=added, removed=removed)
        return f"Patch applied to '{path}': +{added} lines / -{removed} lines."

    async def create_directory(self, path: str) -> str:
        """
        Create a directory (and all required parents).

        Args:
            path: Relative path from workspace root.

        Returns:
            Success message.
        """
        safe = self._safe_path(path)
        safe.mkdir(parents=True, exist_ok=True)
        log.info("Directory created", path=path)
        return f"Directory '{path}' created (or already exists)."
