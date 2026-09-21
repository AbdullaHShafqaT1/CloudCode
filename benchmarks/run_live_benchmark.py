"""Opt-in live test. Writes only to the explicitly selected benchmark workspace."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "NodeCore"))
from node_core.core import NodeCore, create_cloud_llm_config
from node_core.workflow import python_command

PROMPT = """Build a complete two-human-player tic-tac-toe game with a browser GUI, no AI opponent.
Use only Python standard library and embedded HTML/CSS/JavaScript; no external dependencies.
Required files: app_logic.py, app_gui.py, main.py, test_app.py, RunningGUIDE.txt.
app_logic.py exports Game with board (3x3 list of empty strings or X/O), turn ('X' initially),
winner (None or X/O), draw (bool), move(row,col), reset(). move raises ValueError on illegal,
occupied, out-of-range or post-game moves. Alternate turns, detect all row/column/diagonal
wins for either player and draws. Reset clears everything. No automatic computer moves.
app_gui.py exports create_server(host, port) returning a standard-library HTTPServer.
GET / serves a usable graphical 3x3 board, with button IDs cell-0-0 through cell-2-2,
a status element id=status and reset button id=reset. Clicking submits fetch('/move',...)
and renders returned state. GET /state returns JSON {board,turn,winner,draw}.
POST /move accepts JSON {row,col}, returns state JSON or HTTP 400 JSON on invalid moves.
POST /reset resets and returns state JSON. Display whose turn it is, winner, or draw.
main.py runs create_server('127.0.0.1',8080).serve_forever() only under a __main__ guard.
All modules must import without launching servers. Headless unittest suite exercises game
rules and error cases. Guide explains python main.py and http://127.0.0.1:8080, controls,
rules and stopping the server. The independent acceptance checker will test this contract.
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--max-rounds", type=int, default=12)
    args = parser.parse_args()
    ws = Path(args.workspace).resolve()
    acceptance = {"required_files": ["app_logic.py", "app_gui.py", "main.py", "test_app.py", "RunningGUIDE.txt"],
                  "checks": [{"requirement": "Two-player rules, GUI delivery and running HTTP integration",
                              "command": python_command(str(ROOT / "benchmarks" / "two_player_acceptance.py"))}]}
    config = create_cloud_llm_config(args.url)
    config["cache_seed"] = None
    config["config_list"][0]["max_retries"] = 0
    result = NodeCore(workspace_root=str(ws), llm_config=config).start_task(
        PROMPT, max_rounds=args.max_rounds, acceptance=acceptance)
    (ws / ".cloudcode" / "benchmark_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("BENCHMARK_RESULT", json.dumps({k: result[k] for k in ("status", "rounds", "termination_reason", "report_path")}))
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
