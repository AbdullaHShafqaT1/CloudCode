import os
import sys
import re
from pathlib import Path

# Add repo to sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "NodeCore"))

from autogen import ConversableAgent, UserProxyAgent
from autogen.code_utils import extract_code
from NodeCore.node_core.tools import NodeForge, NodePulse, NodeLog, configure_tools

def build_execution_hook(workspace_path: str):
    def custom_code_execution_reply(recipient, messages=None, sender=None, config=None):
        if messages is None:
            messages = recipient._oai_messages[sender]
        last_message = messages[-1]
        content = last_message.get("content", "")
        if not content:
            return True, "No content to process."
        
        if "TERMINATE" in content.upper():
            return True, None

        blocks = extract_code(content)
        if not blocks:
            # If no code blocks, prompt the agent to write code blocks or conclude
            return True, "Please provide actionable code blocks (e.g., ```python # filename: ...``` or ```bash ...```) or reply with TERMINATE if done."

        output_parts = []
        for lang, code in blocks:
            lang_lower = lang.lower() if lang else ""
            # Check for filename comment
            fn_match = re.search(r'(?:#|//)\s*(?:filename|filepath):\s*([^\r\n]+)', code, re.IGNORECASE)
            
            if fn_match:
                rel_path = fn_match.group(1).strip()
                # Clean code of the first comment line
                clean_code = re.sub(r'^(?:#|//)\s*(?:filename|filepath):[^\r\n]*\r?\n', '', code, count=1, flags=re.IGNORECASE)
                res = NodeForge.write_file(rel_path, clean_code, workspace_path=workspace_path)
                status = res.get("status") if isinstance(res, dict) else "ok"
                output_parts.append(f"[NodeForge] Successfully wrote file: {rel_path} (status: {status})")
                NodeLog.emit("FILE_CREATED", {"file": rel_path, "status": status, "source": "NodeForge"})
            
            elif lang_lower in ["bash", "sh", "shell", "cmd", "powershell"]:
                res = NodePulse.execute_command(code.strip(), cwd=workspace_path)
                exit_code = res.get("exit_code", 0) if isinstance(res, dict) else 0
                out = res.get("output") or res.get("stdout") or res.get("stderr") or "" if isinstance(res, dict) else str(res)
                output_parts.append(f"[NodePulse] Command `{code.strip()}` exited with code {exit_code}:\n{out}")
                NodeLog.emit("COMMAND_RUN", {"command": code.strip(), "exit_code": exit_code, "source": "NodePulse"})
            
            elif lang_lower in ["python", "py"]:
                # Default python script write & run if no filename
                default_fn = "temp_exec.py"
                NodeForge.write_file(default_fn, code, workspace_path=workspace_path)
                res = NodePulse.execute_command(f"python {default_fn}", cwd=workspace_path)
                out = res.get("output") or res.get("stdout") or "" if isinstance(res, dict) else str(res)
                output_parts.append(f"[NodePulse] Executed Python block:\n{out}")
                NodeLog.emit("COMMAND_RUN", {"command": f"python {default_fn}", "source": "NodePulse"})

        combined_output = "\n\n".join(output_parts)
        return True, f"Execution Results:\n{combined_output}\n\nProceed with the next step or conclude with TERMINATE if all goals are achieved."

    return custom_code_execution_reply

print("Execution hook successfully constructed!")
