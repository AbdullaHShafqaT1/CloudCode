# LegacyBridge ↔ NodeInsight Integration

## Directory Structure

```
CloudCode/
├── LegacyBridge/
│   └── legacynode/
│       ├── bridge_adapter.py          ← BridgeAdapter.reader_call() added here
│       ├── core/
│       │   ├── agent_controller.py    (unchanged)
│       │   ├── llm_client.py          (unchanged)
│       │   ├── notification_hub.py    (unchanged)
│       │   └── state_manager.py       (unchanged)
│       └── tools/
│           ├── __init__.py            ← NodeInsightBridge added to exports
│           ├── file_reader.py         (unchanged — used by AgentController)
│           ├── file_writer.py         (unchanged)
│           ├── node_insight_bridge.py ← NEW: thin adapter to NodeInsight
│           └── terminal_executor.py   (unchanged)
│
├── NodeInsight/                       ← ENTIRELY UNCHANGED
│   ├── node_insight.py
│   ├── file_reader_tool/
│   │   ├── __init__.py
│   │   ├── file_reader_tool.py        ← FileReaderTool / NodeInsight class
│   │   └── models.py
│   └── tests/
│
├── test_integration_nodeinsight.py    ← NEW: integration smoke test (project root)
└── INTEGRATION.md                     ← this file
```

## Responsibility Split

| Module | Responsible for |
|--------|----------------|
| **LegacyBridge** | Session management, task dispatch, LLM/cloud communication, `BridgeAdapter`, `AgentController` |
| **NodeInsight** | Local file reading, text parsing, AST analysis, symbol extraction, path-traversal sandboxing |
| **NodeInsightBridge** | Thin glue — forwards `(action, **kwargs)` from LegacyBridge to NodeInsight; no path resolution of its own |

## How LegacyBridge Calls NodeInsight

### Primary interface (synchronous — for Streamlit/scripts)

```python
from legacynode.bridge_adapter import BridgeAdapter, BridgeMode

adapter = BridgeAdapter(
    mode=BridgeMode.LOCAL,
    workspace_root="/path/to/project",
)

# Read a file
result = adapter.reader_call("read_file", path="src/main.py")
if result.ok:
    content = result.data["data"]["content"]
else:
    print("Error:", result.error)

# Parse AST — "Inspect functions in sample_module.py"
result = adapter.reader_call("parse_ast", path="src/sample_module.py")
if result.ok:
    for fn in result.data["data"]["functions"]:
        print(fn["name"], fn["signature"])

# List directory
result = adapter.reader_call("list_dir", path="src", recursive=True)

# Search for a symbol across the project
result = adapter.reader_call("search_symbols", query="Calculator", path=".")

# Read structured data (JSON / YAML / Markdown)
result = adapter.reader_call("read_structured", path="config.json")
```

### Direct adapter (async — for coroutines)

```python
from legacynode.tools.node_insight_bridge import NodeInsightBridge

bridge = NodeInsightBridge(workspace_root="/path/to/project")
tool_resp = await bridge.call("parse_ast", path="src/main.py")
# tool_resp is the raw NodeInsight ToolResponse dict
```

## Request / Response Format

### Request

```python
adapter.reader_call(
    action="read_file",          # str — one of the actions below
    workspace_root=None,         # optional override; defaults to adapter.workspace_root
    path="src/main.py",          # action-specific kwargs forwarded to NodeInsight
    start_line=1,
    end_line=50,
    line_numbers=True,
)
```

**Available actions:**

| Action | Required kwargs | Description |
|--------|----------------|-------------|
| `read_file` | `path` | Read file content (full or line range) |
| `read_file_chunked` | `path` | Paginated read for large files |
| `parse_ast` | `path` | Extract classes, functions, imports via Python AST |
| `list_dir` | `path` | List directory contents with optional recursion |
| `search_files` | `path`, `query` | Find files by name or glob pattern |
| `search_in_file` | `path`, `pattern` | Search for literal string or regex within a file |
| `search_symbols` | `query` | Find class/function/method symbols across Python files |
| `read_structured` | `path` | Parse JSON / YAML / Markdown into structured data |

