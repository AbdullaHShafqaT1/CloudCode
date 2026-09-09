import ast
import os
import re
import sys
from typing import Dict, Any, List, Union, Optional, Tuple
from autogen import ConversableAgent, UserProxyAgent, register_function
from autogen.code_utils import extract_code

from node_core.tools import NodeForge, NodePulse, NodeLog, safe_console_print, sanitize_for_console


def extract_code_blocks_with_metadata(
    content: str,
    default_filename: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Extracts code blocks with robust detection of target filenames and languages.
    Hardenings:
    - Safe non-greedy parsing: handles multi-file blocks safely without swallowing adjacent blocks.
    - Gracefully parses code blocks where trailing triple backticks (```) are missing due to token truncation.
    - Explicit '# filename: <path>' extraction on line 1.
    - Falls back to a structured workspace path rather than dropping output.
    """
    if not content or not isinstance(content, str):
        return []

    results: List[Dict[str, Any]] = []
    lines = content.splitlines()
    i = 0
    n = len(lines)
    preceding_lines: List[str] = []

    in_block = False
    current_lang = ""
    current_header = ""
    current_code_lines: List[str] = []
    block_preceding_text = ""

    fence_pattern = re.compile(r"^\s*```(?P<lang>[a-zA-Z0-9_\-]*)(?:[ \t]+(?P<header>[^\r\n]+))?")

    def process_collected_block(lang: str, header: str, code_lines: List[str], prec_text: str, block_idx: int) -> Dict[str, Any]:
        code = "\n".join(code_lines)
        target_filename = None

        # 1. Detect filename from header: ```python filename=main.py or ```python:main.py
        if header:
            h_match = re.search(r'(?:filename|filepath)?[:=]?\s*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)', header, re.IGNORECASE)
            if h_match:
                target_filename = h_match.group(1).strip()

        # 2. Strict check: detect filename from line 1 of code
        code_split = code.splitlines()
        if not target_filename and code_split:
            first_line = code_split[0].strip()
            # Explicit '# filename: <path>' or '// filename: <path>'
            explicit_match = re.search(
                r'^(?:#|//|<!--|;)?\s*(?:filename|filepath|file)\s*[:=]\s*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)',
                first_line,
                re.IGNORECASE
            )
            if explicit_match:
                target_filename = explicit_match.group(1).strip()
            elif re.search(r'^(?:#|//|<!--|;)\s*([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,5})\s*(?:-->)?$', first_line, re.IGNORECASE):
                target_filename = re.search(r'^(?:#|//|<!--|;)\s*([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,5})\s*(?:-->)?$', first_line, re.IGNORECASE).group(1).strip()

        # Check subsequent first 4 lines in case shebang/docstring preceded
        if not target_filename and len(code_split) > 1:
            for line in code_split[1:5]:
                sub_match = re.search(
                    r'^(?:#|//|<!--|;)?\s*(?:filename|filepath|file)\s*[:=]\s*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)',
                    line.strip(),
                    re.IGNORECASE
                )
                if sub_match:
                    target_filename = sub_match.group(1).strip()
                    break

        # 3. Detect filename from preceding markdown lines
        if not target_filename and prec_text:
            last_lines = prec_text.splitlines()[-4:]
            tail = "\n".join(last_lines)
            md_match = re.search(
                r"""(?:(?:file(?:name)?|save as|in|here is|here's)\s*[:=]?\s*[`"']?|###?\s*(?:(?:step\s*)?[0-9]+[\.\):]\s*)?[`"']?|\*\*(?:file:)?\s*(?:(?:step\s*)?[0-9]+[\.\):]\s*)?[`"']?)([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]{1,5})[`"']?""",
                tail,
                re.IGNORECASE
            )
            if md_match:
                cand = md_match.group(1).strip()
                if not cand.lower().endswith((".png", ".jpg", ".gif", ".exe")):
                    target_filename = cand

        # 4. Fallback inference based on content signatures
        if not target_filename:
            code_lower = code.lower()
            if "class chessboard" in code_lower or "class chessgame" in code_lower or "is_check" in code_lower:
                target_filename = "chess_logic.py"
            elif "class ludoboard" in code_lower or "class ludogame" in code_lower:
                target_filename = "ludo_logic.py"
            elif "class sudokuboard" in code_lower or "class sudokugame" in code_lower:
                target_filename = "sudoku_logic.py"
            elif "class chessgui" in code_lower:
                target_filename = "chess_gui.py"
            elif "class ludogui" in code_lower:
                target_filename = "ludo_gui.py"
            elif "class sudokugui" in code_lower:
                target_filename = "sudoku_gui.py"
            elif "unittest.testcase" in code_lower or "def test_" in code_lower:
                target_filename = "test_suite.py"
            elif 'if __name__ == "__main__":' in code and ("gui" in code_lower or "game" in code_lower or "app" in code_lower):
                target_filename = "main.py"
            elif "running guide" in code_lower or "rules of" in code_lower or "how to play" in code_lower:
                target_filename = "RunningGUIDE.txt"

        # 5. Structured workspace fallback: NEVER drop output
        if not target_filename:
            if default_filename:
                target_filename = default_filename
            elif lang in ["python", "py"]:
                target_filename = f"workspace_artifact_{block_idx + 1}.py"
            elif lang in ["bash", "sh", "shell", "cmd", "powershell"]:
                target_filename = None
            elif lang in ["json"]:
                target_filename = f"config_{block_idx + 1}.json"
            else:
                target_filename = f"artifact_{block_idx + 1}.txt"

        # Clean code: remove leading filename directive line if present
        clean_code = code
        if target_filename:
            clean_code = re.sub(r'^(?:#|//|<!--|;)[^\r\n]*(?:filename|filepath|file|\.[a-zA-Z0-9]{1,5})[^\r\n]*\r?\n', '', clean_code, count=1, flags=re.IGNORECASE)

        # Strip accidental trailing TERMINATE inside code block
        clean_code = re.sub(r'^\s*TERMINATE\s*$', '', clean_code, flags=re.MULTILINE).strip()

        return {
            "lang": lang,
            "filename": target_filename,
            "code": clean_code,
            "raw_code": code
        }

    while i < n:
        line = lines[i]
        fence_match = fence_pattern.match(line)

        if fence_match:
            lang = (fence_match.group("lang") or "").strip().lower()
            header = (fence_match.group("header") or "").strip()

            if in_block:
                # If closing fence (no lang, no header)
                if not lang and not header:
                    results.append(process_collected_block(current_lang, current_header, current_code_lines, block_preceding_text, len(results)))
                    in_block = False
                    current_code_lines = []
                    current_lang = ""
                    current_header = ""
                    block_preceding_text = ""
                    preceding_lines = []
                    i += 1
                    continue
                else:
                    # New code fence started without closing previous (truncated block)
                    results.append(process_collected_block(current_lang, current_header, current_code_lines, block_preceding_text, len(results)))
                    in_block = True
                    current_lang = lang
                    current_header = header
                    current_code_lines = []
                    block_preceding_text = "\n".join(preceding_lines[-4:])
                    preceding_lines = []
                    i += 1
                    continue
            else:
                # Starting a code block
                in_block = True
                current_lang = lang
                current_header = header
                current_code_lines = []
                block_preceding_text = "\n".join(preceding_lines[-4:])
                preceding_lines = []
                i += 1
                continue

        if in_block:
            current_code_lines.append(line)
        else:
            preceding_lines.append(line)
        i += 1

    # Gracefully handle truncated code block at EOF (missing closing ```)
    if in_block and current_code_lines:
        results.append(process_collected_block(current_lang, current_header, current_code_lines, block_preceding_text, len(results)))

    # Fallback for LLM responses that output code directly with '# filename: <path>' without opening markdown fences
    if not results:
        raw_sections = re.split(r'(?=^(?:#|//|<!--|;)?\s*(?:filename|filepath|file)\s*[:=]\s*[a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)', content, flags=re.MULTILINE)
        for section in raw_sections:
            sec_clean = section.strip()
            if not sec_clean:
                continue
            first_line = sec_clean.splitlines()[0].strip()
            fn_match = re.search(r'^(?:#|//|<!--|;)?\s*(?:filename|filepath|file)\s*[:=]\s*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)', first_line, re.IGNORECASE)
            if fn_match:
                fname = fn_match.group(1).strip()
                clean_sec = re.sub(r'```[a-zA-Z0-9_\-]*\s*$', '', sec_clean).strip()
                clean_sec = re.sub(r'^(?:#|//|<!--|;)[^\r\n]*(?:filename|filepath|file|\.[a-zA-Z0-9]{1,5})[^\r\n]*\r?\n', '', clean_sec, count=1, flags=re.IGNORECASE)
                clean_sec = re.sub(r'^\s*TERMINATE\s*$', '', clean_sec, flags=re.MULTILINE).strip()
                results.append({
                    "lang": "python" if fname.endswith(".py") else "text",
                    "filename": fname,
                    "code": clean_sec,
                    "raw_code": sec_clean
                })

    return results


def extract_api_contracts(target: str, is_code: bool = False, source_name: str = "") -> str:
    """
    Extracts public function and class signatures from a Python module using AST.
    Generates an exact, unambiguous Markdown API Contract block for downstream GUI,
    test suite, and main application launcher phases.
    """
    if not is_code and os.path.isfile(target):
        source_name = source_name or os.path.basename(target)
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                code_text = f.read()
        except Exception as e:
            return f"[!] Error reading {target} for contract extraction: {e}"
    else:
        code_text = target
        source_name = source_name or "logic_module.py"

    try:
        tree = ast.parse(code_text, filename=source_name)
    except SyntaxError as se:
        return f"[!] Syntax error parsing {source_name} for contract extraction (line {se.lineno}): {se.msg}"

    classes_info = []
    functions_info = []
    constants_info = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            doc = ast.get_docstring(node) or ""
            first_doc = doc.splitlines()[0].strip() if doc else ""
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_name = item.name
                    if m_name == "__init__" or not m_name.startswith("_"):
                        args_list = []
                        num_defaults = len(item.args.defaults)
                        num_args = len(item.args.args)
                        default_offset = num_args - num_defaults
                        for idx, a in enumerate(item.args.args):
                            ann = f": {ast.unparse(a.annotation)}" if a.annotation else ""
                            def_str = ""
                            if idx >= default_offset:
                                d_node = item.args.defaults[idx - default_offset]
                                def_str = f" = {ast.unparse(d_node)}"
                            args_list.append(f"{a.arg}{ann}{def_str}")
                        if item.args.vararg:
                            ann = f": {ast.unparse(item.args.vararg.annotation)}" if item.args.vararg.annotation else ""
                            args_list.append(f"*{item.args.vararg.arg}{ann}")
                        if item.args.kwarg:
                            ann = f": {ast.unparse(item.args.kwarg.annotation)}" if item.args.kwarg.annotation else ""
                            args_list.append(f"**{item.args.kwarg.arg}{ann}")
                        ret_ann = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                        sig = f"{m_name}({', '.join(args_list)}){ret_ann}"
                        m_doc = ast.get_docstring(item) or ""
                        first_m_doc = f" # {m_doc.splitlines()[0].strip()}" if m_doc else ""
                        methods.append(f"    - {sig}{first_m_doc}")
            classes_info.append({
                "name": class_name,
                "doc": first_doc,
                "methods": methods
            })

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f_name = node.name
            if not f_name.startswith("_"):
                args_list = []
                num_defaults = len(node.args.defaults)
                num_args = len(node.args.args)
                default_offset = num_args - num_defaults
                for idx, a in enumerate(node.args.args):
                    ann = f": {ast.unparse(a.annotation)}" if a.annotation else ""
                    def_str = ""
                    if idx >= default_offset:
                        d_node = node.args.defaults[idx - default_offset]
                        def_str = f" = {ast.unparse(d_node)}"
                    args_list.append(f"{a.arg}{ann}{def_str}")
                if node.args.vararg:
                    ann = f": {ast.unparse(node.args.vararg.annotation)}" if node.args.vararg.annotation else ""
                    args_list.append(f"*{node.args.vararg.arg}{ann}")
                if node.args.kwarg:
                    ann = f": {ast.unparse(node.args.kwarg.annotation)}" if node.args.kwarg.annotation else ""
                    args_list.append(f"**{node.args.kwarg.arg}{ann}")
                ret_ann = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                sig = f"{f_name}({', '.join(args_list)}){ret_ann}"
                f_doc = ast.get_docstring(node) or ""
                first_f_doc = f" # {f_doc.splitlines()[0].strip()}" if f_doc else ""
                functions_info.append(f"  - {sig}{first_f_doc}")

        elif isinstance(node, ast.Assign):
            for t_node in node.targets:
                if isinstance(t_node, ast.Name) and t_node.id.isupper():
                    val_preview = ast.unparse(node.value)
                    if len(val_preview) > 60:
                        val_preview = val_preview[:57] + "..."
                    constants_info.append(f"  - {t_node.id} = {val_preview}")

    lines = [
        f"=== EXTRACTED API CONTRACT (from {source_name}) ===",
        "Your code MUST strictly conform to these exact class names, method signatures, and parameters:"
    ]

    if classes_info:
        lines.append("\nPublic Classes & Methods:")
        for c in classes_info:
            doc_str = f" - \"\"\"{c['doc']}\"\"\"" if c['doc'] else ""
            lines.append(f"  class {c['name']}:{doc_str}")
            if c['methods']:
                lines.extend(c['methods'])
            else:
                lines.append("    (no public methods)")

    if functions_info:
        lines.append("\nPublic Module Functions:")
        lines.extend(functions_info)

    if constants_info:
        lines.append("\nExported Constants:")
        lines.extend(constants_info)

    lines.append("==================================================")
    return "\n".join(lines)


def detect_project_profile(task_prompt: str, ws_files: Optional[List[str]] = None) -> Dict[str, str]:
    """Detects target file naming convention based on prompt or existing workspace."""
    p_lower = task_prompt.lower()
    ws_files = ws_files or []

    if "ludo" in p_lower:
        prefix = "ludo"
    elif "sudoku" in p_lower:
        prefix = "sudoku"
    elif "chess" in p_lower:
        prefix = "chess"
    else:
        logic_match = [f for f in ws_files if f.endswith("_logic.py")]
        if logic_match:
            prefix = logic_match[0][:-9]
        else:
            m = re.search(r'([a-zA-Z0-9_\-]+)_logic\.py', task_prompt, re.IGNORECASE)
            if m:
                prefix = m.group(1).lower()
            else:
                prefix = "app"

    return {
        "prefix": prefix,
        "logic": f"{prefix}_logic.py",
        "gui": f"{prefix}_gui.py",
        "tests": f"test_{prefix}.py",
        "main": "main.py",
        "guide": "RunningGUIDE.txt"
    }


def prepare_phased_task_prompt(task_prompt: str) -> str:
    """
    Transforms an open-ended multi-file project prompt into a scoped Phase 1 prompt,
    preventing the LLM from attempting to generate all project files at once
    and hitting remote token caps.
    """
    p_lower = task_prompt.lower()
    if "hello.py" in p_lower or "sanity check" in p_lower:
        return task_prompt

    profile = detect_project_profile(task_prompt, [])
    logic_file = profile["logic"]

    return f"""TASK OBJECTIVE:
{task_prompt.strip()}

================================================================================
STAGED PIPELINE EXECUTION: PHASE 1 OF 5 (Interface & Logic Engine)
================================================================================
MANDATE FOR THIS TURN:
1. Implement ONLY the core logic engine file (`{logic_file}`).
2. Implement all core data models, board/state representations, rules, valid move calculations, and state serialization.
3. STRICT NEGATIVE CONSTRAINTS:
   - ZERO CONVERSATIONAL ADVICE: Do NOT provide human advice, greetings, or conversational intros.
   - DO NOT generate GUI, main entry point, test suite, or documentation in this turn.
   - FORBIDDEN STUBS: Never write '# TODO', '# Implement later', or empty stubs. All logic must be fully implemented.
4. Output 100% complete source code in a single Python code block with '# filename: {logic_file}' on line 1.
================================================================================
"""


def create_coder_agent(llm_config: Dict[str, Any]) -> ConversableAgent:
    """Streamlined autonomous coder agent tailored for Qwen-Coder and dual-agent runner."""
    return ConversableAgent(
        name="CoderAgent",
        system_message="""You are the Autonomous Principal Software Engineer (CoderAgent).
Your objective is to build complete, production-grade, fully functional software projects without missing any required files or features.

STRICT OPERATIONAL RULES & NEGATIVE CONSTRAINTS:
1. ZERO CONVERSATIONAL ADVICE:
   - You are an automated non-interactive engine. Never output conversational advice, pleasantries, step-by-step human instructions (e.g., 'Open file X and indent line Y'), or explanations when an error occurs.
   - Do NOT say 'Here is the code...' or 'I have implemented...'. Output ONLY executable code blocks.

2. COMPLETE RECOVERY ARTIFACTS:
   - When runtime/syntax errors or failed tests are returned by UserProxyRunner, your ONLY valid output is the 100% complete, corrected file containing `# filename: <filepath>` on line 1.
   - Never output diffs, snippets, or partial patches. Always provide the entire, fully implemented file.

3. FORBIDDEN STUBS & PLACEHOLDERS:
   - Never use placeholder comments such as `# TODO`, `# Implement later`, `# Add piece logic here`, or `pass`.
   - All core logic, algorithms, state serialization, and event handlers must be fully and thoroughly implemented.

4. PHASED CODE BLOCK FORMATTING:
   - Output EACH file in its own Markdown code block.
   - ALWAYS place `# filename: <filepath>` on the FIRST line of the code block.
   - Focus exclusively on the file(s) requested for the active phase. Do NOT output downstream files prematurely.

5. TESTS & HEADLESS EXECUTION:
   - Write automated unit tests using the standard `unittest` library.
   - DO NOT run GUI mainloops in test files or bash blocks, as GUI event loops block the non-interactive runner. Test game logic headlessly.

6. TERMINATION:
   - Output TERMINATE on its own line ONLY after all requested phases are completed, all files are saved, and the automated test suite has passed with exit code 0.
""",
        llm_config=llm_config,
    )


def create_user_proxy_runner(
    name: str = "UserProxyRunner",
    workspace_path: Optional[str] = None
) -> UserProxyAgent:
    """Creates a UserProxyAgent with robust Phased Pipeline Execution and Markdown code block interception."""
    ws = os.path.abspath(workspace_path or ".")
    os.makedirs(ws, exist_ok=True)

    user_proxy = UserProxyAgent(
        name=name,
        human_input_mode="NEVER",
        max_consecutive_auto_reply=30,
        is_termination_msg=None,  # Handled cleanly inside custom_execution_reply
        code_execution_config=False,
    )

    # Phased Pipeline State
    phase_state = {
        "current_phase": 1,  # 1: Logic, 2: Contracts, 3: GUI, 4: Tests/Main, 5: Docs, 6: Done
        "contract": "",
        "profile": None,
        "is_simple_task": False,
        "logic_verified": False,
        "gui_verified": False,
        "tests_passed": False
    }

    def custom_execution_reply(recipient, messages=None, sender=None, config=None):
        if messages is None:
            messages = recipient._oai_messages[sender]
        last_message = messages[-1]
        content = last_message.get("content", "")
        if not content:
            return True, "No content received."

        # Scan active workspace inventory
        try:
            ws_files = sorted([f for f in os.listdir(ws) if os.path.isfile(os.path.join(ws, f))])
        except Exception:
            ws_files = []

        # Detect initial prompt & profile if not already set
        if phase_state["profile"] is None:
            initial_prompt = messages[0].get("content", "") if messages else ""
            if "hello.py" in initial_prompt.lower() or "sanity check" in initial_prompt.lower():
                phase_state["is_simple_task"] = True
            phase_state["profile"] = detect_project_profile(initial_prompt, ws_files)

        profile = phase_state["profile"]
        expected_logic = profile["logic"]
        expected_gui = profile["gui"]
        expected_tests = profile["tests"]
        expected_main = profile["main"]
        expected_guide = profile["guide"]

        # Default fallback filename based on active phase
        cur_p = phase_state["current_phase"]
        if cur_p == 1:
            phase_default = expected_logic
        elif cur_p == 3:
            phase_default = expected_gui
        elif cur_p == 4:
            phase_default = expected_tests
        elif cur_p == 5:
            phase_default = expected_guide
        else:
            phase_default = None

        parsed_blocks = extract_code_blocks_with_metadata(content, default_filename=phase_default)

        # Strict termination detection: on its own line or at very end of message
        has_terminate = bool(re.search(r'(?:^\s*TERMINATE\s*$|\bTERMINATE\s*$)', content, re.MULTILINE))

        output_parts = []
        syntax_errors = []
        saved_files = []

        for blk in parsed_blocks:
            lang_lower = blk["lang"]
            target_file = blk["filename"]
            code = blk["code"]

            # Case A: File writing block (Python, Text, JSON, etc.)
            if target_file and lang_lower not in ["bash", "sh", "cmd", "powershell"]:
                rel_path = target_file.strip().replace("\\", "/")
                res = NodeForge.write_file(rel_path, code, workspace_path=ws)
                bytes_written = res.get("bytes_written", len(code)) if isinstance(res, dict) else len(code)
                msg = f"[NodeForge] Successfully created file '{rel_path}' ({bytes_written} bytes)"

                # Syntax check for Python files
                if rel_path.endswith(".py"):
                    try:
                        compile(code, rel_path, 'exec')
                        msg += " [Syntax: OK]"
                        saved_files.append(rel_path)
                    except SyntaxError as se:
                        err_msg = f" [!] SyntaxError on line {se.lineno}: {se.msg}"
                        msg += err_msg
                        syntax_errors.append((rel_path, se.lineno, se.msg))

                output_parts.append(msg)
                safe_console_print(f"[+] {msg}")
                NodeLog.emit("FILE_CREATED", {"file": rel_path, "bytes": bytes_written, "source": "NodeForge"})

            # Case B: Shell / Bash command execution
            elif lang_lower in ["bash", "sh", "shell", "cmd", "powershell"]:
                lines = []
                for line in code.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#!") or stripped.upper() == "TERMINATE":
                        continue
                    if stripped.startswith("#") and not stripped.startswith("# filename"):
                        continue
                    if stripped:
                        lines.append(stripped)

                cmd = " && ".join(lines) if lines else ""
                if cmd:
                    res = NodePulse.execute_command(cmd, cwd=ws)
                    exit_code = res.get("exit_code", 0) if isinstance(res, dict) else 0
                    out = res.get("output") or res.get("stdout") or res.get("stderr") or "" if isinstance(res, dict) else str(res)
                    msg = f"[NodePulse] Executed `{cmd}` (exit code {exit_code}):\n{out}"
                    output_parts.append(msg)
                    safe_console_print(f"[+] {msg}")
                    NodeLog.emit("COMMAND_RUN", {"command": cmd, "exit_code": exit_code, "output": out[:200], "source": "NodePulse"})

        combined = "\n\n".join(output_parts) if output_parts else "No executable code blocks processed."

        # Re-scan workspace files after writes
        try:
            ws_files = sorted([f for f in os.listdir(ws) if os.path.isfile(os.path.join(ws, f))])
        except Exception:
            ws_files = []

        # Simple Task Shortcut (e.g. hello.py)
        if phase_state["is_simple_task"]:
            if has_terminate or any("hello.py" in f for f in ws_files):
                NodeLog.emit("TASK_COMPLETED", {"message": "Simple task verified.", "source": "UserProxyRunner"})
                return True, None
            return True, f"Execution Results:\n{combined}\n\nWorkspace Files: {ws_files}\nPlease output TERMINATE to conclude."

        # Handle Syntax Errors immediately: Demand 100% complete recovery artifact
        if syntax_errors:
            file_err, line_err, desc_err = syntax_errors[0]
            return True, (
                f"Execution Results:\n{combined}\n\n"
                f"[!] CRITICAL SYNTAX ERROR in '{file_err}' on line {line_err}: {desc_err}\n"
                f"STRICT RECOVERY DIRECTIVE:\n"
                f"Output the 100% complete, corrected '{file_err}' in a single Python code block with '# filename: {file_err}' on line 1.\n"
                f"DO NOT output conversational advice or partial diffs."
            )

        # =============================================================
        # Phased Pipeline State Machine Transitions
        # =============================================================

        # -------------------------------------------------------------
        # Phase 1: Core Logic Verification & Phase 2: Contracts Extraction
        # -------------------------------------------------------------
        logic_exists = any(f == expected_logic or f.endswith("_logic.py") for f in ws_files)
        if phase_state["current_phase"] == 1:
            if logic_exists:
                actual_logic = [f for f in ws_files if f == expected_logic or f.endswith("_logic.py")][0]
                logic_full_path = os.path.join(ws, actual_logic)
                contract_str = extract_api_contracts(logic_full_path, is_code=False, source_name=actual_logic)
                phase_state["contract"] = contract_str
                phase_state["logic_verified"] = True
                phase_state["current_phase"] = 3  # Advance to Phase 3 (Phase 2 is Contracts Extraction)

                NodeLog.emit("PHASE_TRANSITION", {
                    "from_phase": 1,
                    "to_phase": 3,
                    "event": "CONTRACT_EXTRACTED",
                    "source_file": actual_logic,
                    "contract_preview": contract_str[:250]
                })

                return True, (
                    f"Execution Results:\n{combined}\n\n"
                    f"[+] Phase 1 (Interface & Logic) VERIFIED: '{actual_logic}' [Syntax: OK]\n\n"
                    f"{contract_str}\n\n"
                    f">>> PHASE 3 DIRECTIVE (GUI / Application Layer):\n"
                    f"Generate ONLY the graphical user interface file ('{expected_gui}').\n"
                    f"CRITICAL ARCHITECTURAL CONSTRAINTS:\n"
                    f"1. You MUST strictly adhere to the Phase 2 API Contract extracted from '{actual_logic}' above.\n"
                    f"2. Do NOT invent method names or change parameter signatures that exist in '{actual_logic}'.\n"
                    f"3. Output the complete source code in a ```python code block with '# filename: {expected_gui}' on line 1.\n"
                    f"4. DO NOT generate main.py, tests, or guide files in this turn."
                )
            else:
                return True, (
                    f"Execution Results:\n{combined}\n\n"
                    f"[!] Phase 1 Incomplete: Core logic engine '{expected_logic}' is missing.\n"
                    f"Please generate '{expected_logic}' now in a complete ```python code block with '# filename: {expected_logic}' on line 1."
                )

        # -------------------------------------------------------------
        # Phase 3: GUI / Application Layer Verification
        # -------------------------------------------------------------
        gui_exists = any(f == expected_gui or f.endswith("_gui.py") for f in ws_files)
        if phase_state["current_phase"] == 3:
            if gui_exists:
                actual_gui = [f for f in ws_files if f == expected_gui or f.endswith("_gui.py")][0]
                phase_state["gui_verified"] = True
                phase_state["current_phase"] = 4  # Advance to Phase 4 (Verification & Scaffolding)

                NodeLog.emit("PHASE_TRANSITION", {
                    "from_phase": 3,
                    "to_phase": 4,
                    "event": "GUI_VERIFIED",
                    "source_file": actual_gui
                })

                contract_reminder = phase_state["contract"] or ""
                return True, (
                    f"Execution Results:\n{combined}\n\n"
                    f"[+] Phase 3 (GUI Layer) VERIFIED: '{actual_gui}' [Syntax: OK]\n\n"
                    f">>> PHASE 4 DIRECTIVE (Verification & Scaffolding):\n"
                    f"Generate:\n"
                    f"1. Automated headless test suite ('{expected_tests}') using standard unittest verifying the logic engine against the contract:\n"
                    f"{contract_reminder}\n"
                    f"2. Application entry point ('main.py') initializing the logic engine and launching '{actual_gui}'.\n\n"
                    f"CRITICAL REQUIREMENTS:\n"
                    f"- Output each file in its own code block with '# filename: <filename>' on line 1.\n"
                    f"- Write tests headlessly. Do NOT invoke GUI mainloop inside '{expected_tests}'.\n"
                    f"- Provide 100% complete source code without stubs or placeholders."
                )
            else:
                return True, (
                    f"Execution Results:\n{combined}\n\n"
                    f"[!] Phase 3 Incomplete: GUI file '{expected_gui}' is missing.\n"
                    f"Please generate '{expected_gui}' strictly conforming to the Phase 2 contract, with '# filename: {expected_gui}' on line 1."
                )

        # -------------------------------------------------------------
        # Phase 4: Test Suite & Scaffolding Automatic Verification
        # -------------------------------------------------------------
        tests_exist = any((f.startswith("test") or f.endswith("_test.py")) and f.endswith(".py") for f in ws_files)
        main_exists = "main.py" in ws_files
        if phase_state["current_phase"] == 4:
            if not tests_exist or not main_exists:
                missing_scaffolding = []
                if not tests_exist:
                    missing_scaffolding.append(f"Unit Tests ('{expected_tests}')")
                if not main_exists:
                    missing_scaffolding.append("Application Entrypoint ('main.py')")
                return True, (
                    f"Execution Results:\n{combined}\n\n"
                    f"[!] Phase 4 Incomplete: Still missing {', '.join(missing_scaffolding)}.\n"
                    f"Please output the missing files with '# filename: <filename>' on line 1."
                )

            # Automated Test Execution via NodePulse
            test_candidates = [f for f in ws_files if (f.startswith("test") or f.endswith("_test.py")) and f.endswith(".py")]
            tfile = test_candidates[0]
            safe_console_print(f"[*] Running automated test suite: python {tfile}")
            res = NodePulse.execute_command(f"python {tfile}", cwd=ws)
            ecode = res.get("exit_code", 0) if isinstance(res, dict) else 0
            tout = res.get("output") or res.get("stdout") or res.get("stderr") or "" if isinstance(res, dict) else str(res)

            if ecode != 0:
                # Diagnostic check for exact imports and tracebacks
                diag_info = ""
                actual_logic = [f for f in ws_files if f == expected_logic or f.endswith("_logic.py")]
                logic_name = actual_logic[0] if actual_logic else expected_logic
                try:
                    if actual_logic:
                        mod_name = actual_logic[0][:-3]
                        dres = NodePulse.execute_command(
                            f"python -c \"import {mod_name}; print('Logic module exports:', [a for a in dir({mod_name}) if not a.startswith('_')])\"",
                            cwd=ws
                        )
                        if dres.get("output"):
                            diag_info += f"\n- {dres.get('output').strip()}"

                    # AST-based import compatibility check between test suite and logic module
                    try:
                        test_code = open(os.path.join(ws, tfile), "r", encoding="utf-8", errors="replace").read()
                        tree = ast.parse(test_code)
                        imported_names = []
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ImportFrom) and node.module == mod_name:
                                imported_names.extend([alias.name for alias in node.names])
                        if imported_names:
                            check_cmd = f"python -c \"import {mod_name}; missing = [n for n in {imported_names!r} if not hasattr({mod_name}, n)]; print('Missing from {mod_name}:', missing)\""
                            cres = NodePulse.execute_command(check_cmd, cwd=ws)
                            if cres.get("output"):
                                diag_info += f"\n- Import Check: `{tfile}` imports {imported_names} from `{mod_name}`. {cres.get('output').strip()}"
                    except Exception:
                        pass

                    # Run direct import to capture exact unsuppressed traceback
                    tres = NodePulse.execute_command(f"python -c \"import {tfile[:-3]}\"", cwd=ws)
                    if tres.get("exit_code") != 0 and tres.get("output"):
                        diag_info += f"\n- Raw Test File Error Output:\n{tres.get('output').strip()[:400]}"
                except Exception:
                    pass

                # Tests failed: Reject termination and demand complete recovery artifact
                return True, (
                    f"Execution Results:\n{combined}\n\n"
                    f"[!] [Automated Verification] Test suite `{tfile}` FAILED (exit code {ecode}):\n"
                    f"{tout[:600]}\n"
                    f"{diag_info}\n\n"
                    f"CRITICAL RECOVERY DIRECTIVE:\n"
                    f"Inspect the failure details above. Ensure that all types, classes, or functions imported by `{tfile}` (and `{expected_gui}`) "
                    f"are explicitly defined and exported in `{logic_name}`, or update `{tfile}` so all imports match `{logic_name}`.\n"
                    f"Generate the 100% complete, corrected file with '# filename: <filename>' on line 1 to resolve the issue.\n"
                    f"Do NOT output conversational advice or partial snippets."
                )

            # Tests passed!
            phase_state["tests_passed"] = True
            phase_state["current_phase"] = 5  # Advance to Phase 5 (Documentation & Completion)
            NodeLog.emit("PHASE_TRANSITION", {
                "from_phase": 4,
                "to_phase": 5,
                "event": "TESTS_PASSED",
                "test_file": tfile
            })

            return True, (
                f"Execution Results:\n{combined}\n\n"
                f"[+] Phase 4 (Automated Verification) PASSED: `{tfile}` exit code 0!\n"
                f"{tout[:300]}\n\n"
                f">>> PHASE 5 DIRECTIVE (Documentation & Completion):\n"
                f"Generate '{expected_guide}' detailing launch commands (`python main.py`), controls, and rules.\n"
                f"Output the guide in a code block with '# filename: {expected_guide}' on line 1.\n"
                f"On a separate line immediately after the code block, output TERMINATE to conclude the project."
            )

        # -------------------------------------------------------------
        # Phase 5: Documentation & Completion
        # -------------------------------------------------------------
        guide_exists = any("guide" in f.lower() or "readme" in f.lower() for f in ws_files)
        if phase_state["current_phase"] == 5:
            if not guide_exists:
                return True, (
                    f"Execution Results:\n{combined}\n\n"
                    f"[!] Phase 5 Incomplete: User guide '{expected_guide}' is missing.\n"
                    f"Please generate '{expected_guide}' with launch instructions, then output TERMINATE."
                )

            if has_terminate:
                phase_state["current_phase"] = 6
                NodeLog.emit("TASK_COMPLETED", {
                    "message": "All 5 phases completed and verified successfully.",
                    "workspace_files": ws_files,
                    "source": "UserProxyRunner"
                })
                return True, None

            return True, (
                f"Execution Results:\n{combined}\n\n"
                f"All 5 phases are complete and verified!\n"
                f"Current Workspace Files ({len(ws_files)}): {ws_files}\n"
                f"Please output TERMINATE on its own line to finish."
            )

        # Default fallback
        if has_terminate:
            return True, None
        return True, f"Execution Results:\n{combined}\n\nWorkspace Files: {ws_files}"

    user_proxy.register_reply([ConversableAgent, None], custom_execution_reply, position=0)
    return user_proxy


def create_user_proxy_agent(
    name: str = "UserProxyRunner",
    work_dir: Optional[str] = None
) -> UserProxyAgent:
    """Alias for create_user_proxy_runner."""
    return create_user_proxy_runner(name=name, workspace_path=work_dir)


def create_architect_agent(llm_config: Dict[str, Any]) -> ConversableAgent:
    return ConversableAgent(
        name="ArchitectAgent",
        system_message="""You are the Lead Architect Agent.""",
        llm_config=llm_config,
    )


def create_dev_agent(llm_config: Dict[str, Any]) -> ConversableAgent:
    return ConversableAgent(
        name="DevAgent",
        system_message="""You are the Software Engineer Agent (DevAgent).""",
        llm_config=llm_config,
    )


def create_pulse_agent(llm_config: Dict[str, Any]) -> ConversableAgent:
    return ConversableAgent(
        name="PulseAgent",
        system_message="""You are the Verification & QA Agent (PulseAgent).""",
        llm_config=llm_config,
    )


def create_link_agent(llm_config: Dict[str, Any]) -> ConversableAgent:
    return ConversableAgent(
        name="LinkAgent",
        system_message="""You are the Cloud & MLOps Agent (LinkAgent).""",
        llm_config=llm_config,
    )


def create_supervisor_agent(llm_config: Dict[str, Any]) -> ConversableAgent:
    return ConversableAgent(
        name="NodeCoreSupervisor",
        system_message="""You are the Supervisor / Controller Agent.""",
        llm_config=llm_config,
    )


def register_node_tools(
    caller_agents: Union[ConversableAgent, List[ConversableAgent]],
    executor_agent: UserProxyAgent,
    tools_dict: Dict[str, Any]
) -> None:
    """Register all toolchain functions with callers (LLMs) and executor (UserProxy)."""
    callers = caller_agents if isinstance(caller_agents, list) else [caller_agents]
    for name, func in tools_dict.items():
        executor_agent.register_for_execution(name=name)(func)
    for caller in callers:
        for name, func in tools_dict.items():
            caller.register_for_llm(name=name, description=func.__doc__ or f"Execute {name}")(func)
