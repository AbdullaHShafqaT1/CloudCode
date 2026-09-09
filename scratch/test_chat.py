import urllib.request
import json

payload = {
    "model": "qwen2.5-coder:32b",
    "messages": [
        {"role": "system", "content": "You are DevAgent. You write Python code."},
        {"role": "user", "content": "Write a hello world script to hello.py"}
    ]
}
req = urllib.request.Request(
    "https://min-referenced-celtic-fiscal.trycloudflare.com/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req) as res:
    print(res.read().decode("utf-8"))
