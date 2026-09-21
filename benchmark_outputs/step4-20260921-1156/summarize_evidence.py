"""Summarize retained real-run records without interpreting them as passes."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def main():
    summary = {"runs": {}, "config_sha256": hashlib.sha256((ROOT / "orchestrator_config.json").read_bytes()).hexdigest()}
    for name in ("control", "control-retest", "chess"):
        path = OUT / f"{name}-result.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        meta = json.loads((OUT / f"{name}-metadata.json").read_text(encoding="utf-8"))
        requests = [json.loads(line) for line in (OUT / f"{name}-http.jsonl").read_text(encoding="utf-8").splitlines()]
        events = [json.loads(line) for line in (OUT / f"{name}-events.jsonl").read_text(encoding="utf-8").splitlines()]
        independent = OUT / f"{name}-independent-final.json"
        summary["runs"][name] = {
            "workflow_status": result["status"], "reason": result["termination_reason"],
            "phase": result["current_phase"], "completed_phases": result["completed_phases"],
            "rounds": result["rounds"], "response_count": result["responses_received"],
            "actual_http_requests": len(requests),
            "actual_http_responses_with_choices": sum(bool(item.get("response", {}).get("choices")) for item in requests),
            "http_codes": [item.get("http_status") for item in requests],
            "elapsed_seconds": meta["elapsed_seconds"], "prompt_file": meta["prompt_file"],
            "files_read": result["files_read"], "files_created": result["files_created"],
            "files_modified": result["files_modified"], "final_files": result["files_actual"],
            "commands": result["commands"], "verification": result["verification"],
            "remaining_requirements": result["remaining_requirements"],
            "phase_transitions": [item for item in events if item["event"] == "PHASE_TRANSITION"],
            "run_record": result["report_path"], "checker_unchanged": meta["checker_unchanged"],
            "independent": json.loads(independent.read_text()) if independent.exists() else None,
        }
    (OUT / "evidence-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for name, data in summary["runs"].items():
        print(name, data["workflow_status"], "rounds", data["rounds"], "real HTTP responses", data["actual_http_responses_with_choices"])


if __name__ == "__main__":
    main()
