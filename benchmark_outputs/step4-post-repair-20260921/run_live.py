"""Benchmark observer around the real GUI/CLI-shared launcher worker.

No application files or model replies are supplied by this observer. It only
sets the approved run configuration and records actual traffic/events/results.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "NodeCore")]
import httpx
import launcher_gui
from node_core.tools import NodeLog
from node_core.workflow import python_command


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["control", "chess"])
    parser.add_argument("--retest", action="store_true", help="Use a separate clean workspace after the documented pipeline fix")
    args = parser.parse_args()
    preflight = json.loads((OUT / "preflight.json").read_text())
    if not preflight["ready"]:
        raise RuntimeError("Live preflight has not passed")
    kind = args.kind
    run_id = kind + ("-retest" if args.retest else "")
    workspace = OUT / run_id
    workspace.mkdir(exist_ok=False)
    prompt = (OUT / f"{kind}-prompt.txt").read_text(encoding="utf-8")
    checker = OUT / f"{kind}_acceptance.py"
    if not checker.is_file():
        raise RuntimeError("Independent acceptance suite is missing")
    (workspace / ".cloudcode").mkdir()
    acceptance = {"task": prompt, "required_files": ["README.md"], "checks": [{
        "requirement": f"Independent {kind} behavior and documented launch acceptance",
        "command": python_command(str(checker), str(workspace)),
    }]}
    (workspace / ".cloudcode" / "acceptance.json").write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
    original_factory = launcher_gui.create_cloud_llm_config
    def live_config(*a, **kw):
        config = original_factory(*a, **kw)
        config["cache_seed"] = None  # Live benchmark cannot be satisfied by response cache.
        return config
    launcher_gui.create_cloud_llm_config = live_config
    traffic = OUT / f"{run_id}-http.jsonl"
    events = OUT / f"{run_id}-events.jsonl"
    def append(path, data):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    send = httpx.Client.send
    def observed_send(client, request, *a, **kw):
        if request.url.host != "prescribed-douglas-donate-skins.trycloudflare.com":
            return send(client, request, *a, **kw)
        record = {"started_at": now(), "url": str(request.url), "method": request.method}
        try:
            record["request"] = json.loads(request.content)
        except (ValueError, RuntimeError):
            record["request"] = "Non-JSON body omitted"
        started = time.monotonic()
        try:
            response = send(client, request, *a, **kw)
            record["http_status"] = response.status_code
            record["response"] = json.loads(response.read())
            return response
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            append(traffic, record)
    httpx.Client.send = observed_send
    NodeLog.add_listener(lambda event, data: append(events, {"time": now(), "event": event, "data": data}))
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    started = time.monotonic()
    metadata = {"started_at": now(), "endpoint": preflight["endpoint"], "model": preflight["model"],
                "max_rounds": preflight["model_response_budget"], "cache_seed": None, "mode": "LIVE",
                "user_application_prompts": 1, "workspace": str(workspace), "manual_application_edits": 0,
                "entrypoint": "launcher_gui.run_autonomous_orchestrator (shared GUI/CLI worker)",
                "prompt_file": str(OUT / f"{kind}-prompt.txt"), "checker_sha256": hashlib.sha256(checker.read_bytes()).hexdigest()}
    (OUT / f"{run_id}-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    result = launcher_gui.run_autonomous_orchestrator(
        workspace_root=str(workspace), base_url=preflight["endpoint"], model=preflight["model"],
        task_prompt=prompt, max_rounds=preflight["model_response_budget"], acceptance=acceptance,
    )
    metadata.update(finished_at=now(), elapsed_seconds=round(time.monotonic()-started, 3),
                    checker_unchanged=metadata["checker_sha256"] == hashlib.sha256(checker.read_bytes()).hexdigest())
    (OUT / f"{run_id}-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (OUT / f"{run_id}-result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": result["status"], "rounds": result["rounds"], "elapsed": metadata["elapsed_seconds"]}))
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
