# Reliability mission: evidence and decisions

## Scope and immutable specification

The authoritative task is `step4-post-repair-20260921/chess-prompt.txt`, the browser version, not the older Tkinter preset in `orchestrator_config.json`.

- Prompt SHA256: `782ccd82b78c53dfe81a8c04ff2d5e8d71c910d003cbe86e08e90168fd0be821`.
- Independent checker SHA256: `9f1e948334c5bee30f5bacf84cd128e0142eb37ac5c0d374ef30f0c516920bcf`.
- Neither source is edited. Each new attempt copies these exact bytes to its own evidence directory; no application source is copied into any new workspace.
- Existing `TESTING-0.1` through `TESTING-0.6` are preserved. New attempts use the next unused sequential index under the user's Tests root.

## Architecture inspected

The shared GUI/CLI launcher calls NodeCore, which constructs AutoGen CoderAgent/UserProxyRunner. TaskWorkflow owns phase transitions, immutable caller acceptance criteria, response accounting and final evidence. Markdown file/read/shell blocks route through NodeForge, NodeInsight and NodePulse. Verification uses the current Python executable and a separate trusted unittest runner. Final completion also requires the caller's acceptance command. NodeLog records actions and NodeCore retains the full message/verification history.

Inspected the orchestrator, model configuration, parser, contract extraction, workflow, tool adapters, terminal/process cleanup, trusted verification runner, prior live runner, original prompt/checker, previous diagnosis/implementation reports and regression tests. Existing uncommitted repairs were preserved. This is an extension of the current architecture, not a rewrite.

## Findings supported by direct evidence

1. **A real regression rejected valid Python repairs.** Initial complete NodeCore run: 168 passed, 4 failed (`.cache/mission-baseline.log`). The incomplete-artifact heuristic treated an inline function body as an unfinished definition. Python compilation now decides Python structural validity. Existing write-rollback, filename protection, GUI routing and full repair/completion regressions all pass again.
2. **The prior benchmark used the wrong environment.** Its recorded import commands explicitly invoke `Python310/python.exe` and report missing `chess`. The prepared environment is `.cache/diagnostic-venv/Scripts/python.exe`, Python 3.12, with python-chess 1.999/chess 1.11.2. Terminal PATH now starts with the verification executable's directory. Context identifies that interpreter and its pip command.
3. **Parts of the old API diagnosis were wrong.** Direct inspection of installed chess 1.11.2 proves `Color`, `Status`, and `Board.status` exist. `CastleRights` does not. This does not make the generated use of those APIs correct; it invalidates the blanket claims of absent symbols in the old report.
4. **Recovery instructions contradicted inspection.** The system message said a corrected complete file was the ONLY valid error response while feedback requested read/shell inspection. It now permits bounded diagnostic reads and commands before writing a complete repair.
5. **Recovery was effectively a single chance.** Old accumulated stall counters could immediately stop the next response after a recovery event. The new recovery clears those counters once and grants another bounded stall window, still inside 50 total responses. Reports retain pre-recovery state and compare actual files, phase and errors afterward. These comparisons establish state change, not causal proof of successful recovery.
6. **Adaptive feedback could contradict dependency priority.** Ordinary repeated-error feedback requested missing tests even while imports failed. It now prioritizes import inspection. Dependency guidance no longer tells the model to remove a task-required library.
7. **Repeated failed source accumulated in every model request.** The inference-only history hook keeps the original requirements and latest tool feedback, and selects at most three recent intermediate messages within a 36,000-character optional-history budget. It preserves the full original transcript for evidence. First/latest messages are not clipped, so this is not a hard token/context bound.
8. **The old post-repair run has no HTTP transcript.** Its `run_live.py` contains an endpoint-specific traffic filter, and `chess-http.jsonl` is absent. Server-side token ceilings cannot be inferred from the earlier reports alone. The new observer matches the explicit endpoint dynamically and records actual request limits, response usage, finish reason and character counts.

## Changes and verification

Changes are confined to workflow/parser-adjacent reliability instructions, history selection, regression coverage, and benchmark observation. No generated chess file is manually implemented or repaired.

Full local suite after these changes: **531 passed, 5 skipped, 63 warnings, 47.23 seconds**, `.cache/mission-full-suite.log`. The 16 new regressions exercise valid/truncated Python, interpreter identity, inspection followed by repair, budget enforcement, bounded inference view, blocker priority, fresh workspace allocation and conservative measurement. Two older stall-duration assertions now match the deliberately expanded bounded recovery window; the independent acceptance checker is unchanged.

Skips: Windows chmod semantics; two explicitly opt-in live model tests; remote bridge test without configured remote mode; native Tk test because this runtime lacks Tcl. The benchmark uses a real browser rather than Tk. Local tests are not benchmark-completion evidence.

## Measurement

`benchmarks/measure_chess.py` runs the original checker test bodies unchanged and records unittest outcomes, including failed subtests. The first matrix declared 33 functional requirement groups before generation, with six areas lacking checker coverage. File presence receives no credit. A failed grouped browser test receives no partial credit, even if earlier interactions happened to work. Review subsequently separated two additional unverified GUI requirements (Unicode symbols and visible winner) into a stricter 35-group version-2 matrix. The original Testing-0.7 measurement is retained, and its version-2 reassessment is saved separately. Documentation and generated tests are recorded separately by the unchanged checker. The matrix is an explicit equal-weight approximation of functionality, not an assertion that all requirements have equal effort.

No milestone is claimed until a fresh live run supplies evidence. Historical 15–20% estimates are not validated percentages and are not directly comparable with this matrix.

## Confirmed external serving defects and final status

Inspection of the user-supplied Colab notebook confirmed that it loads Qwen2.5-Coder-3B-Instruct, advertises 32B, retains only the last user message, hardcodes 1,000 output tokens, and fabricates token/finish metadata. Live diagnostic requests independently reproduced missing system/history behavior and a 20-word response despite `max_tokens=1`. Testing-0.7 was stopped and disqualified after ten completed responses. Its unchanged checker ran all 20 methods; only check detection and the tested fifty-move/insufficient-material positions passed.

Final reviewed local suite: **553 passed, 5 skipped, 64 warnings, 50.47 seconds**, `.cache/mission-reviewed-full-suite.log`; `git diff --check` clean. The added warning concerns Starlette's TestClient, installed in the isolated environment to exercise the bridge's real HTTP routes. See `MISSION_STATUS_REPORT.md` for the external blocker, prepared correction, complete evidence and resume procedure. The final bridge and feedback improvements have not yet had a qualifying actual-32B live benchmark.
