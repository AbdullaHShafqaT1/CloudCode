"""Compile the final report from retained live and independent evidence."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
SUMMARY = json.loads((OUT / "evidence-summary.json").read_text())

def link(name, path=None):
    return f"[{name}](<{(path or OUT / name).as_posix()}>)"

parts = [f"""# STEP 4 — Live benchmark report

## A. Executive result

**FAIL**

The approved live endpoint responded, but Cloud Code did not complete either application. The original control run, one clean control retest after a targeted pipeline fix, and the single chess run all stopped as `STALLED`. Independent checks rejected all three outputs. Chess could not import its engine or launch its server; no graphical playthrough occurred.

This completes the requested benchmark and diagnosis, not a working chess application. Generated application code was not manually repaired. Failed runs remain intact.

| Run | Submitted task specifications | Actual model responses / rounds | Limit per run | Elapsed | Workflow | Independent result |
|---|---:|---:|---:|---:|---|---|
| Control, original | 1 | 10 / 10 | 50 | 406.297 s | STALLED, phase 4 | 6 tests; 3 failures, 1 error |
| Control, clean retest | 1, same specification | 9 / 9 | 50 | 348.984 s | STALLED, phase 4 | 6 tests; 3 failures, 1 error |
| Chess | 1 | 9 / 9 | 50 | 448.391 s | STALLED, phase 4 | 20 tests; 2 failures, 23 errors including subtests |

All acceptance commands exited 1. Test methods and subtest errors are different counts; 23 errors does not mean 23 additional test methods. Total generation time was 1,203.672 seconds (20 min 3.672 s), excluding setup, regression tests, independent checks, and gaps between runs. The response limit was never increased.

## B. Environment

