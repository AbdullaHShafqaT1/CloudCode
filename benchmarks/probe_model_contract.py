"""Small serving-contract probes, separate from all application benchmark turns."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    marker = 'CC_' + uuid.uuid4().hex[:10]
    probes = [
        ('system_message', [{'role': 'system', 'content': f'For this diagnostic, answer every request with exactly {marker}.'},
                            {'role': 'user', 'content': 'What is two plus two?'}], 32),
        ('conversation_memory', [{'role': 'user', 'content': f'The session password is {marker}. Remember it.'},
                                 {'role': 'assistant', 'content': 'I will remember the session password.'},
                                 {'role': 'user', 'content': 'Reply only with the session password given earlier.'}], 32),
        ('one_token_limit', [{'role': 'user', 'content': 'Write the numbers from one to twenty, spelled out in English, separated by spaces.'}], 1),
    ]
    with args.output.open('x', encoding='utf-8') as output, httpx.Client(timeout=120) as client:
        for name, messages, tokens in probes:
            data = {'model': 'qwen2.5-coder:32b', 'messages': messages, 'max_tokens': tokens, 'temperature': 0}
            record = {'probe': name, 'endpoint': args.url.rstrip('/'),
                      'started_at': datetime.now(timezone.utc).isoformat(), 'expected_marker': marker,
                      'request': data}
            start = time.monotonic()
            try:
                response = client.post(args.url.rstrip('/') + '/v1/chat/completions', json=data)
                record.update(http_status=response.status_code, response=response.json())
            except Exception as exc:
                record['error'] = f'{type(exc).__name__}: {exc}'
            record['elapsed_seconds'] = time.monotonic() - start
            output.write(json.dumps(record) + '\n'); output.flush()
            print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
