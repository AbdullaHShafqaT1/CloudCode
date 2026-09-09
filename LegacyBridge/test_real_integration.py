import asyncio
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

# Add legacynode to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'legacynode'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from legacynode.core.llm_client import LLMClient, LLMConfig

COLAB_TUNNEL_URL = os.environ.get('TUNNEL_URL', 'https://itunes-dive-sherman-mpg.trycloudflare.com')
MODEL_NAME = os.environ.get('LLM_MODEL', 'qwen2.5-coder:32b')

PROMPT = '''Analyze the following three interconnected source files:

# === File A: auth_store.py ===
class UserSession:
    def __init__(self, user_id: int, token: str, is_active: bool = True):
        self.user_id = user_id
        self.token = token
        self.is_active = is_active

class SessionStore:
    def __init__(self):
        self._sessions = {
            "tok_adm_1": UserSession(user_id=101, token="tok_adm_1", is_active=True),
            "tok_usr_2": UserSession(user_id=201, token="tok_usr_2", is_active=True)
        }

    def get_session(self, token: str):
        return self._sessions.get(token)

# === File B: permissions.py ===
from auth_store import SessionStore

class PermissionManager:
    ADMIN_IDS = [101, 102]

    def __init__(self, store: SessionStore):
        self.store = store

    def check_admin_access(self, token: str) -> dict:
        session = self.store.get_session(token)
        if not session:
            return {"valid": False, "reason": "No session"}

        # Cross-file interaction: Accessing session fields
        if not session["is_active"]:
            return {"valid": False, "reason": "Inactive"}

        return {"valid": True, "uid": str(session["user_id"])}

# === File C: account_api.py ===
from permissions import PermissionManager
from auth_store import SessionStore

def handle_admin_action(token: str, action: str) -> tuple:
    store = SessionStore()
    perm_mgr = PermissionManager(store)

    auth = perm_mgr.check_admin_access(token)
    if not auth.get("valid"):
        return {"error": auth.get("reason")}, 401

    # Cross-file interaction: Comparing uid with ADMIN_IDS
    if auth["uid"] not in PermissionManager.ADMIN_IDS:
        return {"error": "Forbidden: user is not admin"}, 403

    return {"status": "success", "action": action, "user_id": auth["uid"]}, 200

# === Instructions ===
Please perform a rigorous integration analysis:
1. Trace the execution flow when handle_admin_action("tok_adm_1", "delete_cache") is called.
2. Identify all cross-file integration incompatibilities across these three files.
3. Explain exactly where each incompatibility occurs and why the system fails.
4. Provide the complete corrected code for the affected files.
5. State whether any other files need changes.
6. Provide a concise final diagnosis.'''

async def main():
    base_url = COLAB_TUNNEL_URL.rstrip('/') + '/v1'
    print(f'[INIT] Target Colab Endpoint: {base_url}')
    print(f'[INIT] Target Model: {MODEL_NAME}')

    config = LLMConfig(
        base_url=base_url,
        model=MODEL_NAME,
        api_key='ollama',
        timeout_seconds=180.0,
        max_retries=2,
    )
    client = LLMClient(config)

    print('\n[STEP 1] Probing Colab endpoint health...')
    await client.connect()
    is_healthy = await client.probe_health()
    if not is_healthy:
        print('[ERROR] Colab endpoint unreachable.')
        return
    print('[OK] Endpoint healthy and ready.')

    print('\n' + '='*70)
    print('REQUEST SENT TO COLAB:')
    print(PROMPT)
    print('='*70)

    messages = [{'role': 'user', 'content': PROMPT}]
    t0 = time.time()
    try:
        response = await client.chat(messages=messages, stream=False)
        latency_ms = (time.time() - t0) * 1000

        print('\n' + '='*70)
        print('RESPONSE RECEIVED FROM COLAB:')
        print(response.content)
        print('='*70)

        print(f'\n[METRICS]')
        print(f'HTTP Status: 200 OK')
        print(f'Total Round-Trip Latency: {latency_ms:.1f} ms ({latency_ms/1000:.2f} s)')
        print(f'Total Tokens (approx): {response.total_tokens}')
        print(f'Finish Reason: {response.finish_reason}')
        print(f'Response Length: {len(response.content)} characters')

    except Exception as e:
        print(f'\n[ERROR] Request failed: {e}')
        import traceback
        traceback.print_exc()
    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(main())
