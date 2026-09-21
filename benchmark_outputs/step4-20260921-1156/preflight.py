"""Read-only live preflight. Records no authentication headers or credentials."""
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "NodeCore"))
from launcher_gui import load_config
from node_core.core import create_cloud_llm_config


def request(url, payload=None):
    started = time.monotonic()
    record = {"url": url, "method": "POST" if payload else "GET", "started_at": datetime.now(timezone.utc).isoformat()}
    headers = {"User-Agent": "CloudCode-Step4-Preflight", "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload else None, headers=headers)
        with urllib.request.urlopen(req, timeout=35 if payload else 15) as response:
            body = response.read(65536).decode("utf-8", errors="replace")
            record.update(http_status=response.status)
            try:
                data = json.loads(body)
                if payload:
                    record.update(response_model=data.get("model"), response_id=data.get("id"),
                                  choices=data.get("choices"), usage=data.get("usage"))
                else:
                    record["model_ids"] = [item.get("id") for item in data.get("data", [])]
            except ValueError:
                record.update(parse_error="Response was not JSON", body_sha256=hashlib.sha256(body.encode()).hexdigest())
    except urllib.error.HTTPError as exc:
        body = exc.read(65536).decode("utf-8", errors="replace")
        record.update(http_status=exc.code, error=str(exc), body_sha256=hashlib.sha256(body.encode()).hexdigest())
        # Cloudflare's plain numeric error code is diagnostic and contains no secrets.
        import re
        found = re.search(r"error code:\s*(\d+)", body, re.I)
        if found:
            record["cloudflare_error_code"] = found.group(1)
    except Exception as exc:
        record.update(error_type=type(exc).__name__, error=str(exc))
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if payload:
        record["request_payload"] = payload
    return record


def main():
    cfg = load_config(); cfg["model"] = "qwen2.5-coder:32b"  # User-approved run override
    base = "https://str-resolution-neck-varying.trycloudflare.com"
    api = base if base.endswith("/v1") else base + "/v1"
    llm = create_cloud_llm_config(base, cfg["model"])
    configuration = ROOT / "orchestrator_config.json"
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(), "local_time": datetime.now().astimezone().isoformat(),
        "os": platform.platform(), "python": sys.version, "python_executable": sys.executable,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).splitlines(),
        "configuration_sha256_before": hashlib.sha256(configuration.read_bytes()).hexdigest(),
        "endpoint": base, "model": cfg["model"], "model_response_budget": cfg["max_rounds"],
        "request_timeout_seconds": llm["timeout"], "max_output_tokens": llm["max_tokens"],
        "temperature": llm["temperature"], "openai_sdk_default_max_retries": 2,
        "terminal_default_timeout_seconds": 60, "stall_limit": 4,
        "endpoint_override_environment_present": bool(os.environ.get("TUNNEL_URL")),
        "requested_mode": "LIVE", "mock_model_or_response_used": False,
        "benchmark_output": str(OUT), "control_workspace": str(OUT / "control"), "chess_workspace": str(OUT / "chess"),
        "versions": {name: importlib.metadata.version(name) for name in ("pyautogen", "openai", "pytest")},
        "requests": [], "benchmark_user_prompts_submitted": 0, "benchmark_model_responses": 0,
        "benchmarks_started": False,
    }
    report["requests"].append(request(api + "/models"))
    models = report["requests"][-1]
    if models.get("http_status") == 200 and cfg["model"] in models.get("model_ids", []):
        report["requests"].append(request(api + "/chat/completions", {
            "model": cfg["model"], "messages": [{"role": "user", "content": "Reply exactly: LIVE_PREFLIGHT_OK"}],
            "max_tokens": 20, "temperature": 0,
        }))
        chat = report["requests"][-1]
        report["ready"] = chat.get("http_status") == 200 and bool(chat.get("choices"))
        report["reason"] = "Live completion received" if report["ready"] else "Live completion preflight failed"
    else:
        report["ready"] = False
        report["reason"] = "Configured endpoint unavailable" if models.get("http_status") != 200 else "Configured model identifier not advertised; no substitution attempted"
    report["configuration_sha256_after"] = hashlib.sha256(configuration.read_bytes()).hexdigest()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "preflight.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
