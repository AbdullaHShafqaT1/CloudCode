"""Extract key evidence from chess-result.json for analysis."""
import json

with open('chess-result.json', 'r', encoding='utf-8') as f:
    r = json.load(f)

print("=== RESULT SUMMARY ===")
print("status:", r['status'])
print("rounds:", r['rounds'])
print("files_created:", r.get('files_created', []))
print("phases_reached:", [v.get('phase') for v in r.get('verification', [])])
print()

print("=== VERIFICATION HISTORY ===")
for i, v in enumerate(r.get('verification', [])):
    print("--- Round", i+1, "---")
    print("  phase:", v.get('phase'))
    print("  passed:", v.get('passed'))
    errors = v.get('errors', [])
    for e in errors[:3]:
        print("  error:", e[:200])
    if len(errors) > 3:
        print("  ... and", len(errors)-3, "more errors")
    print()

print("=== EVENTS TIMELINE ===")
with open('chess-events.jsonl', 'r', encoding='utf-8') as f:
    events = [json.loads(line) for line in f]

for e in events:
    ev = e['event']
    if ev in ('FILE_CREATED', 'FILE_MODIFIED', 'PHASE_TRANSITION', 'STALL_RECOVERY', 'TASK_FINISHED', 
              'INCOMPLETE_ARTIFACT', 'COMMAND_RUN'):
        data_str = json.dumps(e['data'])[:300]
        print("%s  %s  %s" % (e['time'][:19], ev, data_str))
