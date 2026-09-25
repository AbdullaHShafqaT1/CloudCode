# Testing-0.8 — preflight stopped; bridge correction still required

Endpoint supplied by the user: `https://test-mason-rhode-warranties.trycloudflare.com`.

The endpoint is reachable: `/v1/models` returned HTTP 200. It advertises `qwen2.5-coder:32b` but supplies no loaded-model identifier, parameter count, history-handling capability, or actual token-budget metadata. Model provenance preflight therefore failed. This response alone does **not** prove that the newly supplied endpoint loads 3B; the actual loaded model remains unverified.

Three small diagnostic completion requests were then made separately from application generation:

| Check | Requested | Observed |
|---|---|---|
| System message | Reply with a newly generated unique marker supplied only in the system message | Replied “Two plus two equals four.” instead. |
| Prior conversation | Recall a unique value supplied in an earlier user message | Invented `1234567890`; did not return the supplied value. |
| Output budget | Spell out twenty numbers, with `max_tokens=1` | Returned all twenty English number words. |

In every probe, reported prompt/completion usage exactly matches the last user message's and response's whitespace-word counts. These reproduce the previous bridge's observed failures. The endpoint fails the required serving behavior. The probes do not establish exactly how its implementation handles history, its actual output cap, or the new runtime's precise model size.

## Benchmark integrity

- Fresh workspace: `C:\Users\Acer\Desktop\Projects\Tests\Testing-0.8`.
- Workspace remains empty; no application generation started.
- Benchmark application responses: **0**. Separate diagnostic requests: **3**.
- Independent acceptance, application launch and browser playthrough: **not run**.
- Functional completion: **not measured**, because this attempt stopped before generation. It is not a new 0% application result.
- Original prompt and checker copied byte-for-byte into this evidence directory. Source hashes, source snapshot, patch and configuration are recorded.
- Testing-0.7 and earlier workspaces/evidence were not reused or changed.

## Evidence

- `preflight.json`: HTTP 200 model listing and exact provenance rejection.
- `serving-contract-probes.jsonl`: complete diagnostic requests/responses with unique markers and timing.
- `diagnostic-invocation.json`: records the explicit fresh endpoint used for those probes; this version of the probe utility did not yet embed the URL in each row. Future probe rows include it directly.
- `endpoint-diagnosis.json`: structured findings, distinction between diagnostic and benchmark responses, and continuation requirement.
- `metadata.json`: attempt status, workspace, configuration and source provenance.

## What must change before the next attempt

Apply the already prepared `LegacyBridge/legacynode/cloud_runners/COLAB_BRIDGE_REPAIR.ipynb` cell to a runtime with the **actual 32B weights** loaded, following `COLAB_BRIDGE_FIX.md`, and provide that server's fresh endpoint. The bridge must preserve system/prior messages, honor `max_tokens`, report tokenizer usage and identify the loaded model honestly. A reachable tunnel by itself does not establish those conditions.

No qualifying benchmark milestone is claimed. No further benchmark was started against this unchanged serving behavior.
