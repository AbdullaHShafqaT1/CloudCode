# Testing-0.7 — stopped and disqualified

## Outcome

The run used a fresh `C:\Users\Acer\Desktop\Projects\Tests\Testing-0.7` workspace. It was intentionally interrupted after 10 completed model responses when the supplied notebook revealed an invalid model identity. No generated application files were manually edited. Original prompt/checker bytes are unchanged.

The shared notebook loads `Qwen/Qwen2.5-Coder-3B-Instruct`, while its API hardcodes `qwen2.5-coder:32b`. It sends only the last user message to the model, hardcodes `max_new_tokens=1000`, reports word counts as token counts, and always reports `finish_reason=stop`. The same notebook output contains the exact endpoint used for this attempt.

This does not qualify as the requested 32B benchmark. An advertised model name is not sufficient evidence of the loaded model.

## Independent result

The unchanged checker ran all **20 test methods**. Two methods passed; failed subtests contribute multiple failures. Recorded result: 25 failure entries and 3 errors. Generated files were unchanged by measurement. Check detection and the checked fifty-move/insufficient-material draw positions passed. Engine movement/state, HTTP rejection, documentation/tests, and full browser interaction did not pass. The HTTP server responded, but the browser test timed out waiting for the required rendered position. No working browser application is claimed.

The original version-1 matrix recorded **2/33 = 6.06%**. That original evidence remains untouched in `requirements.json` and metadata. A subsequent review found that two combined GUI rows could overclaim Unicode rendering and visible winner text. Version 2 separates those unverified requirements, producing **2/35 = 5.71%**, stored separately in `requirements-v2-review.json`. The distinction does not affect the conclusion: the application is far below the milestone, and the model identity disqualifies the run regardless of percentage.

## Version-2 requirement matrix

| Requirement | Status | Evidence |
|---|---|---|
| Initial 32-piece position and JSON state | unverified | No complete passing check for this requirement |
| Legal movement for all six piece types | unverified | No complete passing check for this requirement |
| Two human players and turn enforcement | unverified | No complete passing check for this requirement |
| Illegal/malformed moves rejected without mutation | unverified | No complete passing check for this requirement |
| Captures and persistent move sequence | unverified | No complete passing check for this requirement |
| King safety | unverified | No complete passing check for this requirement |
| Pinned pieces | unverified | No complete passing check for this requirement |
| Check detection | verified | test_08_check |
| Checkmate, winner and postgame rejection | unverified | No complete passing check for this requirement |
| Stalemate | unverified | No complete passing check for this requirement |
| Both sides' kingside/queenside castling | unverified | No complete passing check for this requirement |
| Castling restrictions | unverified | No complete passing check for this requirement |
| En passant capture | unverified | No complete passing check for this requirement |
| En passant expiration | unverified | No complete passing check for this requirement |
| All four engine promotion choices | unverified | No complete passing check for this requirement |
| Engine reset | unverified | No complete passing check for this requirement |
| Fifty-move and insufficient-material draws | verified | test_draw_handling |
| Claimable repetition draw | unverified | No complete passing check for this requirement |
| Real documented CLI launch | unverified | No complete passing check for this requirement |
| Visible 8x8 alternating board and 32 pieces | unverified | No complete passing check for this requirement |
| All pieces rendered with Unicode chess symbols | unverified | No complete passing check for this requirement |
| Selection and legal-destination highlighting | unverified | No complete passing check for this requirement |
| Browser two-player moves, capture and turn updates | unverified | No complete passing check for this requirement |
| Browser new game resets position | unverified | No complete passing check for this requirement |
| Browser checkmate status | unverified | No complete passing check for this requirement |
| Winner visibly identified in browser | unverified | No complete passing check for this requirement |
| Browser promotion choice and draw display | unverified | No complete passing check for this requirement |
| Browser check and stalemate displays | unverified | No complete passing check for this requirement |
| Piece accessible labels and two-player instructions | unverified | No complete passing check for this requirement |
| HTTP state, move, error JSON and reset | unverified | No complete passing check for this requirement |
| HTTP position load through the same engine | unverified | No complete passing check for this requirement |
| Bounded HTTP request sizes and bad JSON handling | unverified | No complete passing check for this requirement |
| Each server owns a fresh game | unverified | No complete passing check for this requirement |
| Import without starting servers/windows | unverified | No complete passing check for this requirement |
| No network assets or CDN | unverified | No complete passing check for this requirement |

## Evidence

- `metadata.json`: initial configuration, source commit/hash manifest, disqualification and preserved version-1 score.
- `source-snapshot.zip`, `source-hashes.json`, `working-tree.patch`: implementation used at attempt start, including uncommitted source.
- `chess-prompt.txt`, `chess_acceptance.py`: exact original specification/checker bytes.
- `preflight.json`: initial advertised-model and minimal-completion success. Later source inspection invalidated model identity.
- `http.jsonl`: all ten completed benchmark request/response bodies, reported usage, finish reasons, output/prompt lengths and elapsed times.
- `events.jsonl`: generated-file, command and phase evidence. The final workflow report is unavailable because the run was deliberately interrupted.
- `server-routes.json`, `serving-contract-probes.jsonl`, `server-findings.json`: bridge schema, diagnostic requests and notebook findings.
- `independent.json`, `independent.log`, `chess-launch.log`: unchanged independent checker results, tracebacks and launch output.
- `requirements.json`: preserved initial measurement. `requirements-v2-review.json`: separately versioned conservative reassessment.

## Changes for the next attempt

Cloud Code now carries original requirements, observed current source and the concrete next action together in each feedback message. A replacement Colab bridge preserves all conversation roles, propagates output limits and temperature, reports tokenizer usage/actual finish reason, and derives identity from the loaded model with a parameter-count check. The benchmark preflight now requires loaded-model provenance and refuses the old mislabeled bridge. Review fixes ensure truthful final import credit, setup-failure metadata, cancellation evidence, and timeout cleanup.

The new bridge and final feedback changes have local regression coverage; they have **not** yet been validated with a live actual-32B benchmark. No second benchmark was started against the unchanged disqualified endpoint.
