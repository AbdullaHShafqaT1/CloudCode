"""
LegacyNode — File Reader Tool
Safe, sandboxed file reading with workspace boundary enforcement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Union

import aiofiles
import structlog

log = structlog.get_logger(__name__)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB default guard


class FileReader:
    """
    Provides read-only file system access for the agent.

    All paths are resolved relative to `workspace_root` and validated to ensure
    the agent cannot escape the sandbox via path traversal attacks (e.g. ../../etc/passwd).
    """

    def __init__(self, workspace_root: str = ".", max_file_size: int = MAX_FILE_SIZE):
        self._root = Path(workspace_root).resolve()
        self._max_size = max_file_size

    # ─── Path Validation ─────────────────────────────────────────────────────

    def _safe_path(self, path: str) -> Path:
        """
        Resolve a path relative to workspace_root.
        Raises ValueError if the resolved path escapes the workspace sandbox.
        """
        resolved = (self._root / path).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError(
                f"Path traversal detected: '{path}' resolves outside workspace root "
                f"'{self._root}'"
            )
        return resolved

    # ─── Public API ──────────────────────────────────────────────────────────

    async def read_file(self, path: str) -> str:
        """
        Read the full content of a single file.

        Args:
            path: Relative path to the file (from workspace root).

        Returns:
            File content as a UTF-8 string.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If path escapes workspace or file is too large.
        """
        safe = self._safe_path(path)

        if not safe.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not safe.is_file():
            raise IsADirectoryError(f"Path is a directory, not a file: {path}")

        size = safe.stat().st_size
        if size > self._max_size:
            raise ValueError(
                f"File '{path}' is {size / 1024:.1f} KB, exceeding the "
                f"{self._max_size // 1024} KB read limit."
            )

        async with aiofiles.open(safe, "r", encoding="utf-8", errors="replace") as f:
            content = await f.read()

        log.debug("File read", path=path, bytes=size)
        return content

    async def read_files(self, paths: list[str]) -> dict[str, Union[str, dict]]:
        """
        Read multiple files in parallel.

        Args:
            paths: List of relative file paths.

        Returns:
            Dict mapping each path to its content string,
            or an error dict `{"error": "..."}` if reading failed.
        """
        import asyncio

        async def _read_one(p: str) -> tuple[str, Union[str, dict]]:
            try:
                content = await self.read_file(p)
                return p, content
            except Exception as e:
                return p, {"error": str(e)}

        results = await asyncio.gather(*[_read_one(p) for p in paths])
        return dict(results)

    async def scan_tree(
        self,
        root: str = ".",
        max_depth: int = 4,
        exclude_patterns: Optional[list[str]] = None,
    ) -> str:
        """
        Recursively scan a directory and return a JSON tree structure.

        Args:
            root: Starting directory (relative to workspace root).
            max_depth: Maximum recursion depth.
            exclude_patterns: List of directory/file name patterns to skip.

        Returns:
            JSON string representing the file tree.
        """
        safe_root = self._safe_path(root)

        if not safe_root.exists():
            raise FileNotFoundError(f"Directory not found: {root}")

        if not safe_root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")

        default_excludes = {
            ".git", "__pycache__", ".venv", "venv", "node_modules",
            ".legacynode", ".mypy_cache", ".pytest_cache", "dist", "build",
        }
        excludes = set(exclude_patterns or []) | default_excludes

        def _build_tree(path: Path, depth: int) -> dict:
            if depth > max_depth:
                return {"name": path.name, "type": "...", "truncated": True}

            node: dict = {"name": path.name}
            if path.is_file():
                node["type"] = "file"
                node["size"] = path.stat().st_size
            elif path.is_dir():
                node["type"] = "directory"
                try:
                    children = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
                    node["children"] = [
                        _build_tree(child, depth + 1)
                        for child in children
                        if child.name not in excludes
                    ]
                except PermissionError:
                    node["children"] = []
                    node["error"] = "permission denied"
            return node

        tree = _build_tree(safe_root, depth=0)
        log.debug("Directory tree scanned", root=root, max_depth=max_depth)
        return json.dumps(tree, indent=2)


# Allow Optional import without circular
from typing import Optional
