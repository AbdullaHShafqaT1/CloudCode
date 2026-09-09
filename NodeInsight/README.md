# FileReaderTool — NodeInsight

A self-contained, **read-only** filesystem inspection module for the **LegacyNode** AI controller.  
Part of the **CloudBridge** project family.

---

## Features

| Operation | Description |
|---|---|
| `list_dir` | Directory exploration with optional recursion, depth limit, and extension filtering |
| `search_files` | Glob / keyword name search across the project hierarchy |
| `read_file` | Full or line-range text read, with optional line-number prefixes |
| `read_file_chunked` | Paginated reading for large files (avoids memory overflow) |
| `search_in_file` | Literal string / regex search within a file, with context lines |

### Safety Guarantees

- **Read-only**: No write, delete, or chmod operations exist anywhere in this module.
- **Path traversal protection**: Every path is resolved and checked against the workspace root before any I/O.
- **Binary file detection**: Null-byte sniff prevents garbled output from binary files.
- **File size guard**: Configurable limit (default 10 MB) with a helpful error directing to chunked reads.
- **Structured errors**: All failures return `{status: "error", error_message: "ErrorCode: detail"}` — no exceptions leak to the caller.

---

## Installation

```bash
cd NodeInsight
pip install -r requirements.txt
```

> **Optional**: Install `chardet` for improved encoding detection on non-UTF-8 files:
> ```bash
> pip install chardet
> ```

---

## Quick Start

```python
import asyncio
from file_reader_tool import FileReaderTool

tool = FileReaderTool(workspace_root="/path/to/your/project")

async def main():
    # 1. Explore the project structure
    result = await tool.call("list_dir", path=".", recursive=True, max_depth=3)
    print(result["data"]["tree"])

    # 2. Find all Python files
    result = await tool.call("search_files", path=".", query="*.py")
    for match in result["data"]["matches"]:
        print(match["path"], match["size"])

    # 3. Read a file with line numbers
    result = await tool.call("read_file", path="src/main.py", line_numbers=True)
    print(result["data"]["content"])

    # 4. Read a large file in chunks
    offset = 1
    while True:
        result = await tool.call("read_file_chunked", path="big.log", offset_line=offset)
        print(result["data"]["content"])
        if not result["data"]["has_more"]:
            break
        offset = result["data"]["next_offset"]

    # 5. Search for a pattern inside a file
    result = await tool.call(
        "search_in_file",
        path="src/main.py",
        pattern=r"def \w+\(",
        use_regex=True,
        context_lines=2,
    )
    for match in result["data"]["matches"]:
        print(f"Line {match['line_number']}: {match['line_content']}")

asyncio.run(main())
```

---

## API Reference

### `FileReaderTool(workspace_root, max_file_size)`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `workspace_root` | `str` | `"."` | Absolute or relative path to the project root |
| `max_file_size` | `int` | `10485760` | Max file size in bytes for text reads |

---

### `call(action, **kwargs) → dict`

Unified dispatcher. Always returns:

```json
{
  "status": "success" | "error",
  "data":   <payload or null>,
  "error_message": "<ErrorCode: detail>" | null
}
```

---

### Actions

#### `list_dir`

```python
await tool.call(
    "list_dir",
    path=".",                  # relative directory path
    recursive=False,           # descend into subdirectories
    max_depth=3,               # max recursion depth
    file_types=[".py", ".js"], # restrict to these extensions (None = all)
    include_hidden=False,      # include dotfiles
    include_ignored=False,     # include node_modules, .git, etc.
)
```

**Returns** `ListDirResult`:
```json
{
  "root": ".",
  "entries": [{"name": "src", "path": "src", "type": "directory", "size": 0, "modified": "..."}],
  "tree": "workspace/\n├── src/\n└── docs/",
  "total_files": 5,
  "total_dirs": 2
}
```

---

#### `search_files`

```python
await tool.call(
    "search_files",
    path=".",       # search root
    query="*.py",   # glob pattern or substring keyword
    file_types=None,
    include_hidden=False,
    include_ignored=False,
)
```

**Returns** `SearchFilesResult`:
```json
{
  "query": "*.py",
  "matches": [{"name": "main.py", "path": "src/main.py", "size": 480, "modified": "..."}],
  "truncated": false
}
```

---

#### `read_file`

```python
await tool.call(
    "read_file",
    path="src/main.py",
    start_line=1,        # 1-indexed (None = first line)
    end_line=50,         # 1-indexed (None = last line)
    line_numbers=False,  # prepend "N: " to each line
    encoding=None,       # None = auto-detect
)
```

**Returns** `ReadFileResult`:
```json
{
  "path": "src/main.py",
  "content": "...",
  "start_line": 1,
  "end_line": 50,
  "total_lines": 200,
  "encoding": "utf-8",
  "size_bytes": 4096
}
```

---

#### `read_file_chunked`

```python
await tool.call(
    "read_file_chunked",
    path="large.log",
    chunk_size=200,   # lines per page
    offset_line=1,    # start from this line (use next_offset to paginate)
    encoding=None,
)
```

**Returns** `ChunkResult`:
```json
{
  "path": "large.log",
  "content": "...",
  "start_line": 1,
  "end_line": 200,
  "total_lines": 5000,
  "has_more": true,
  "next_offset": 201,
  "encoding": "utf-8"
}
```

---

#### `search_in_file`

```python
await tool.call(
    "search_in_file",
    path="src/main.py",
    pattern="def ",       # literal string or regex
    use_regex=False,
    context_lines=2,      # N lines before and after each match
    max_matches=200,
    encoding=None,
)
```

**Returns** `SearchInFileResult`:
```json
{
  "path": "src/main.py",
  "pattern": "def ",
  "use_regex": false,
  "matches": [
    {
      "line_number": 12,
      "line_content": "def calculate(x, y):",
      "context_before": ["# arithmetic", ""],
      "context_after": ["    return x + y", ""]
    }
  ],
  "total_matches": 1,
  "truncated": false
}
```

---

## Error Codes

| Code | Cause |
|---|---|
| `FileNotFound` | Path does not exist |
| `PermissionDenied` | OS-level access denied |
| `FileTooLarge` | Exceeds `max_file_size`; use `read_file_chunked` |
| `InvalidLineRange` | `start_line > end_line`, or out of bounds |
| `BinaryFile` | Null bytes detected; text read refused |
| `PathTraversal` | Resolved path escapes workspace root |
| `InvalidAction` | Unknown action string passed to `call()` |
| `InvalidArgument` | Bad argument type/value (e.g. invalid regex, chunk_size=0) |
| `NotADirectory` | `list_dir`/`search_files` called on a file path |
| `IsADirectory` | `read_file`/`read_file_chunked` called on a directory |

---

## Running Tests

```bash
cd NodeInsight
pytest tests/test_file_reader_tool.py -v --tb=short
```

The test suite covers **50+ cases** across all five operations, security constraints, edge cases, and the unified dispatcher.

---

## Project Layout

```
NodeInsight/
├── file_reader_tool/
│   ├── __init__.py          # Public API exports
│   ├── file_reader_tool.py  # Main implementation
│   └── models.py            # Typed response structures
├── tests/
│   ├── __init__.py
│   └── test_file_reader_tool.py
├── pytest.ini
├── requirements.txt
└── README.md
```
