# Cloud Code mission status: external model/runtime blocker

**The 70–80% functional milestone has not been achieved.** Local engineering and a reviewable bridge correction are complete for the next attempt; an actual 32B runtime is required before the benchmark loop can continue.

## Proven blocker

The user-supplied notebook is [LegacyBridge.ipynb](https://colab.research.google.com/drive/1H5MC9l17QyAYKIo22daaaYKkoMgb-0vn?usp=sharing). Its code and output were inspected in the browser:

| Location | Observed behavior | Effect |
|---|---|---|
| Model-loading cell | `model_name = "Qwen/Qwen2.5-Coder-3B-Instruct"` | The notebook loads 3B weights, not the required 32B weights. |
| Health endpoint | Hardcoded `qwen2.5-coder:32b` | `/v1/models` falsely suggests the benchmark model is present. |
| Completion handler | Iterates messages and retains only the last `role == "user"` content | System rules and earlier source/feedback are discarded. |
| Generator | Hardcoded `max_new_tokens=1000`, `do_sample=False` | The requested 16,384-token budget and temperature are ignored. |
| Response metadata | `len(...split())`; constant `finish_reason="stop"` | Word counts masquerade as token counts; truncation is reported as a normal stop. |

The notebook's tunnel output is the exact endpoint supplied for this run. Three separately recorded serving probes found that the endpoint ignored a system-only marker, invented an earlier-message value, and returned twenty spelled-out numbers despite `max_tokens=1`. Notebook code explains these results directly; this is not merely speculation about model capability.

The shared notebook was publicly readable but signed out/read-only. This session has no control of its running model or GPU. The displayed T4 has approximately 15 GiB of GPU memory; merely changing the existing float16 model-loading name to 32B is not sufficient to make it fit. A functioning actual-32B loading/runtime configuration is external work, and no paid runtime change or model download was made.

## Engineering completed

- Replaced Python truncation text heuristics with compilation, fixing four pre-existing regressions involving valid inline definitions, write rollback and full repair/completion.
- Aligned shell Python with verification Python and included executable/API inspection guidance. The prepared benchmark environment has python-chess 1.999/chess 1.11.2 installed.
- Removed contradictory recovery instructions that prohibited read/shell inspection. Added concise test/decomposition guidance while retaining all task requirements.
- Added one bounded inspection/repair recovery window inside the same response budget, with before/after file, phase and error evidence.
- Limited optional inference history while retaining the complete evidence transcript. Subsequent feedback also includes the original requirements, relevant observed source and a concrete next action together, with the action last.
- Added a sequential clean-workspace runner with explicit endpoint input, environment/model preflight, source snapshots, immutable prompt/checker copies, complete HTTP observation, independent outcomes and requirement matrices.
- Added a corrected Colab FastAPI bridge: loaded-model identity and parameter-count checks, full messages, requested token budget/temperature, tokenizer counts, accurate stop/length, and explicit context-limit rejection. The benchmark now requires loaded-model provenance, not merely an advertised alias.
- Review fixes preserve cancellation/setup failures, require final all-module import evidence, clean checker/browser process trees on timeout, and invalidate all verification claims when integrity checks fail.

Existing work was preserved. No chess application source was manually written, repaired or copied into a benchmark workspace. No changes were made to the original benchmark prompt or independent acceptance checker.

## Validation

Final complete local suite: **553 passed, 5 skipped, 64 warnings in 50.47 seconds**. Log: `.cache/mission-reviewed-full-suite.log`. Focused bridge/reliability suite: 38 passed. `git diff --check` is clean.

Skips remain Windows chmod behavior, two opt-in live model tests, the remote bridge test without a remote configuration, and the native Tk lifecycle test because this Python runtime lacks Tcl. The browser benchmark's Chromium preflight succeeded. These local tests do not establish chess functionality or live 32B inference.

The bridge was exercised through its real FastAPI routes using tiny model/tokenizer doubles. GPU execution of the replacement bridge remains unverified until the remote correction is applied.

## Benchmark history and progression

| Attempt | Advertised model / responses | Result | What is actually established |
|---|---|---|---|
| Earlier `step4-20260921-1641` | 32B / 9 | STALLED | Prior evidence retained; actual model identity was not established. |
| Earlier `step4-post-repair-20260921` | 32B / 16 | STALLED | Wrong local Python environment, repeated bad imports, truncated files; no recorded HTTP transcript to establish server token limits. |
| `Testing-0.7` | 32B label / 10 completed | Intentionally interrupted, disqualified | Shared notebook reveals 3B loading and broken request forwarding. All 20 independent test methods run after stopping; application still fails. |
| `Testing-0.8` | 32B label / 0 application responses | PREFLIGHT_FAILED | New user endpoint reachable but lacks loaded-model provenance. Three separate diagnostics reproduce ignored system/history messages and ignored output limits. Actual model size remains unverified; workspace remains empty. |

Testing-0.7 used a fresh `C:\Users\Acer\Desktop\Projects\Tests\Testing-0.7`. Existing Testing-0.1 through Testing-0.6 were untouched. Its initial version-1 measurement is preserved as **2/33 = 6.06%**. Review split out two GUI requirements that the checker does not prove, producing the separately recorded version-2 reassessment **2/35 = 5.71%**. Only check detection and the checked fifty-move/insufficient-material draw positions are verified. Full matrix and per-test evidence: `Testing-0.7/REPORT.md`, `independent.json`, and `requirements-v2-review.json`.

This run is not a qualifying 32B benchmark regardless of its functional percentage. The historic 15–20% assessment was an estimate with no comparable fixed requirement matrix. No reliable functional progression or “two prompts remaining” conclusion can be inferred from these runs.

## Prepared correction and exact next action

- `LegacyBridge/legacynode/cloud_runners/COLAB_BRIDGE_REPAIR.ipynb`: ready-to-copy single repair cell, compiled locally.
- `LegacyBridge/legacynode/cloud_runners/qwen_bridge_server.py`: the tested bridge implementation.
- `LegacyBridge/legacynode/cloud_runners/COLAB_BRIDGE_FIX.md`: application steps and runtime prerequisites.

The repair cell reuses an already loaded actual-32B model and tokenizer. It can replace the existing FastAPI app's routes without replacing its tunnel; it refuses to relabel the current 3B model. The correction has not been deployed into the shared notebook.

To continue, the user needs to start an actual-32B runtime with this corrected bridge and provide its fresh endpoint. Then run, from the CloudCode root:

```powershell
& .\.cache\diagnostic-venv\Scripts\python.exe benchmarks/run_chess_mission.py --url '<fresh supplied URL>' --reason 'Actual 32B weights, corrected full-history bridge and validated reliability changes'
```

The runner allocates Testing-0.9 or the next unused index and repeats preflight before submitting the unchanged task. Testing-0.8 reserved a fresh empty workspace but stopped in preflight; see `Testing-0.8/REPORT.md`. A `STOP` file in an attempt's evidence directory requests cooperative cancellation. No new attempt should reuse a misconfigured endpoint unchanged.

## Evidence index and integrity

- Investigation: `MISSION_INVESTIGATION.md`.
- Current attempt: `Testing-0.7/REPORT.md`, `metadata.json`, `preflight.json`, `http.jsonl`, `events.jsonl`.
- Serving diagnosis: `server-routes.json`, `serving-contract-probes.jsonl`, `server-findings.json` within Testing-0.7.
- Version used during generation: `source-snapshot.zip`, `source-hashes.json`, `working-tree.patch` within Testing-0.7. Later fixes are distinguished above and were not applied to generated chess files.
- Original prompt SHA256: `782ccd82b78c53dfe81a8c04ff2d5e8d71c910d003cbe86e08e90168fd0be821`.
- Original checker SHA256: `9f1e948334c5bee30f5bacf84cd128e0142eb37ac5c0d374ef30f0c516920bcf`.

A successful `FINAL_BENCHMARK_REPORT.md` is deliberately not created: the success condition remains unmet. The next recommendation is to correct the serving environment and resume a clean benchmark, not to rerun the same mislabeled 3B setup or claim success from the local test count.
