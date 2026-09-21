"""Read-only final generated-test and exact launch diagnostics; no model calls."""
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from independent_check import snapshot

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
records = []
for name in ("chess",):
    workspace = OUT / name
    before = snapshot(workspace)
    evidence = OUT / f"{name}-generated-tests.json"
    command = [sys.executable, "-I", str(ROOT / "NodeCore/node_core/verification_runner.py"), str(workspace), str(evidence)]
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=30)
    log = OUT / f"{name}-generated-tests.log"
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    records.append({"run": name, "command": command, "cwd": str(workspace), "exit_code": result.returncode,
                    "result": json.loads(evidence.read_text()), "log": str(log), "files_unchanged": before == snapshot(workspace)})
workspace = OUT / "chess"
before = snapshot(workspace)
command = [sys.executable, "main.py", "--host", "127.0.0.1", "--port", "8765"]
result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=10)
log = OUT / "chess-required-launch.log"
log.write_text(result.stdout + result.stderr, encoding="utf-8")
records.append({"run": "chess", "purpose": "Exact requested launch command; generated documentation contains no complete command", "command": command,
                "cwd": str(workspace), "exit_code": result.returncode, "log": str(log), "files_unchanged": before == snapshot(workspace)})
(OUT / "final-diagnostics.json").write_text(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(), "records": records}, indent=2), encoding="utf-8")
for item in records:
    print(item["run"], "exit", item["exit_code"], item.get("result", item.get("purpose")), "unchanged", item["files_unchanged"])
