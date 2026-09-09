# NodeForge — FileWriterTool

A safe, audited, atomic, and granular file write/modification tool for the NodeForge AI controller.

---

## Features

| Capability | Method | Action String |
|---|---|---|
| Create / overwrite file | `write_file` | `"write_file"` |
| Diff / line-level patch | `patch_file` | `"patch_file"` |
| Append content | `append_file` | `"append_file"` |
| Create directories | `create_dir` | `"create_dir"` |
| Safe file deletion | `delete_file` | `"delete_file"` |
| Rollback from backup | `rollback` | `"rollback"` |

---

## Installation & Setup

```bash
pip install pytest pytest-asyncio
```

No third-party runtime dependencies — only the Python standard library.

---

## Quick Start

```python
import asyncio
from tools.file_writer_tool import FileWriterTool

# Scope the tool to your project root
tool = FileWriterTool(workspace_root="/path/to/project")

async def main():
    # --- Write a new file ---
    result = await tool.call(
        "write_file",
        path="src/main.py",
        content="print('hello, NodeForge')\n",
    )
    print(result)
    # {
    #   "status": "success",
    #   "action": "write_file",
    #   "path": "src/main.py",
    #   "backup_created": null,
    #   "bytes_written": 28,
    #   "lines_affected": null,
    #   "timestamp": "2026-08-29T18:27:00+00:00",
    #   "data": "File written successfully.",
    #   "error_message": null
    # }

    # --- Patch a unique block ---
    result = await tool.call(
        "patch_file",
        path="src/main.py",
        target_block="print('hello, NodeForge')\n",
        replacement_block="print('hello, world')\n",
    )

    # --- Append to a log file ---
    result = await tool.call(
        "append_file",
        path="logs/run.log",
        content="[INFO] Agent started.\n",
    )

    # --- Rollback to last .bak ---
    result = await tool.call("rollback", path="src/main.py")

    # --- Safe delete (requires explicit confirmation) ---
    result = await tool.call("delete_file", path="tmp/scratch.py", confirm=True)

asyncio.run(main())
```

---

## API Reference

### `FileWriterTool(workspace_root)`

| Parameter | Type | Description |
|---|---|---|
| `workspace_root` | `str \| Path` | Absolute root path. All operations are confined to this directory. |

---

### `call(action, **kwargs) -> dict`

Unified async dispatcher. Returns a structured telemetry dict for every call.

#### Common Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `action` | `str` | — | Operation name (see table above) |
| `path` | `str` | — | Target file/dir path, relative to `workspace_root` |
| `backup` | `bool` | `True` | Create `.bak` before modifying (write/patch/delete) |

#### `write_file` — specific parameters

| Parameter | Type | Description |
|---|---|---|
| `content` | `str` | Full file content to write |

#### `patch_file` — specific parameters

| Parameter | Type | Description |
|---|---|---|
| `target_block` | `str` | Unique text block to locate (must appear exactly once) |
| `replacement_block` | `str` | Replacement text |

#### `append_file` — specific parameters

| Parameter | Type | Description |
|---|---|---|
| `content` | `str` | Text to append |

#### `delete_file` — specific parameters

| Parameter | Type | Description |
|---|---|---|
| `confirm` | `bool` | **Must be `True`** to authorise deletion |

---

### Response Schema

```json
{
  "status":        "success",
  "action":        "write_file",
  "path":          "src/main.py",
  "backup_created": "src/main.py.bak",
  "bytes_written": 1420,
  "lines_affected": null,
  "timestamp":     "2026-08-29T18:27:00+00:00",
  "data":          "File written successfully.",
  "error_message": null
}
```

On error, `status` is `"error"` and `error_message` contains the human-readable reason. No exceptions are raised through `call()`.

---

## Security

- **Workspace isolation**: Every path is resolved to an absolute path and checked against `workspace_root`. Traversal attempts (`../../`) raise a `SecurityError` (subclass of `PermissionError`) — or return a structured error via `call()`.
- **Atomic writes**: All write and rollback operations use a temp-file + `os.replace()` pattern. A partial write never corrupts the target file.
- **Mandatory confirmation for deletion**: `delete_file` requires `confirm=True` to prevent accidental removal.

---

## Running Tests

```bash
# From the NodeForge project root:
pytest tests/ -v
```

Expected output (all 16 tests passing):

```
tests/test_file_writer_tool.py::test_write_new_file                    PASSED
tests/test_file_writer_tool.py::test_write_overwrites_with_backup      PASSED
tests/test_file_writer_tool.py::test_write_creates_parent_dirs         PASSED
tests/test_file_writer_tool.py::test_write_no_backup_when_flag_false   PASSED
tests/test_file_writer_tool.py::test_patch_file_success                PASSED
tests/test_file_writer_tool.py::test_patch_file_not_found              PASSED
tests/test_file_writer_tool.py::test_patch_file_ambiguous              PASSED
tests/test_file_writer_tool.py::test_append_file                       PASSED
tests/test_file_writer_tool.py::test_append_creates_file_if_missing    PASSED
tests/test_file_writer_tool.py::test_rollback_restores_original        PASSED
tests/test_file_writer_tool.py::test_rollback_no_bak_returns_error     PASSED
tests/test_file_writer_tool.py::test_delete_without_confirm_rejected   PASSED
tests/test_file_writer_tool.py::test_delete_with_confirm               PASSED
tests/test_file_writer_tool.py::test_path_traversal_blocked_write      PASSED
tests/test_file_writer_tool.py::test_path_traversal_blocked_delete     PASSED
tests/test_file_writer_tool.py::test_path_traversal_direct_raises      PASSED
tests/test_file_writer_tool.py::test_create_dir                        PASSED
tests/test_file_writer_tool.py::test_create_dir_idempotent             PASSED
tests/test_file_writer_tool.py::test_audit_record_has_required_fields  PASSED
tests/test_file_writer_tool.py::test_unknown_action_returns_error      PASSED
```
