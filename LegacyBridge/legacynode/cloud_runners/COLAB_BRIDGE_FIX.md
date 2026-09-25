# Correct the shared Colab bridge

The shared `LegacyBridge.ipynb` loads **Qwen/Qwen2.5-Coder-3B-Instruct** in its model-loading cell. Its server advertises `qwen2.5-coder:32b` regardless of the loaded model. The benchmark requires the actual 32B model, so renaming the response is not a fix.

The server cell also passes only the last user message to generation, hardcodes `max_new_tokens=1000`, always returns `finish_reason="stop"`, and counts whitespace words as tokens. All four behaviors are corrected in `qwen_bridge_server.py` and the accompanying `COLAB_BRIDGE_REPAIR.ipynb`.

## Apply the correction

1. Load the actual `Qwen/Qwen2.5-Coder-32B-Instruct` weights and matching tokenizer in your own runtime. The notebook's current 3B weights do not qualify. The displayed T4 has about 15 GiB of GPU memory; 32 billion parameters alone at two bytes each need roughly 64 GB before runtime overhead. Simply changing the model name in the existing float16 loading cell can exhaust that GPU. Use a runtime/model-loading configuration that can actually hold and run the 32B weights. No model download, paid runtime change, or replacement of your running model was performed here.
2. Copy the single code cell from `COLAB_BRIDGE_REPAIR.ipynb` into the existing notebook, **after** the model-loading cell. It reuses the existing `model` and `tokenizer`. It refuses to label 3B as 32B.
3. Run that cell. If the original FastAPI server is still running as `app`, its routes are replaced in place and the existing tunnel can remain. Otherwise it starts the corrected server on port 8000; reuse the original tunnel cell to expose that server. Do not start two servers on port 8000.
4. Check `<tunnel>/v1/models`. The required entry must report `id=qwen2.5-coder:32b`, `loaded_model=Qwen/Qwen2.5-Coder-32B-Instruct`, an actual parameter count near 32 billion, `supports_full_history=true`, `usage_source=tokenizer`, and `max_output_tokens=16384`.
5. Supply that fresh endpoint for the next benchmark. The runner will create `Testing-0.8` or the next unused sequential directory, check model provenance and completion health, then submit the unchanged original chess task through Cloud Code.

The repair cell does not install/download model weights or open a new tunnel. Its HTTP routes retain FastAPI, already used by the shared notebook. It rejects context overflow and unsupported output budgets explicitly instead of silently truncating requests. Generation is serialized while model-list health requests remain responsive. Streaming is explicitly unsupported.

## Local validation and limits

`LegacyBridge/test_qwen_bridge_server.py` exercises the real backend and FastAPI routes with tiny model/tokenizer doubles: truthful identity, parameter-count mismatch rejection, full message forwarding, output budget and temperature propagation, tokenizer counts, stop versus length, overflow, and unsupported streaming. These tests do not claim GPU or live 32B inference validation.

The repair is prepared locally. The shared notebook was publicly readable but signed out/read-only in this session; no edits or runtime changes were applied to it.