### Response

`BridgeResult` returned by `adapter.reader_call()`:

```python
@dataclass
class BridgeResult:
    ok: bool            # True = success, False = error
    data: dict          # Contains the NodeInsight ToolResponse dict:
                        #   {"status": "success"|"error",
                        #    "data": <payload> | null,
                        #    "error_message": "<ErrorCode: detail>" | null}
    error: str | None   # Shortcut: same as data["error_message"] when ok=False
    latency_ms: float
    timestamp: str
```

**Success example:**

```json
{
  "ok": true,
  "data": {
    "status": "success",
    "data": {
      "path": "src/sample_module.py",
      "classes": [{"name": "Greeter", ...}],
      "functions": [{"name": "main", ...}],
      "imports": [...],
      "import_graph": ["os", "typing"]
    },
    "error_message": null
  },
  "error": null,
  "latency_ms": 12.4
}
```

**Error example:**

```json
{
  "ok": false,
  "data": {
    "status": "error",
    "data": null,
    "error_message": "FileNotFound: File not found: 'src/ghost.py'"
  },
  "error": "FileNotFound: File not found: 'src/ghost.py'",
  "latency_ms": 1.1
}
```

**Structured error codes** (from `NodeInsight/file_reader_tool/models.py`):

| Code | Meaning |
|------|---------|
| `FileNotFound` | Path does not exist |
| `PathTraversal` | Path escapes workspace sandbox |
| `BinaryFile` | File is binary; text read refused |
| `FileTooLarge` | File exceeds size limit |
| `IsADirectory` | Expected a file, got a directory |
| `NotADirectory` | Expected a directory, got a file |
| `ParseError` | AST/JSON/YAML syntax error |
| `InvalidAction` | Unknown action name |
| `InvalidArgument` | Wrong keyword argument |
| `PermissionDenied` | OS-level read permission denied |

## Running the Local Integration

### Prerequisites

Install both modules' dependencies (from project root):

```powershell
# NodeInsight dependencies
pip install aiofiles structlog chardet

# LegacyBridge dependencies (for BridgeAdapter)
pip install aiofiles structlog python-dotenv

# Test runner
pip install pytest pytest-asyncio
```

### Run the integration smoke test

```powershell
# From CloudCode/ (project root)
pytest test_integration_nodeinsight.py -v --tb=short
```

### Run all tests (NodeInsight + integration)

```powershell
# NodeInsight standalone
cd NodeInsight
pytest test_node_insight.py tests/ -v --tb=short
cd ..

# Integration
pytest test_integration_nodeinsight.py -v --tb=short
```

## Switching to Remote Mode

`reader_call()` **always delegates to local NodeInsight** regardless of the
`BridgeMode`. Only the LLM/agent component communicates remotely.

```python
adapter = BridgeAdapter(
    mode=BridgeMode.REMOTE,
    workspace_root="/path/to/project",
    tunnel_url="https://your-cloudflare-tunnel.trycloudflare.com",
    llm_model="qwen2.5-coder:32b",
)
# reader_call() still reads from the local filesystem via NodeInsight
result = adapter.reader_call("parse_ast", path="src/main.py")
```

The `tunnel_url` and `llm_model` only affect `initialize_session()`,
`health_check()`, and `submit_task()` — not `reader_call()`.

## Security Notes

- NodeInsight's path-traversal sandbox (`_safe_path()`) is **not bypassed**.
- `NodeInsightBridge` performs **zero** path resolution of its own.
- Requests like `../../etc/passwd` are rejected inside NodeInsight and
  propagated back as `PathTraversal` errors through `BridgeResult`.
- The `workspace_root` passed to `NodeInsightBridge` acts as the security
  boundary; all paths are resolved relative to it.
