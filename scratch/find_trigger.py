import json
import urllib.request

def test_prompt(sys_txt, usr_txt):
    msgs = []
    if sys_txt:
        msgs.append({"role": "system", "content": sys_txt})
    msgs.append({"role": "user", "content": usr_txt})
    
    payload = {"model": "qwen2.5-coder:32b", "messages": msgs}
    req = urllib.request.Request(
        "https://min-referenced-celtic-fiscal.trycloudflare.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read())
        print(f"SYS: {sys_txt[:30]}... | USR: {usr_txt[:30]}...")
        print("REPLY:", data["choices"][0]["message"]["content"][:100])
        print("-" * 50)

# Test 1: Just user message
test_prompt("", "Build a simple Kanban app in FastAPI.")

# Test 2: User message with target directory
test_prompt("", "Target Directory: C:\\Users\\Acer\\Desktop\\projects\\CloudCodeTesting\nBuild a complete Kanban app.")

# Test 3: System message alone
test_prompt("You are an expert Python engineer.", "Build a simple Kanban app in FastAPI.")