- Benchmark date: 21 September 2026, Asia/Karachi (UTC+05:00). Successful preflight: 11:58:19–11:58:21 local. Generation: 12:00:47–12:24:19 local, with gaps between runs. Final chess independent verification: 15:55:24 local after continuation.
- Report compiled: {datetime.now(timezone.utc).isoformat()}.
- Windows 11, build 26200; Python 3.12.14, 64-bit; pyautogen 0.2.35; openai 1.109.1; pytest 9.1.1.
- Chess dependency: python-chess 1.999 / chess 1.11.2. Browser automation: Playwright 1.63.0 with Chromium 153.0.8010.12 (build 1243). These were installed for the specified library and independent GUI checks. A browser environment smoke check succeeded; it is not chess evidence.
- Repository HEAD: `4ef7be127ecb9442b1650615ed3c234087be70e2`. The checkout already contained Steps 1–3 changes and user changes; HEAD alone does not describe the tested tree. Initial working-tree inventory is in {link('preflight.json')}.
- Model: `qwen2.5-coder:32b`, explicitly approved by the user after the endpoint advertised that exact identifier.
- Endpoint: [approved Cloudflare endpoint](https://str-resolution-neck-varying.trycloudflare.com). `/v1/models` and the live completion probe returned HTTP 200; probe content was `LIVE_PREFLIGHT_OK`. Health is recorded at run time, not asserted indefinitely.
- Runtime settings: 50 model responses per run; 120-second model request timeout; 4,096 maximum output tokens; temperature 0.2; OpenAI SDK default maximum retries 2; terminal default timeout 60 seconds; stall limit 4.
- Live mode: AutoGen response cache disabled for these runs (`cache_seed=None`); no mock or prerecorded responses supplied. The observer retained 28 actual HTTP 200 completion responses during generation, plus one separate preflight completion. No failed HTTP attempt or model-request timeout was recorded. HTTP evidence establishes requests to the approved service; it does not independently establish its server internals, GPU, or model weights.
- Configuration preserved: `orchestrator_config.json` SHA-256 before and after is `{SUMMARY['config_sha256']}`. Saved configuration was not rewritten; endpoint/model/cache overrides were confined to benchmark invocation. Existing saved values remain unchanged.
- Isolated output directory: `{OUT}`. Separate generated workspaces: `control`, `control-retest`, and `chess` beneath it. No existing application project was overwritten.

The earlier unavailable-endpoint preflight and report are preserved under `{OUT.parent / 'step4-20260921-1107'}`. The intermediate model-mismatch preflight is also preserved. No benchmark was run against an unavailable endpoint or silently substituted model.

## C. Control task

Exact frozen prompt: {link('control-prompt.txt')}. The task was a standard-library Integer Summary CLI: a callable `summarize` function, nonempty list/tuple of integers excluding booleans, exact count/total/mean/minimum/maximum result, input preservation, JSON CLI output, useful invalid-input behavior, genuine unit tests, README, and running guide. `app_gui.py` was explicitly designated the CLI presentation module to accommodate the existing workflow's fixed Python profile; no control GUI was claimed.

Both runs autonomously wrote `app_logic.py`, `app_gui.py`, `main.py`, and `test_app.py`. Neither supplied README.md or RunningGUIDE.txt. They produced backups retained as evidence.

On final files, each generated suite actually ran 10 tests: 9 passed, 1 failed, 0 errors, 0 skipped; process exit 1. The boolean-rejection test failed. Each independent suite ran 6 test methods and reported 3 failures and 1 error: two boolean-rejection subtests failed, valid CLI execution crashed with `NameError: format_summary is not defined`, and README was missing. The callable handled the exercised ordinary statistics and several invalid inputs, but that partial behavior does not meet the contract.

The first generated logic response already used `isinstance(x, int)` without excluding `bool`. This is the earliest identifiable generated-code defect, confirmed by later execution. In original round 4, nested shell examples in an unfinished README were treated as executable commands. The placeholder `python main.py <integer1> <integer2> ...` exited 1 with a shell syntax error. An embedded test example also executed and exposed the boolean failure. Formal workflow verification was absent in that original run because document parsing errors stopped it earlier.

After the narrow parser/feedback fix described in section F, the same prompt and acceptance checker were rerun from an empty control-retest workspace. Embedded README examples no longer executed. Formal generated tests ran in rounds 4–9, each failing the boolean case; import checks exited 0. The model did not repair the application or finish the documentation, and the retest stalled after 9 responses.

Chess was still meaningful as a bounded diagnostic because the remote model, file writing, and real execution had been demonstrated, while the parser defect had a tested fix. The failed control result was retained and was never treated as a successful prerequisite.

## D. Chess benchmark

Exact frozen prompt: {link('chess-prompt.txt')}. One specification was submitted, followed by 9 live responses and automatic workflow feedback. No human application repair was inserted between responses.

The specification required a local two-human browser GUI using the standard-library HTTP server and the disclosed python-chess rules library, full standard moves and special moves, turn enforcement, king safety, check/mate/stalemate and specified draws, promotion choice, HTTP state/move/reset/position endpoints, accessible interactive board controls, a guarded command-line launcher, meaningful tests, requirements, and complete documentation. The prompt specifies exact public contracts so acceptance can inspect behavior without supplying an implementation.

**Actual output:** `chess_logic.py`, `chess_gui.py`, `main.py`, and an incomplete `RunningGUIDE.txt`. Missing: `test_chess.py`, `README.md`, `requirements.txt`, and the `index.html` referenced by the GUI. The files contain an attempted Game wrapper, HTTP handler, and argument parser; no chess feature was independently demonstrated to work.

**Independent final result:** 20 test methods executed in 8.868 seconds; 2 failures and 23 errors including subtests; exit 1. Engine checks could not get past `ImportError: cannot import name 'CastleRights' from 'chess'`. The HTTP/GUI launch failed separately with `ImportError: cannot import name 'Game' from 'chess'`. The generated handler imports Game from the third-party package instead of its own `chess_logic` module. Static inspection also shows a fresh Game created per request and missing HTML, so a working persistent board is not implemented. Those later defects were inspected, not runtime-verified after bypassing the import failure.

**Generated tests:** trusted unittest discovery found 0 tests and exited 1. The unittest result object's `successful: true` on an empty suite is explicitly rejected by the runner; it is not a pass. Repeated responses 6–9 contained an unfinished `test_chess.py` with impossible move strings such as `e2e74`; incomplete fences were rejected rather than written as valid tests.

**Launch and GUI:** the independent suite attempted the real launcher twice on dynamically allocated local ports; both exited 1. A separate bounded launch using exactly the requested `python main.py --host 127.0.0.1 --port 8765` also exited 1. The generated documentation never supplied a complete launch command, so this is a check of the requested launch contract, not proof that a documented procedure works. The checker's phrase “Documented launch” in its error message refers to that contract. No server became usable, no browser chess board opened, no square was clicked, and no chess screenshot was captured. Browser availability did not overcome application import errors.

The frozen suite uses hardcoded positions plus python-chess as a legal-move oracle. Because the generated app was also allowed to use python-chess, this checks the wrapper and integration rather than independently validating that library's rules implementation. It includes king safety, pins, both castling sides and restrictions, en passant and expiry, all promotion choices, resets, known endgames, draw cases, HTTP rejection, generated tests, and a real browser sequence. Rule behavior remained untested because the engine could not import; the suite failed rather than skipping those errors.

### Required acceptance checklist

`NOT TESTED` below means the actual behavior was not reached, not that the acceptance attempt was skipped or successful.

| # | Requirement | Result | Evidence / limit |
|---:|---|---|---|
"""]
requirements = ["Correct initial pieces and squares", "Legal movement of each piece", "Turn enforcement", "Illegal move rejection", "Captures update board", "King cannot move into check", "Pinned piece cannot expose king", "Check detection", "Checkmate detection", "Stalemate detection", "Legal castling", "Prohibited castling rejected", "Legal en passant", "Unavailable en passant rejected", "Promotion applies selected piece", "New game resets position and state"]
for i, name in enumerate(requirements, 1):
    parts.append(f"| {i} | {name} | NOT TESTED | Engine import failed before behavior could run. |\n")
parts.append("""| 17 | Complete two-player GUI sequence | NOT TESTED | Actual launch exited 1 before browser interaction. |
| 18 | Launch using documented command | FAIL | No complete documented command; required launch also exits 1. |
| 19 | Genuine generated tests execute and pass | FAIL | test_chess.py absent; trusted discovery ran 0 tests, exit 1. |
| 20 | Final files match verified files | PASS | SHA-256 snapshots match before/after final checks and NodeCore final record. This is identity, not correctness. |

Additional draw and HTTP behavior checks were also not reached because of the import/launch failures. Visible board, alternating colors, rendered pieces, selection, legal highlights, turn updates, captures, and endgame notifications remain unverified and incomplete. Final workflow state: **STALLED, phase 4**, with phases 1, 2, and 3 recorded complete. Those phase markers indicate workflow progression, not independent feature acceptance. Independent chess benchmark result: **FAIL**.

## E. Execution evidence

The observer invokes `launcher_gui.run_autonomous_orchestrator`, the actual shared GUI/CLI worker, and retains NodeCore/AutoGen traffic and events. It does not write the application itself or replace the agent loop. It bypasses clicking the GUI and the top-level CLI configuration-saving wrapper to preserve the saved configuration. Therefore this verifies the shared execution worker, not the usability of the GUI start button or full CLI argument parsing.

All three runs show `files_read: []`: no Reader operation was recorded. File creation/modification was real. The original control recorded 9 shell commands (all exit 1); the retest recorded 12 verification commands (six generated-test exits 1, six import exits 0). Chess recorded no terminal commands: workflow verification stopped at the missing test file in rounds 4–9. The separate independent checker then ran the failing application. The requested successful Reader → Writer → Terminal → acceptance cycle was consequently **not demonstrated end to end**.

Every run recorded transitions 1 → 2 → 3 → 4 and terminated with “Repeated iterations made no new file or phase progress.” No run reached final acceptance inside Cloud Code, phase 5 completion, or COMPLETED. None exhausted the 50-response budget. The stall guard, rather than a timeout or manual cancellation, stopped repeated unproductive output. No extra chess retry or budget increase was used.

Exact commands, their output and exit codes, file operations, final hashes, outstanding requirements, and transition timestamps are in each result/event record and evidence-summary.json. Full task context and actual response bodies are in the HTTP JSONL records; credentials and HTTP authorization headers were not logged. All benchmark driver exits were 1 because their workflows were incomplete. Final diagnostic collection itself exited 0 after retaining failing child exit codes; it does not indicate application success.

### Commands and final verification

From the repository root, the live invocations were the diagnostic venv Python plus `run_live.py control`, `run_live.py control --retest`, and `run_live.py chess`, with logs redirected to their respective live logs. Exact absolute paths are listed below and retained in metadata. These commands used the approved endpoint/model and fixed budget.

The frozen independent checkers were each run against final files; all exited 1. Additional final diagnostics invoked the real NodeCore isolated unittest runner on every final workspace, then the exact required chess launcher. Each child exited 1. Final diagnostics retain complete argv, working directory, output paths, test counts, and unchanged-file checks in final-diagnostics.json.

Control and chess acceptance checker hashes were unchanged from their run metadata through final verification. Application source/document hashes were unchanged during final checks and match NodeCore's final snapshot. Snapshots intentionally exclude runtime metadata, caches, NodeLog data, and writer .bak backups. No acceptance check or application code was weakened to obtain a pass.

## F. Failure analysis and targeted fix

| First divergence | Category / owner | Evidence and conclusion |
|---|---|---|
| Control response 1 accepts bool as int | Generated application | Full HTTP response; later generated boolean test and independent subtests fail. Explicit prompt excludes bool, so the contract was not ambiguous. |
| Control round 4 executes a nested README shell example | Cloud Code infrastructure | COMMAND_RUN entries show the literal placeholder command and exit 1. The document-fence parser exposed nested examples as commands; parse errors also prevented formal verification feedback. |
| Control round 4 launcher omits format_summary import | Generated application | Final independent CLI execution raises NameError. This survives the clean retest. |
| Chess response 1 imports nonexistent CastleRights | Generated application | First HTTP response matches retained logic; independent import fails in the installed, available chess dependency. No evidence of a missing installation. |
| Chess response 2 uses third-party chess.Game | Generated application | Response and final GUI source; actual main.py launch raises ImportError. The same GUI references missing HTML and does not retain one server-owned Game. |
| Chess tests repeatedly unfinished; rounds 6–9 repeat invalid test text | Response content / unsuccessful repair | Complete recorded HTTP bodies have unfinished fences and impossible UCI strings. Writer rejects incomplete test artifact. Missing-test verification feedback never leads to a repaired suite. |
| Chess verification stops at missing test_chess.py | Pipeline diagnostic limitation | Verification records contain the missing-file error and no executed import/test command. This left other defects undiscovered by the live loop, though independent checks exposed them. No further pipeline change/retry was made. |

Response bodies frequently end mid-artifact while the service reports `finish_reason: stop`. This establishes incomplete returned content; it does not establish whether a server-side generation limit, backend behavior, or another cause is responsible. Do not attribute these failures to the network, context loss, model weights, or exhausted round budget without further evidence. The observed host returned HTTP 200 for all generation requests. Repeated identical content is preserved, and local AutoGen response caching was disabled.

One limited pipeline repair was made after preserving the original control failure:

- `NodeCore/node_core/agents.py`: support wider outer fences and preserve nested shell/code examples within Markdown/text artifacts as document content; incomplete outer artifacts remain rejected.
- `NodeCore/node_core/workflow.py`: when documentation parsing fails in phases 4/5, still verify already-written code and include real failures in feedback without advancing or declaring completion; recommend a four-backtick outer document fence.
- `NodeCore/test_document_fences.py`: four regressions covering nested examples, incomplete documents not executing embedded commands, mixed document/Python artifacts, and visibility of a genuine failing test despite a document parse error.

Validation: `python -m pytest NodeCore -q --tb=short` in the diagnostic venv: **124 passed, 8 warnings, 19.93 s**, exit 0. The warnings were retained in parser-regressions.log. `git diff --check` also exited 0 (line-ending notices only). Existing Step 3's 464/4 results are historical, not rerun or counted as live application evidence here. Some infrastructure tests use test doubles; they are regression evidence only.

The clean control retest confirms the corrected command handling and restored verification feedback, while also confirming the application still fails. No manual generated-code edit, acceptance relaxation, architecture rewrite, repeated infrastructure repair loop, commit, or deployment was performed.

## G. Honest conclusion

1. **Did the endpoint work?** Yes: model discovery, the probe, and 28 real generation completions returned HTTP 200 from the approved endpoint.
2. **Did Cloud Code autonomously generate the control task?** It generated partial source and real tests, but neither attempt produced a complete working CLI.
3. **Did it autonomously generate chess?** It generated four partial artifacts. It did not produce a functioning chess application.
4. **Did chess actually launch?** No. Actual launcher processes exited 1 during import.
5. **Did chess pass independent acceptance?** No. The suite failed; no GUI playthrough or rules behavior was verified.
6. **How many explicit user prompts were required?** Completion was not achieved, so a successful prompt requirement cannot be stated. For this Step 4 sequence there was one substantive benchmark brief plus the endpoint instruction and explicit model approval; continuation/status messages did not alter either specification. The evaluator submitted two distinct application specifications and replayed the control once: three task submissions total. Those evaluator submissions are not three separately authored user prompts. The metadata field `user_application_prompts: 1` per run counts submitted application tasks, not conversational messages. No human corrective application prompt was inserted inside a run.
7. **How many model responses?** 28 coding responses: 10 original control + 9 control retest + 9 chess; plus 1 separately reported preflight response. Automatic feedback rounds and tool calls are not additional user prompts.
8. **What remains incomplete?** Control bool validation, CLI import, and documentation; chess engine integration, persistent server state, actual HTML/GUI, complete launch/setup documentation, requirements, generated tests, and verified chess rules/playthrough. Cloud Code also did not demonstrate Reader use or a successful complete execution cycle in this benchmark.
9. **Ready for broader real-world coding?** These results do not support that claim. The narrower supported claim is live transport, autonomous file writing, some real verification/feedback, a tested parser fix, and honest stall reporting. Reliability at completing even this control task is not demonstrated under the tested configuration.

## H. Artifact list

All links below resolve to exact local paths. Failed outputs, raw responses, diagnostic scripts, and logs are retained. There are no chess screenshots; the screenshot directory contains no chess playthrough evidence.

""")
for name in ["STEP4_LIVE_BENCHMARK_REPORT.md", "preflight.json", "preflight.log", "preflight-model-mismatch.json", "preflight-model-mismatch.log", "control-prompt.txt", "chess-prompt.txt", "control_acceptance.py", "chess_acceptance.py", "run_live.py", "independent_check.py", "final_diagnostics.py", "final-diagnostics.json", "evidence-summary.json", "parser-regressions.log", "chess-launch.log", "chess-required-launch.log"]:
    parts.append(f"- {link(name)}\n")
for name, data in SUMMARY["runs"].items():
    parts.append(f"\n### {name}\n\n- Generated workspace: {link(name)}\n- NodeCore run record: {link(Path(data['run_record']).name, Path(data['run_record']))}\n")
    for suffix in ["live.log", "http.jsonl", "events.jsonl", "metadata.json", "result.json", "independent-final.json", "independent-final.log", "generated-tests.json", "generated-tests.log"]:
        parts.append(f"- {link(name + '-' + suffix)}\n")
    parts.append("\nFinal deliverable SHA-256 values (also match independent before/after snapshots):\n\n| File | SHA-256 |\n|---|---|\n")
    for file, sha in data["final_files"].items():
        parts.append(f"| {link(file, OUT / name / file)} | `{sha}` |\n")
parts.append("\n### Source changes and preserved earlier report\n\n")
for name in ["NodeCore/node_core/agents.py", "NodeCore/node_core/workflow.py", "NodeCore/test_document_fences.py", "STEP3_REPORT.md"]:
    parts.append(f"- {link(name, ROOT / name)}\n")
parts.append(f"- {link('Earlier unavailable-endpoint report', OUT.parent / 'step4-20260921-1107/STEP4_LIVE_BENCHMARK_REPORT.md')}\n")
report = OUT / "STEP4_LIVE_BENCHMARK_REPORT.md"
report.write_text("".join(parts), encoding="utf-8")
print(str(report))

# Check identity claims against independent data, rather than trusting report prose.
for name, data in SUMMARY["runs"].items():
    assert data["final_files"] == data["independent"]["files_before"] == data["independent"]["files_after"], name
    assert data["checker_unchanged"] and data["independent"]["files_unchanged"]
assert SUMMARY["config_sha256"] == "fe68f9054e01c64c948c3a89233aba44913c145a991c5037b677b7dfe393bb88"
assert hashlib.sha256((ROOT / "orchestrator_config.json").read_bytes()).hexdigest() == SUMMARY["config_sha256"]
print("Report identity/count evidence validated.")
