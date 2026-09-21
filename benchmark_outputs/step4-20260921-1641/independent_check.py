"""Run frozen acceptance against final files and retain exact exit/output/hash evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()
            and not any(part.startswith(".") or part in {"__pycache__", "nodelog_data"} for part in p.relative_to(root).parts)
            and not p.name.endswith(".bak")}


def main():
    run_id = sys.argv[1]
    if run_id not in {"control", "control-retest", "chess"}:
        raise ValueError("Unknown benchmark run")
    kind = run_id.split("-")[0]
    workspace = OUT / run_id
    before = snapshot(workspace)
    checker = OUT / f"{kind}_acceptance.py"
    command = [sys.executable, str(checker), str(workspace)]
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    after = snapshot(workspace)
    log_path = OUT / f"{run_id}-independent-final.log"
    log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "command": command,
              "exit_code": result.returncode, "elapsed_seconds": round(time.monotonic()-started,3),
              "files_before": before, "files_after": after, "files_unchanged": before == after,
              "checker_sha256": hashlib.sha256(checker.read_bytes()).hexdigest(), "output_log": str(log_path),
              "passed": result.returncode == 0 and before == after}
    (OUT / f"{run_id}-independent-final.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
