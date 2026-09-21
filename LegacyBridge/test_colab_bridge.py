import asyncio
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 console output on Windows
if sys.platform == "win32" and __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

# Add legacynode to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'legacynode'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from legacynode.core.llm_client import LLMClient, LLMConfig

COLAB_TUNNEL_URL = os.environ.get('TUNNEL_URL', 'https://itunes-dive-sherman-mpg.trycloudflare.com')
MODEL_NAME = os.environ.get('LLM_MODEL', 'qwen2.5-coder:32b')

PROMPT = '''We have two interconnected source files:

File 1 (database.py):
def fetch_user(user_id: int):
    if not isinstance(user_id, int):
        raise TypeError("user_id must be int")
    return {"id": user_id, "username": "alice"}

File 2 (api.py):
from database import fetch_user
def get_user_endpoint(params: dict):
    user_id = params.get("user_id")
    return fetch_user(user_id)

Task: Explain why calling get_user_endpoint({"user_id": "101"}) fails, and provide the fixed code for api.py.'''

async def test():
    if not os.environ.get("CLOUDCODE_RUN_LIVE_TESTS"):
        import pytest
        pytest.skip("Live model test requires CLOUDCODE_RUN_LIVE_TESTS; reserved for Step 4")
    base_url = COLAB_TUNNEL_URL.rstrip('/') + '/v1'
    print(f'\n[INIT] Target Endpoint: {base_url}')
    print(f'[INIT] Model: {MODEL_NAME}')

    config = LLMConfig(
        base_url=base_url,
        model=MODEL_NAME,
        api_key='ollama',
        timeout_seconds=120.0,
        max_retries=2,
    )
    client = LLMClient(config)

    print('\n[STEP 1] Probing Colab endpoint health...')
    await client.connect()
    is_healthy = await client.probe_health()
    if not is_healthy:
        print('[ERROR] Colab endpoint is unreachable.')
        return
    print('[OK] Endpoint reachable and healthy!')

    messages = [{'role': 'user', 'content': PROMPT}]

    print('\n' + '='*70)
    print('REQUEST SENT TO COLAB:')
    print(PROMPT)
    print('='*70)

    try:
        t0 = time.time()
        response = await client.chat(messages=messages, stream=False)
        latency = (time.time() - t0) * 1000

        print('\n' + '='*70)
        print('RESPONSE RECEIVED FROM COLAB:')
        print(response.content)
        print('='*70)
        print(f'\n[SUCCESS] Latency: {latency:.1f}ms | Tokens: {response.total_tokens}')

    except Exception as e:
        print('\n[CALL FAILED]:')
        print(f'Type: {type(e).__name__}')
        print(f'Details: {e}')
    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(test())
