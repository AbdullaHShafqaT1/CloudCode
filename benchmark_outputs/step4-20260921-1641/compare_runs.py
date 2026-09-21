"""Compare completed attempts without modifying any generated application."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
OLD = OUT.parent / "step4-20260921-1156"
ROOT = OUT.parents[1]
def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
def traffic(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
old, new = traffic(OLD / "chess-http.jsonl"), traffic(OUT / "chess-http.jsonl")
def content(item):
    return item.get("response", {}).get("choices", [{}])[0].get("message", {}).get("content", "")
records = []
for index, item in enumerate(new):
    text = content(item)
    records.append({"response": index + 1, "http_status": item.get("http_status"),
                    "response_characters": len(text), "body_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "matches_previous_response": index < len(old) and text == content(old[index]),
                    "reported_finish_reason": item.get("response", {}).get("choices", [{}])[0].get("finish_reason")})
provenance = read_json(OUT / "rerun-provenance.json")
after = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in provenance["source_hashes_before"]}
result = read_json(OUT / "chess-result.json")
independent = read_json(OUT / "chess-independent-final.json")
summary = {"responses": records, "previous_response_count": len(old), "new_response_count": len(new),
           "previous_workflow_status": read_json(OLD / "chess-result.json")["status"],
           "new_workflow_status": result["status"],
           "source_and_configuration_unchanged": after == provenance["source_hashes_before"],
           "source_hashes_after": after,
           "prompt_unchanged": (OUT / "chess-prompt.txt").read_bytes() == (OLD / "chess-prompt.txt").read_bytes(),
           "checker_unchanged": (OUT / "chess_acceptance.py").read_bytes() == (OLD / "chess_acceptance.py").read_bytes(),
           "final_files_match_verified": result["files_actual"] == independent["files_before"] == independent["files_after"]}
(OUT / "comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k not in {"responses", "source_hashes_after"}}, indent=2))
