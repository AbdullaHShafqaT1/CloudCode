import sys
import json
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "NodeCore"))


system_msg = """You are the Lead Architect Agent.
Your role is to analyze project requirements, inspect the repository structure using `scan_workspace` and `file_reader`,
and write an actionable execution blueprint with file paths and module contracts.
Decompose high-level directives into concrete implementation steps for DevAgent and PulseAgent.
"""

user_msg = """Target Directory: C:\\Users\\Acer\\Desktop\\projects\\CloudCodeTesting
Build a complete, production-style Kanban web application from scratch.
Requirements:
- FastAPI backend with SQLite authentication and task CRUD.
- Responsive frontend with HTML/JS/CSS.
- Run unit tests to verify backend routes.
Use your available tools (file_writer, terminal_executor, file_reader, scan_workspace) to inspect, write, and verify.
When all tasks and tests are verified, conclude with TERMINATE.
"""

payload = {
    "model": "qwen2.5-coder:32b",
    "messages": [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg}
    ]
}

req = urllib.request.Request(
    "https://min-referenced-celtic-fiscal.trycloudflare.com/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req) as res:
        print(res.read().decode("utf-8"))
except Exception as e:
    print("ERROR:", e)
