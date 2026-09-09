import json
import urllib.request

system_message = """You are an Autonomous Software Engineer.
Your goal is to create code files by outputting standard Markdown code blocks with `# filename: <path>` on the first line.
Example:
```python
# filename: hello.py
print("Hello from NodeCore!")
```
When you have created the file and want it executed, write:
```bash
python hello.py
```
When all tasks are finished, output TERMINATE.
"""

user_message = """Please create a sanity file `hello.py` that prints 'Hello from NodeCore autonomous runner!' and then run it with python."""

payload = {
    "model": "qwen2.5-coder:32b",
    "messages": [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message}
    ],
    "temperature": 0.2
}

req = urllib.request.Request(
    "https://min-referenced-celtic-fiscal.trycloudflare.com/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

with urllib.request.urlopen(req) as res:
    data = json.loads(res.read())
    print("REPLY:\n", data["choices"][0]["message"]["content"])
