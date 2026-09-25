"""Evidence and bounded phase control for the existing two-agent coding runner.

Acceptance checks are supplied by the caller (or loaded before generation), never
inferred from the coder's completion message. Structural checks alone are not a
claim that an arbitrary natural-language requirement has been implemented.
"""
from __future__ import annotations

import hashlib
import ast
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from node_core.tools import NodeForge, NodeInsight, NodeLog, NodePulse


def command_output(result):
    if not isinstance(result, dict):
        return str(result)
    return "\n".join(str(result[k]) for k in ("output", "stdout", "stderr", "error", "error_message")
                     if result.get(k))


def python_command(*args):
    parts = [sys.executable, *args]
    return subprocess.list2cmdline(parts) if sys.platform == "win32" else shlex.join(parts)


class TaskWorkflow:
    def __init__(self, workspace, task, profile, max_rounds=30, acceptance=None,
                 stall_limit=4, should_stop=None):
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
            raise ValueError("max_rounds must be a positive integer")
        if stall_limit < 1:
            raise ValueError("stall_limit must be positive")
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.task = task
        self.profile = profile
        self.max_rounds = max_rounds
        self.stall_limit = stall_limit
        self.should_stop = should_stop or (lambda: False)
        self.phase = 1
        self.completed_phases = []
        self.rounds = 0
        self.status = "IN_PROGRESS"
        self.reason = "Execution has not finished"
        self.contract = ""
        self.files_created = set()
        self.files_modified = set()
        self.files_read = set()
        self.read_evidence = set()
        self.responses_received = 0
        self._last_feedback = None
        self.commands = []
        self.verification = []
        self.remaining = []
        self.no_progress = 0
        self.seen_states = {}
        # FIX 4: Track error history for adaptive feedback
        self.error_history = []  # list of sorted error tuples per round
        self.consecutive_identical_errors = 0
        # FIX 5: Track whether stall recovery has been attempted
        self._recovery_attempted = False
        self.recovery_evidence = []
        # Snapshot caller-owned criteria before the model can change the workspace.
        if acceptance is None:
            path = self.workspace / ".cloudcode" / "acceptance.json"
            acceptance = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.acceptance = json.loads(json.dumps(acceptance))
        if not isinstance(self.acceptance, dict):
            raise ValueError("Acceptance specification must be an object")
        if "task" in self.acceptance and self.acceptance["task"] != task:
            raise ValueError("Acceptance specification belongs to a different task")
        self.single_file = "hello.py" in task.lower() and "sanity check" in task.lower()
        if self.single_file:
            self.profile["logic"] = "hello.py"
        self.checks = self.acceptance.get("checks", [])
        self.required_files = self.acceptance.get("required_files", [])
        if not isinstance(self.checks, list) or not isinstance(self.required_files, list):
            raise ValueError("Acceptance checks and required_files must be lists")
        for check in self.checks:
            if not isinstance(check, dict) or not all(isinstance(check.get(k), str) and check[k].strip()
                                                     for k in ("requirement", "command")):
                raise ValueError("Each acceptance check needs a requirement and command")
        for name in self.required_files:
            self.path(name)
        protected = self.acceptance.get("protected_files", [])
        if not isinstance(protected, list):
            raise ValueError("protected_files must be a list")
        self.protected_files = {name: hashlib.sha256(self.path(name).read_bytes()).hexdigest() for name in protected}
        criteria_dir = self.workspace / ".cloudcode"
        if criteria_dir.exists():
            for path in criteria_dir.iterdir():
                if path.is_file():
                    self.protected_files[path.relative_to(self.workspace).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()

    def criteria_errors(self):
        errors = []
        for name, digest in self.protected_files.items():
            try:
                unchanged = hashlib.sha256(self.path(name).read_bytes()).hexdigest() == digest
            except OSError:
                unchanged = False
            if not unchanged:
                errors.append(f"Caller-owned acceptance file changed: {name}")
        return errors

    def path(self, name):
        if not isinstance(name, str) or not name:
            raise ValueError("File path must be a nonempty string")
        path = (self.workspace / name).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError(f"Path outside workspace: {name}")
        return path

    def snapshot(self):
        files = {}
        for path in self.workspace.rglob("*"):
            rel = path.relative_to(self.workspace)
            if any(p.startswith(".") or p in {"__pycache__", "node_modules", "nodelog_data", "venv"}
                   for p in rel.parts) or path.suffix == ".bak":
                continue
            if path.is_file() and path.resolve().is_relative_to(self.workspace):
                files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return files

    def context(self):
        scan = NodeInsight.scan_workspace(str(self.workspace))
        files = scan.get("files", []) if scan.get("status") == "success" else []
        result = [f"Workspace: {self.workspace}", f"Inventory: {files[:200]}",
                  f"Verification Python: {sys.executable} ({sys.version.split()[0]}). "
                  f"Use {python_command('-m', 'pip')} for package operations. "
                  "Shell python resolves to this same environment. Inspect installed APIs with "
                  "short Python commands (help, dir, inspect) when uncertain; do not guess symbols."]
        if scan.get("status") != "success":
            result.append("Workspace scan failed: " + str(scan.get("error", "Unknown reader error")))
        budget = 16000
        for name in files:
            if budget <= 0:
                break
            if Path(name).suffix.lower() not in {".py", ".md", ".txt", ".json", ".toml", ".js", ".ts"}:
                continue
            if any(p.startswith(".") or p in {"node_modules", "nodelog_data", "__pycache__"}
                   for p in Path(name).parts):
                continue
            res = NodeInsight.read_file(name, workspace_path=str(self.workspace))
            if res.get("status") == "success":
                content = res.get("content", "")[:min(budget, 4000)]
                result.append(f"FILE {name} (possibly truncated):\n{content}")
                self.files_read.add(name)
                budget -= len(content)
        result.append("To inspect any file, emit a ```read block containing one relative path per line, "
                      "or path:START-END for an inclusive line range. "
                      "Shell blocks run in this workspace; commands must finish without GUI mainloops.")
        result.append("Acceptance requirements: " + json.dumps(self.acceptance))
        if not self.checks:
            result.append("No caller-provided acceptance checks are configured. Implement and test the project; "
                          "final status will remain VERIFICATION_FAILED until task-specific checks are supplied.")
        return "\n\n".join(result)

    def detect_incomplete_artifact(self, code, name):
        """FIX 2: Detect structurally incomplete artifacts beyond fence-level checks."""
        issues = []
        if not code or not code.strip():
            return issues
        # The Python parser understands inline suites, quoted punctuation and
        # triple quotes inside comments/strings. Text heuristics rejected valid
        # repairs such as `def add(a,b): return a+b` in four existing regressions.
        if name.lower().endswith('.py'):
            try:
                compile(code, name, 'exec')
            except SyntaxError as exc:
                issues.append(f"Incomplete or invalid Python artifact {name}: {exc}")
            return issues
        # Unclosed triple-quoted strings
        if code.count('"""') % 2 != 0:
            issues.append(f"Unclosed triple-quote string in {name}")
        if code.count("'''") % 2 != 0:
            issues.append(f"Unclosed triple-quote string in {name}")
        # Truncated at bracket/brace/paren/comma
        stripped = code.rstrip()
        if stripped and stripped[-1] in ('{', '[', '(', ','):
            issues.append(f"Artifact {name} appears truncated (ends with '{stripped[-1]}')")
        # Python file ending with incomplete def/class header
        if name.endswith('.py'):
            lines = code.splitlines()
            if lines:
                last_nonblank = ''
                for line in reversed(lines):
                    if line.strip():
                        last_nonblank = line.strip()
                        break
                if re.match(r'(def|class)\s+\w+', last_nonblank) and not last_nonblank.endswith(':'):
                    issues.append(f"Artifact {name} ends with an incomplete definition")
        return issues

    def dependency_preflight(self):
        """FIX 7: Check whether declared dependencies (requirements.txt) are importable.

        Returns a list of human-readable error strings.  Does NOT install packages.
        This gives the model actionable feedback distinguishing 'library missing'
        from 'wrong import name'.
        """
        req_path = self.workspace / "requirements.txt"
        if not req_path.is_file():
            return []  # No requirements declared yet — nothing to check.
        errors = []
        try:
            text = req_path.read_text(encoding="utf-8")
        except OSError:
            return []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Extract package name (strip version specifiers)
            pkg = re.split(r"[<>=!~;\[\]]", line, 1)[0].strip()
            if not pkg:
                continue
            # Normalise: pip uses dashes but Python import uses underscores
            import_name = pkg.replace("-", "_").lower()
            # Some well-known package→import mappings
            known_mappings = {
                "python_chess": "chess",
                "pillow": "PIL",
                "scikit_learn": "sklearn",
                "beautifulsoup4": "bs4",
                "pyyaml": "yaml",
                "opencv_python": "cv2",
            }
            import_name = known_mappings.get(import_name, import_name)
            probe = self.execute(
                python_command("-c", f"import {import_name}; print('{import_name} OK')"),
                "dependency_check"
            )
            if not self.passed(probe):
                output = command_output(probe)
                if "ModuleNotFoundError" in output or "No module named" in output:
                    errors.append(
                        f"Dependency '{pkg}' (import as '{import_name}') is declared in "
                        f"requirements.txt but NOT installed in this environment. "
                        f"Install it with: {python_command('-m', 'pip', 'install', pkg)} "
                        f"Preserve dependencies explicitly required by the original task."
                    )
                else:
                    errors.append(
                        f"Dependency '{pkg}' probe failed: {output[:300]}"
                    )
        return errors

    @staticmethod
    def classify_import_error(output):
        """FIX 8: Classify an import failure to give targeted feedback.

        Returns (category, detail) where category is one of:
          'missing_package'  — third-party library not installed
          'bad_import_name'  — module exists but imported name is wrong
          'syntax_error'     — file has a syntax error
          'other'            — unclassified
        """
        if "ModuleNotFoundError" in output or "No module named" in output:
            # Extract the module name
            m = re.search(r"No module named ['\"]([^'\"]+)['\"]", output)
            mod = m.group(1) if m else "unknown"
            # Standard-library modules that should always be available
            stdlib = {"sys", "os", "json", "re", "pathlib", "http", "urllib",
                      "unittest", "argparse", "hashlib", "io", "collections",
                      "functools", "itertools", "math", "datetime", "threading",
                      "subprocess", "tempfile", "shutil", "typing", "abc",
                      "dataclasses", "enum", "socket", "html", "xml"}
            top = mod.split(".")[0]
            if top in stdlib:
                return "bad_import_name", mod
            return "missing_package", mod
        if "ImportError: cannot import name" in output:
            m = re.search(r"cannot import name ['\"]([^'\"]+)['\"]", output)
            name = m.group(1) if m else "unknown"
            return "bad_import_name", name
        if "SyntaxError" in output:
            return "syntax_error", output[:300]
        return "other", output[:300]

    def execute(self, command, kind="command", requirement=None):
        if self.should_stop():
            self.stop("CANCELLED", "User requested cancellation")
            return {"status": "aborted", "exit_code": -1, "output": "Cancelled"}
        # A same-size repair within one timestamp tick must not load stale .pyc.
        with tempfile.TemporaryDirectory(prefix="cloudcode-bytecode-") as cache:
            result = NodePulse.execute_command(command, cwd=str(self.workspace), should_stop=self.should_stop,
                                              env={"PYTHONPYCACHEPREFIX": cache, "PYTHONDONTWRITEBYTECODE": "1",
                                                   "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")})
        if not isinstance(result, dict):
            result = {"status": "error", "exit_code": -1, "output": str(result)}
        record = {"command": command, "kind": kind, "requirement": requirement,
                  "round": self.rounds, "exit_code": result.get("exit_code"),
                  "status": result.get("status"), "output": command_output(result)}
        self.commands.append(record)
        if result.get("status") == "cancelled":
            self.stop("CANCELLED", "User requested cancellation")
        NodeLog.emit("COMMAND_RUN", record)
        return result

    @staticmethod
    def passed(result):
        return result.get("exit_code") == 0 and result.get("status") == "success"

    def file_errors(self, names):
        errors = []
        for name in dict.fromkeys(names):
            try:
                path = self.path(name)
                data = path.read_bytes()
                if not data:
                    raise ValueError("empty file")
                if path.suffix.lower() in {".py", ".txt", ".md", ".json", ".toml", ".js", ".ts", ".html", ".css"} and not data.decode("utf-8").strip():
                    raise ValueError("empty text file")
                if path.suffix.lower() == ".py":
                    text = data.decode("utf-8")
                    compile(text, name, "exec")
                    if name == self.profile["logic"] and not self.single_file:
                        tree = ast.parse(text)
                        if not any(isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body):
                            raise ValueError("core logic has no callable API; provide the actual implementation, not a filename or placeholder")
            except (OSError, ValueError, SyntaxError) as exc:
                errors.append(f"{name}: {exc}")
        return errors

    def verify(self, final=False):
        """FIX 3: Partial verification — continue checking even when some files are missing.

        Previously, if any file was missing the method returned immediately
        with only the file-level errors, never running tests or import checks.
        Now all discoverable problems are surfaced in a single pass.
        Missing files are still reported as errors — acceptance criteria are unchanged.
        """
        before = self.snapshot()
        names = [self.profile[k] for k in ("logic", "gui", "main", "tests")]
        if final:
            names += [self.profile["guide"], *self.required_files]
        file_errs = self.file_errors(names) + self.criteria_errors()
        errors = list(file_errs)

        # FIX 3: Determine which files exist so we can still check them
        existing_names = [n for n in names if self.path(n).is_file()]
        existing_py = [n for n in existing_names if n.endswith('.py')]

        # Run test suite if the test file exists (even if other files are missing)
        test_file = self.profile.get("tests", "")
        if test_file and self.path(test_file).is_file():
            with tempfile.TemporaryDirectory(prefix="cloudcode-check-") as evidence_dir:
                evidence = Path(evidence_dir) / "tests.json"
                command_index = len(self.commands)
                test = self.execute(python_command("-I", str(Path(__file__).with_name("verification_runner.py")),
                                    str(self.workspace), str(evidence)), "tests")
                try:
                    measured = json.loads(evidence.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    measured = {}
                if (not self.passed(test) or measured.get("tests_run", 0) < 1
                        or not measured.get("successful") or measured.get("skipped", 0)):
                    errors.append("Generated test suite must run at least one test, with no failures or skips:\n" + command_output(test))
                if len(self.commands) > command_index:
                    self.commands[command_index]["test_result"] = measured
        elif not file_errs or test_file not in [e.split(":")[0] for e in file_errs]:
            # Test file missing but not yet reported via file_errs
            if test_file and not self.path(test_file).is_file():
                errors.append(f"{test_file}: file is missing; test suite cannot run")

        # FIX 7: Run dependency preflight before import checks
        dep_errors = self.dependency_preflight()
        if dep_errors:
            errors.extend(dep_errors)

        # FIX 3: Import check against existing modules even when some files are missing
        importable = [Path(self.profile[k]).stem for k in ("logic", "gui", "main")
                      if self.path(self.profile[k]).is_file()]
        if importable:
            smoke = self.execute(python_command("-c", "import importlib; " +
                                 "; ".join(f"importlib.import_module({m!r})" for m in importable)), "imports")
            if not self.passed(smoke):
                output = command_output(smoke)
                # FIX 8: Classify the import error for better feedback
                category, detail = self.classify_import_error(output)
                if category == "missing_package":
                    errors.append(
                        f"Import failed because package '{detail}' is not installed. "
                        f"Inspect whether it is a missing local module, nested import, or dependency "
                        f"in verification Python {sys.executable}. Install required dependencies "
                        f"using that interpreter; do not remove a task-required library.\n{output}"
                    )
                elif category == "bad_import_name":
                    errors.append(
                        f"Import failed: '{detail}' cannot be imported. "
                        f"The module may exist but you are importing a name that does not. "
                        f"Check the library's actual API documentation.\n{output}"
                    )
                else:
                    errors.append("Application import integration failed:\n" + output)

        if final:
            if not self.checks:
                errors.append("Task-specific acceptance checks are not configured; requirement coverage is unverified.")
            for check in self.checks:
                result = self.execute(check["command"], "acceptance", check["requirement"])
                if not self.passed(result):
                    errors.append(check["requirement"] + ":\n" + command_output(result))
        after = self.snapshot()
        if after != before:
            errors.append("Project files changed during verification; repair the checks and verify the final files again.")
        errors.extend(self.file_errors(names))
        errors.extend(self.criteria_errors())
        self.verification.append({"round": self.rounds, "final": final, "passed": not errors,
                                  "errors": list(dict.fromkeys(errors)), "files_before": before, "files": after})
        return list(dict.fromkeys(errors))

    def consume_messages(self, messages):
        """Consume each transport response once, including the final bounded turn."""
        responses = [m for m in messages if m.get("role") == "user"]
        while self.responses_received < len(responses):
            message = responses[self.responses_received]
            self.responses_received += 1
            self._last_feedback = self.process(message.get("content", ""))
        return self._last_feedback

    def transition(self, phase):
        self.completed_phases = sorted(set(self.completed_phases + [self.phase]))
        NodeLog.emit("PHASE_TRANSITION", {"from_phase": self.phase, "to_phase": phase})
        self.phase = phase

    def stop(self, status, reason):
        self.status, self.reason = status, reason

    def directive(self):
        if self.single_file:
            return "Implement the requested hello.py and repair the reported runtime or acceptance failure."
        p = self.profile
        return {
            1: f"PHASE 1: Implement complete core logic in {p['logic']}.",
            2: "PHASE 2: Repair the core logic so its API contract can be extracted.",
            3: f"PHASE 3: Implement {p['gui']} using this API contract:\n{self.contract}",
            4: f"PHASE 4: Implement {p['main']} and {p['tests']}, one complete file per response. "
               "Write concise meaningful unittest cases using loops/subTest for related cases; "
               "do not enumerate repetitive assertions or invent unchecked fixtures. "
               "Fix import/runtime blockers before adding tests. "
               "Run all tests and resolve failures. Guard application launch with __name__ == '__main__'.",
            5: f"PHASE 5: Write {p['guide']} with dependencies, launch instructions and controls. "
               "Repair remaining requirements and request final verification using TASK_COMPLETE.",
            6: "Verified completion.",
        }[self.phase]

    def repair_checkpoint(self, errors):
        """Give the next response one concrete target and observed source context.

        Keep this self-contained for endpoints with weak conversation retention.
        Reader evidence is recorded but automatic reads do not reset stall counts.
        """
        existing = self.snapshot()
        blockers = [e for e in errors if any(s in e.lower() for s in
                    ('import failed', 'modulenotfounderror', 'cannot import name', 'not installed'))]
        focus = None
        for error in blockers:
            paths = re.findall(r'File "([^"]+\.py)"', error)
            for path in reversed(paths):
                candidate = Path(path)
                if candidate.is_absolute() and candidate.is_relative_to(self.workspace):
                    focus = candidate.relative_to(self.workspace).as_posix()
                    break
            if focus:
                break
        if focus is None:
            keys = {1: ['logic'], 2: ['logic'], 3: ['gui'], 4: ['main', 'tests'], 5: ['guide']}[self.phase]
            needed = [self.profile[k] for k in keys] + list(self.required_files)
            focus = next((name for name in needed if not self.path(name).is_file()), self.profile[keys[-1]])
        context = []
        for name in dict.fromkeys([focus, self.profile['logic']]):
            if name not in existing:
                continue
            result = NodeInsight.read_file(name, workspace_path=str(self.workspace))
            if result.get('status') == 'success':
                value = result.get('content', '')
                self.files_read.add(name)
                context.append(f'CURRENT FILE {name}:\n' + value[:7000] +
                               ('\n[Excerpt only; use a read block for the remaining lines.]' if len(value) > 7000 else ''))
        next_action = (f'NEXT RESPONSE: inspect/fix the blocking import in {focus}. ' if blockers else
                       f'NEXT RESPONSE: produce one concise complete {focus}. ')
        next_action += ('Use an explicitly named file block with a closing fence. Do not repeat other files. '
                        'Read/shell inspection is allowed before repair. Keep all original requirements. '
                        'Use loops for related test cases, never enumerate hundreds of assertions. '
                        'If a file cannot fit, factor cohesive helpers into additional complete files; '
                        'do not omit required features or required public entrypoints.')
        return ('Workspace files: ' + ', '.join(sorted(existing)) + '\n' + '\n\n'.join(context), next_action)

    def process(self, content):
        from node_core.agents import extract_code_blocks_with_metadata, extract_api_contracts
        if self.status != "IN_PROGRESS":
            return None
        if self.should_stop():
            self.stop("CANCELLED", "User requested cancellation")
            return None
        if self.rounds >= self.max_rounds:
            self.stop("MAX_ROUNDS_REACHED", "Configured model response budget exhausted")
            return None
        self.rounds += 1
        before = (self.phase, self.snapshot(), set(self.read_evidence))
        feedback, errors = [], []
        content = content or ""
        defaults = {1: "logic", 2: "logic", 3: "gui", 4: "tests", 5: "guide"}
        for block in extract_code_blocks_with_metadata(content, self.profile[defaults[self.phase]]):
            if self.should_stop():
                self.stop("CANCELLED", "User requested cancellation")
                break
            code, lang, name = block["code"], block["lang"], block["filename"]
            if block.get("routing_error"):
                errors.append(block["routing_error"])
                continue
            if block.get("complete") is False:
                errors.append(f"Incomplete code fence for {name}; response may be truncated. Send this complete file alone with a closing fence.")
                continue
            if lang == "read":
                for name in code.splitlines():
                    name = name.strip()
                    try:
                        match = re.fullmatch(r"(.+):(\d+)-(\d+)", name)
                        options = {}
                        label = name
                        if match:
                            name, start, end = match.groups()
                            options = {"start_line": int(start), "end_line": int(end), "line_numbers": True}
                        self.path(name)
                        res = NodeInsight.read_file(name, workspace_path=str(self.workspace), **options)
                        if res.get("status") != "success":
                            raise ValueError(res.get("error", "Read failed"))
                        self.files_read.add(name)
                        value = res.get('content', '')
                        self.read_evidence.add((label, hashlib.sha256(value.encode()).hexdigest()))
                        feedback.append(f"FILE {label}:\n{value[:16000]}" +
                                        ("\n[Truncated. Request path:START-END for later lines.]" if len(value) > 16000 else ""))
                    except (OSError, ValueError) as exc:
                        errors.append(str(exc))
            elif lang in {"bash", "sh", "shell", "cmd", "powershell"}:
                res = self.execute(code)
                feedback.append(f"Command {code!r}, exit {res.get('exit_code')}:\n{command_output(res)}")
                if not self.passed(res):
                    errors.append("Command failed; inspect its output and repair the cause.")
            elif name:
                try:
                    path = self.path(name)
                    if path.relative_to(self.workspace).parts[0].lower() == ".cloudcode":
                        raise ValueError("The .cloudcode directory is reserved for caller criteria and run evidence")
                    existed = path.exists()
                    if existed and block.get("inferred_filename"):
                        raise ValueError(f"Ambiguous overwrite of {name}; include '# filename: {name}' explicitly")
                    # FIX 2: Check for structurally incomplete artifacts
                    artifact_issues = self.detect_incomplete_artifact(code, name)
                    if artifact_issues:
                        for issue in artifact_issues:
                            errors.append(issue)
                        errors.append(f"Incomplete artifact {name} was not written. Existing file preserved.")
                        continue
                    old_bytes = path.read_bytes() if existed else None
                    old = old_bytes.decode("utf-8") if existed else None
                    if name.lower().endswith(".py"):
                        compile(code, name, "exec")
                    res = NodeForge.write_file(name, code, workspace_path=str(self.workspace))
                    if not isinstance(res, dict) or res.get("status") != "success" or not path.is_file() or path.read_bytes() != code.encode("utf-8"):
                        # A failed/readback-mismatched write must preserve the
                        # previously inspected file, even if an adapter misbehaves.
                        if old_bytes is not None:
                            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as restored:
                                restored.write(old_bytes)
                            Path(restored.name).replace(path)
                        elif path.is_file():
                            path.unlink()
                        raise ValueError(f"Write failed: {name}: {res}")
                    if old != code:
                        (self.files_modified if existed else self.files_created).add(name)
                    feedback.append(f"Saved and read back {name}")
                    NodeLog.emit("FILE_CREATED" if not existed else "FILE_MODIFIED", {"file": name})
                except (OSError, ValueError, SyntaxError) as exc:
                    errors.append(str(exc))
        if self.status != "IN_PROGRESS":
            return None
        if self.phase >= 3 and not self.single_file and not self.file_errors([self.profile["logic"]]):
            self.contract = extract_api_contracts(str(self.path(self.profile["logic"])))
        # A malformed documentation block must not hide real test failures in
        # already-written code. Retain every artifact error and do not advance.
        if errors and self.phase in (4, 5):
            errors.extend(self.verify(final=self.phase == 5))
        if not errors:
            if self.single_file:
                errors = self.file_errors(["hello.py", *self.required_files]) + self.criteria_errors()
                if not errors:
                    verification_before = self.snapshot()
                    runtime = self.execute(python_command("hello.py"), "runtime")
                    if not self.passed(runtime):
                        errors.append(command_output(runtime))
                    for check in self.checks:
                        result = self.execute(check["command"], "acceptance", check["requirement"])
                        if not self.passed(result):
                            errors.append(check["requirement"] + ": " + command_output(result))
                    if not self.checks:
                        errors.append("Task-specific acceptance checks are not configured")
                    if self.snapshot() != verification_before:
                        errors.append("Project files changed during verification; verify the final files again")
                    errors.extend(self.criteria_errors())
                    self.verification.append({"round": self.rounds, "final": True, "passed": not errors,
                                              "errors": errors, "files": self.snapshot()})
                    if not errors and self.status == "IN_PROGRESS":
                        self.completed_phases = [1, 4]
                        self.phase = 6
                        self.stop("COMPLETED", "Single-file implementation, runtime and acceptance checks passed")
                    elif not self.checks and self.status == "IN_PROGRESS":
                        self.stop("VERIFICATION_FAILED", "Task-specific acceptance checks are not configured")
            elif self.phase in (1, 2):
                errors = self.file_errors([self.profile["logic"]])
                if not errors:
                    if self.phase == 1:
                        self.transition(2)
                    self.contract = extract_api_contracts(str(self.path(self.profile["logic"])))
                    if self.contract.startswith("[!]"):
                        errors.append(self.contract)
                    else:
                        self.transition(3)
            elif self.phase == 3:
                errors = self.file_errors([self.profile["logic"], self.profile["gui"]])
                if not errors:
                    self.transition(4)
            elif self.phase == 4:
                errors = self.verify()
                if not errors:
                    self.transition(5)
            elif self.phase == 5:
                # Final verification always re-runs after the last writes, even if
                # the model omitted its completion token or changed earlier files.
                errors = self.verify(final=True)
                if not errors and self.status == "IN_PROGRESS":
                    self.transition(6)
                    self.stop("COMPLETED", "All phases and configured acceptance checks passed")
                elif self.status == "IN_PROGRESS" and not self.checks and not self.file_errors([self.profile["guide"]]):
                    self.stop("VERIFICATION_FAILED", "Task-specific acceptance checks are not configured")
        self.remaining = errors
        after = (self.phase, self.snapshot(), set(self.read_evidence))
        self.files_created.update(set(after[1]) - set(before[1]))
        self.files_modified.update(name for name in before[1] if name in after[1]
                                   and before[1][name] != after[1][name])
        self.no_progress = self.no_progress + 1 if before == after else 0
        fingerprint = (self.phase, tuple(sorted(after[1].items())), tuple(sorted(after[2])))
        self.seen_states[fingerprint] = self.seen_states.get(fingerprint, 0) + 1

        # FIX 4: Track error history for adaptive feedback
        error_fingerprint = tuple(sorted(set(errors))) if errors else ()
        if self.error_history and self.error_history[-1] == error_fingerprint:
            self.consecutive_identical_errors += 1
        else:
            self.consecutive_identical_errors = 0
        self.error_history.append(error_fingerprint)

        if self.status == "IN_PROGRESS":
            stall_detected = (self.no_progress >= self.stall_limit
                              or self.seen_states[fingerprint] > self.stall_limit)
            if self.rounds >= self.max_rounds:
                self.stop("MAX_ROUNDS_REACHED", "Configured model response budget exhausted")
            # FIX 5: Active stall recovery — attempt recovery before final STALLED
            elif stall_detected:
                if not self._recovery_attempted:
                    self._recovery_attempted = True
                    self.recovery_evidence.append({"round": self.rounds, "files": self.snapshot(),
                                                   "phase": self.phase, "errors": list(errors)})
                    NodeLog.emit("STALL_RECOVERY", {"round": self.rounds,
                                                     "no_progress": self.no_progress})
                    # Grant a bounded recovery window for inspect -> repair ->
                    # verify instead of terminating on the very next response.
                    self.no_progress = 0
                    self.seen_states.clear()
                    # Build a recovery-specific feedback message
                    return self._build_recovery_feedback(errors, feedback)
                else:
                    self.stop("STALLED", "Repeated iterations made no new file or phase progress")
        if self.status != "IN_PROGRESS":
            return None

        # FIX 4: Build adaptive feedback based on error repetition count
        adaptive_prefix = self._build_adaptive_prefix(errors)
        checkpoint, next_action = self.repair_checkpoint(errors)

        return ("Original task requirements (unchanged):\n" + self.task + "\n\n" + checkpoint + "\n\n" +
                "Iteration finished; the overall task is still incomplete. TERMINATE, PHASE_COMPLETE and "
                "TASK_COMPLETE are requests for evaluation, never proof of completion.\n" +
                adaptive_prefix +
                "\n\n".join(feedback)[-16000:] + "\nRemaining checks:\n" + "\n".join(errors)[-12000:] +
                "\n" + self.directive() +
                "\nReturn complete file code blocks with '# filename: <relative path>' on line 1. "
                "For Markdown containing triple-backtick examples, use FOUR backticks for the outer file fence. "
                "If a response was truncated, return one file at a time. Perform repairs yourself using files or shell blocks." +
                "\n" + next_action)

    def _build_adaptive_prefix(self, errors):
        """FIX 4: Build adaptive prefix when errors repeat across rounds."""
        if self.consecutive_identical_errors <= 0 or not errors:
            return ""
        count = self.consecutive_identical_errors
        blockers = [e for e in errors if any(s in e.lower() for s in
                    ('not installed', 'import failed', 'modulenotfounderror', 'cannot import name'))]
        if blockers:
            return ("Repeated import blocker: inspect the installed library API and the failing file "
                    "with read/shell blocks first. Fix the actual import before generating missing files.\n" +
                    "\n".join(blockers)[:3000] + "\n")
        # Identify missing files from errors
        missing = [e.split(":")[0].strip() for e in errors
                   if "No such file" in e or "file is missing" in e or "Errno 2" in e]
        if count == 1:
            parts = []
            for m in missing:
                parts.append(f"{m} remains missing after your previous attempt. "
                             "Your last response did not create it. "
                             f"Produce only {m} in a single code block. "
                             "Keep it small enough to fit in one response.")
            if parts:
                return "\n".join(parts) + "\n"
            return ("Your previous response did not fix the reported errors. "
                    "Try a different approach.\n")
        # count >= 2: FIX 6 — encourage workspace inspection
        existing_files = sorted(f for f in self.snapshot().keys()
                                if not f.startswith(".") and not f.startswith("nodelog_data"))
        parts = [f"The same errors have persisted for {count + 1} consecutive rounds. "
                 "Your previous approach is not working."]
        if existing_files:
            parts.append("Before attempting repair, inspect the current workspace files:")
            read_block = "```read\n" + "\n".join(existing_files[:10]) + "\n```"
            parts.append(read_block)
        for m in missing:
            parts.append(f"Then produce a minimal but complete {m}.")
        if not missing:
            parts.append("Inspect existing files and make targeted repairs.")
        return "\n".join(parts) + "\n"

    def _build_recovery_feedback(self, errors, feedback):
        """FIX 5 + FIX 6 + FIX 9: Build a recovery-specific feedback after stall detection.

        FIX 9: Prioritise dependency/import errors over missing-file errors,
        because an unresolvable import blocks ALL progress.
        """
        existing_files = sorted(f for f in self.snapshot().keys()
                                if not f.startswith(".") and not f.startswith("nodelog_data"))
        phase_files = [self.profile[k] for k in ("logic", "gui", "tests", "main", "guide")]
        missing_files = [f for f in phase_files if not self.path(f).is_file()]

        # FIX 9: Separate blocking errors (dependency/import) from file-level errors
        dep_errors = [e for e in errors if "not installed" in e.lower() or "missing dependency" in e.lower()
                      or "ModuleNotFoundError" in e or "No module named" in e
                      or "cannot import name" in e.lower()]
        other_errors = [e for e in errors if e not in dep_errors]

        checkpoint, next_action = self.repair_checkpoint(errors)
        parts = [
            "Original task requirements (unchanged):\n" + self.task,
            checkpoint,
            "STALL RECOVERY: Your previous responses have not made progress. "
            "This is a recovery attempt before the task is terminated.",
            "",
        ]

        # FIX 9: Lead with the actual blocker
        if dep_errors:
            parts.append("=== BLOCKING ISSUE: DEPENDENCY / IMPORT ERROR ===")
            parts.append("The following dependency or import errors are preventing ALL progress:")
            for de in dep_errors[:3]:
                parts.append(f"  - {de[:500]}")
            parts.append("")
            parts.append(
                "You MUST fix this before any other work. Options:\n"
                f"1. Inspect the environment using {python_command('-m', 'pip', 'list')}; "
                "install missing required packages with this interpreter. Do not rewrite away "
                "a dependency explicitly required by the task.\n"
                "2. If you are importing a name that does not exist in the library, "
                "check the library's real API and fix the import statement.\n"
                "3. If a requirements.txt declares a package, ensure the import name matches."
            )
            parts.append("")

        if existing_files:
            parts.append(f"Files currently in workspace: {', '.join(existing_files)}")
        if missing_files:
            parts.append(f"Files still missing: {', '.join(missing_files)}")
            if not dep_errors:
                parts.append(f"Produce ONLY {missing_files[0]} in this response. "
                             "Do not include any other files. "
                             "Keep it complete but concise.")
            else:
                parts.append(
                    "Fix the blocking import errors first, then produce missing files."
                )
        else:
            parts.append("All expected files exist but verification is failing.")
            parts.append("Inspect the existing files and make targeted repairs.")
        if existing_files:
            parts.append("")
            parts.append("Inspect existing files first:")
            read_block = "```read\n" + "\n".join(existing_files[:10]) + "\n```"
            parts.append(read_block)
        parts.append("")
        if other_errors:
            parts.append("Other remaining errors:")
            parts.extend(other_errors[-5:])
        parts.append("")
        parts.append(self.directive())
        if feedback:
            parts.append("Latest tool evidence:\n" + "\n".join(feedback)[-16000:])
        parts.append(
            "Return complete file code blocks with '# filename: <relative path>' on line 1. "
            "If a response was truncated, return one file at a time.")
        parts.append(next_action)
        return "\n".join(parts)

    def report(self):
        phase_files = ["hello.py"] if self.single_file else [self.profile[k] for k in ("logic", "gui", "tests", "main", "guide")]
        expected = list(dict.fromkeys(phase_files + self.required_files))
        remaining = list(self.remaining)
        if self.status != "COMPLETED":
            remaining += [f"Missing deliverable: {name}" for name in expected if not self.path(name).is_file()]
            remaining += [check["requirement"] for check in self.checks
                          if not any(c["kind"] == "acceptance" and c["requirement"] == check["requirement"]
                                     and c["status"] == "success" and c["exit_code"] == 0
                                     for c in self.commands if c["round"] == self.rounds)]
        return {"status": self.status, "termination_reason": self.reason, "original_task": self.task,
                "rounds": self.rounds, "max_rounds": self.max_rounds, "current_phase": self.phase,
                "completed_phases": self.completed_phases, "files_expected": expected,
                "files_actual": self.snapshot(), "files_created": sorted(self.files_created),
                "files_modified": sorted(self.files_modified), "files_read": sorted(self.files_read),
                "read_evidence": sorted(self.read_evidence), "responses_received": self.responses_received,
                "commands": self.commands, "verification": self.verification,
                "recovery_evidence": [{**r, "files_changed": r["files"] != self.snapshot(),
                                       "phase_advanced": self.phase > r["phase"],
                                       "errors_changed": r["errors"] != self.remaining}
                                      for r in self.recovery_evidence],
                "acceptance": self.acceptance, "remaining_requirements": list(dict.fromkeys(remaining))}
