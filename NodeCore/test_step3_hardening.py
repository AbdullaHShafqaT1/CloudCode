"""Real filesystem/process regressions for Step 3; no live model endpoint."""
import asyncio
import json
import threading
import time
import difflib
from pathlib import Path

import pytest
from autogen import ConversableAgent
from node_core import core
from node_core.agents import create_user_proxy_runner, detect_project_profile, extract_code_blocks_with_metadata
from node_core.tools import NodeInsight, NodePulse, NodeForge, _run_async
from node_core.workflow import TaskWorkflow, python_command

TASK = "Create an addition utility with GUI, launcher, tests and guide"


def workflow(tmp_path, acceptance=None, **kwargs):
    return TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK), acceptance=acceptance, **kwargs)


def scaffold(w):
    sources = {"logic": "def add(a,b): return a+b", "gui": "from app_logic import add",
               "main": "from app_gui import add", "tests": "import unittest\nclass T(unittest.TestCase):\n def test_sum(self):\n  from app_logic import add\n  self.assertEqual(add(2,3),5)", "guide": "Run python main.py"}
    for key, value in sources.items():
        (w.workspace / w.profile[key]).write_text(value, encoding="utf-8")


def test_different_reads_are_progress(tmp_path):
    w = workflow(tmp_path, max_rounds=15)
    for i in range(7):
        (tmp_path / f"context{i}.txt").write_text(f"unique context {i}")
    for i in range(7):
        reply = w.process(f"```read\ncontext{i}.txt\n```")
        assert f"unique context {i}" in reply
        assert w.status == "IN_PROGRESS"


def test_line_range_reads_and_repeat_stall(tmp_path):
    w = workflow(tmp_path, max_rounds=15)
    (tmp_path / "large.txt").write_text("\n".join(f"line {i}" for i in range(1, 1001)))
    reply = w.process("```read\nlarge.txt:900-902\n```")
    assert "line 900" in reply and "line 902" in reply and "line 899" not in reply
    for _ in range(2 * w.stall_limit + 1):
        w.process("```read\nlarge.txt:900-902\n```")
    assert w.status == "STALLED"
    assert w.rounds <= 2 * w.stall_limit + 1


def test_fake_test_count_cannot_complete(tmp_path):
    w = workflow(tmp_path)
    scaffold(w)
    (tmp_path / w.profile["tests"]).write_text("print('Ran 42 tests')")
    errors = w.verify()
    assert "test suite" in str(errors)
    assert w.commands[0]["test_result"]["tests_run"] == 0


def test_acceptance_cannot_delete_verified_deliverable(tmp_path):
    command = python_command("-c", "from pathlib import Path; Path('app_logic.py').unlink()")
    w = workflow(tmp_path, {"checks": [{"requirement": "Behavior", "command": command}]})
    scaffold(w)
    errors = w.verify(final=True)
    assert "changed during verification" in str(errors)
    assert not w.verification[-1]["passed"]


def test_missing_file_failure_retains_evidence(tmp_path):
    w = workflow(tmp_path)
    assert w.verify()
    assert w.verification and not w.verification[-1]["passed"]


def test_writer_readback_mismatch_restores_original(tmp_path, monkeypatch):
    w = workflow(tmp_path)
    path = tmp_path / "app_logic.py"
    path.write_text("def original(): return 7")
    def corrupt(*args, **kwargs):
        path.write_text("def corrupt(): return 0")
        return {"status": "success"}
    monkeypatch.setattr(NodeForge, "write_file", corrupt)
    reply = w.process("```python\n# filename: app_logic.py\ndef fixed(): return 8\n```")
    assert path.read_text() == "def original(): return 7"
    assert "Write failed" in reply and w.status != "COMPLETED"


@pytest.mark.asyncio
async def test_child_outliving_shell_is_killed(tmp_path):
    from NodePulse.terminal_executor import TerminalExecutorTool
    child = "import time; from pathlib import Path; time.sleep(3); Path('orphan.txt').write_text('alive')"
    parent = f"import subprocess,sys,time; time.sleep(.15); subprocess.Popen([sys.executable,'-c',{child!r}])"
    began = time.monotonic()
    result = await TerminalExecutorTool(str(tmp_path)).run_command(python_command("-c", parent), timeout=0.6)
    assert result["status"] == "timeout"
    assert time.monotonic() - began < 5
    await asyncio.sleep(3.1)
    assert not (tmp_path / "orphan.txt").exists()


@pytest.mark.asyncio
async def test_async_task_cancellation_is_not_swallowed(tmp_path):
    from NodePulse.terminal_executor import TerminalExecutorTool
    command = python_command("-c", "import time; from pathlib import Path; time.sleep(2); Path('alive.txt').write_text('alive')")
    task = asyncio.create_task(TerminalExecutorTool(str(tmp_path)).run_command(command))
    await asyncio.sleep(.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.1)
    assert not (tmp_path / "alive.txt").exists()


def test_literal_terminate_and_normal_comment_preserved():
    code = '# A file containing protocol tokens\nVALUE = """\nTERMINATE\n"""'
    parsed = extract_code_blocks_with_metadata(f"```python filename=protocol.py\n{code}\n```")
    assert parsed[0]["code"] == code


def test_inferred_target_cannot_overwrite_existing_file(tmp_path):
    w = workflow(tmp_path)
    original = "def original(): return 42"
    (tmp_path / "app_logic.py").write_text(original)
    reply = w.process("```python\ndef unrelated(): return 9\n```")
    assert "Ambiguous overwrite" in reply
    assert (tmp_path / "app_logic.py").read_text() == original
    w.process("```python\n# filename: app_logic.py\ndef corrected(): return 43\n```")
    assert "def corrected" in (tmp_path / "app_logic.py").read_text()
    assert w.phase == 3


def test_conflicting_filename_metadata_does_not_write(tmp_path):
    w = workflow(tmp_path)
    reply = w.process("```python filename=app_logic.py\n# filename: app_gui.py\ndef f(): return 1\n```")
    assert "Conflicting filenames" in reply
    assert not (tmp_path / "app_logic.py").exists()
    assert not (tmp_path / "app_gui.py").exists()


def test_caller_owned_check_cannot_be_replaced(tmp_path):
    criteria = tmp_path / ".cloudcode"
    criteria.mkdir()
    (criteria / "check.py").write_text("raise AssertionError('missing requirement')")
    acceptance = {"checks": [{"requirement": "Original requirement", "command": python_command(".cloudcode/check.py")}]}
    w = workflow(tmp_path, acceptance)
    scaffold(w)
    # Simulates a model shell command altering a checker instead of fixing code.
    (criteria / "check.py").write_text("print('all passed')")
    errors = w.verify(True)
    assert "Caller-owned acceptance file changed" in str(errors)
    assert not w.verification[-1]["passed"]


def test_acceptance_for_different_task_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="different task"):
        workflow(tmp_path, {"task": "Unrelated task"})


@pytest.mark.parametrize("status", ["COMPLETED", "INCOMPLETE", "STALLED", "FAILED", "CANCELLED", "TIMEOUT", "MAX_ROUNDS_REACHED"])
def test_cli_and_gui_preserve_every_final_status(monkeypatch, status):
    import queue
    from unittest.mock import Mock
    import run_orchestrator
    from launcher_gui import NodeCoreLauncherApp
    monkeypatch.setattr(run_orchestrator, "launch_orchestrator", Mock(return_value={"status": status}))
    monkeypatch.setattr(__import__("sys"), "argv", ["run_orchestrator.py", "--non-interactive"])
    assert run_orchestrator.main() == (0 if status == "COMPLETED" else 1)
    app = NodeCoreLauncherApp.__new__(NodeCoreLauncherApp)
    for name in ("start_btn", "stop_btn", "runtime_status_badge", "status_bar", "root", "_refresh_workspace_files", "_append_agent_stream"):
        setattr(app, name, Mock())
    app.log_queue = queue.Queue()
    app.log_queue.put(("task_finished", True, {"status": status}))
    app._process_log_queue()
    assert app.runtime_status_badge.configure.call_args.kwargs["text"] == f"● {status}"
    assert (app._append_agent_stream.call_args.args[1] == "tag_success") == (status == "COMPLETED")


def test_gui_mentioning_is_check_does_not_overwrite_logic(tmp_path):
    w = workflow(tmp_path)
    w.profile = detect_project_profile("Build chess")
    (tmp_path / "chess_logic.py").write_text("def is_check(): return False")
    w.phase = 3
    w.process("```python\nclass ChessGUI:\n def paint(self): return self.game.is_check()\n```")
    assert (tmp_path / "chess_logic.py").read_text() == "def is_check(): return False"
    assert "class ChessGUI" in (tmp_path / "chess_gui.py").read_text()


def test_duplicate_history_does_not_repeat_command(tmp_path):
    runner = create_user_proxy_runner(workspace_path=str(tmp_path), task_prompt=TASK)
    command = python_command("-c", "from pathlib import Path; p=Path('counter'); p.write_text(p.read_text()+'x' if p.exists() else 'x')")
    history = [{"role": "assistant", "content": TASK}, {"role": "user", "content": f"```shell\n{command}\n```"}]
    runner.workflow.consume_messages(history)
    runner.workflow.consume_messages(history)
    assert (tmp_path / "counter").read_text() == "x"
    assert runner.workflow.rounds == runner.workflow.responses_received == 1
    history.append(dict(history[-1]))
    runner.workflow.consume_messages(history)
    assert (tmp_path / "counter").read_text() == "xx"


def test_cancellation_interrupts_active_command(tmp_path):
    stopped = threading.Event()
    timer = threading.Timer(0.4, stopped.set)
    w = workflow(tmp_path, should_stop=stopped.is_set)
    timer.start()
    started = time.monotonic()
    try:
        result = w.execute(python_command("-c", "import time; from pathlib import Path; time.sleep(2); Path('escaped.txt').write_text('escaped'); time.sleep(20)"))
    finally:
        timer.cancel()
    assert time.monotonic() - started < 8
    assert result["status"] == "cancelled"
    assert w.status == "CANCELLED"
    assert "SHOULD NOT RUN" not in result["stdout"]
    assert w.commands[-1]["status"] == "cancelled"
    time.sleep(2.1)
    assert not (tmp_path / "escaped.txt").exists(), "Cancelled process was left alive"


def test_real_unified_patch_and_invalid_patch_preserves_file(tmp_path):
    before = "first\nsecond\nthird\n"
    after = "first\nrepaired\nthird\n"
    (tmp_path / "example.txt").write_text(before)
    patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/example.txt", tofile="b/example.txt"))
    result = NodeForge.patch_file("example.txt", patch, str(tmp_path))
    assert result["status"] == "success"
    assert (tmp_path / "example.txt").read_text() == after
    result = NodeForge.patch_file("example.txt", patch, str(tmp_path))
    assert result["status"] == "error"
    assert (tmp_path / "example.txt").read_text() == after


@pytest.mark.asyncio
async def test_legacy_diff_and_path_boundaries(tmp_path):
    from legacynode.tools.file_writer import FileWriter
    from legacynode.tools.file_reader import FileReader
    from legacynode.tools.terminal_executor import TerminalExecutor
    root = tmp_path / "project"
    root.mkdir()
    sibling = tmp_path / "project-other"
    sibling.mkdir()
    (sibling / "private.txt").write_text("preserve")
    writer = FileWriter(str(root))
    await writer.write_file("data.txt", "old\nkeep\n")
    await writer.apply_diff("data.txt", "--- a/data.txt\n+++ b/data.txt\n@@ -1,2 +1,2 @@\n-old\n+new\n keep\n")
    assert (root / "data.txt").read_text() == "new\nkeep\n"
    with pytest.raises(ValueError):
        await writer.apply_diff("data.txt", "--- a/data.txt\n+++ b/data.txt\n@@ -1 +1 @@\n-wrong\n+new\n")
    assert (root / "data.txt").read_text() == "new\nkeep\n"
    with pytest.raises(ValueError):
        await writer.write_file("../project-other/private.txt", "corrupt")
    with pytest.raises(ValueError):
        await FileReader(str(root)).read_file("../project-other/private.txt")
    with pytest.raises(ValueError):
        await TerminalExecutor(str(root)).execute("echo bad", cwd="../project-other")
    assert (sibling / "private.txt").read_text() == "preserve"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unverified", "cancelled", "verified"])
async def test_legacy_controller_completion_gate(tmp_path, monkeypatch, outcome):
    from unittest.mock import AsyncMock, Mock
    from legacynode.core.agent_controller import AgentController
    from legacynode.core.state_manager import StateManager, ExecutionStep
    monkeypatch.setattr(StateManager, "_instance", None)
    state = StateManager(str(tmp_path / "state.db"))
    hub = Mock(push=AsyncMock())
    controller = AgentController(Mock(), state, hub, workspace_root=str(tmp_path))
    if outcome == "verified":
        scaffold(workflow(tmp_path))
        (tmp_path / ".cloudcode").mkdir()
        (tmp_path / ".cloudcode" / "acceptance.json").write_text(json.dumps({"checks": [{"requirement": "Addition", "command": python_command("-c", "from app_logic import add; assert add(2,3)==5") }]}))
    captured = []
    async def respond(*args):
        captured.append(state.current_task)
        state.add_execution_step(ExecutionStep(0, "TASK_COMPLETE"))
        controller._messages_used += 1
        if outcome == "cancelled":
            await controller.stop()
        return "TASK_COMPLETE"
    monkeypatch.setattr(controller, "_execute_with_autogen", respond)
    result = await controller.run_task(TASK)
    expected = {"unverified": "VERIFICATION_FAILED", "cancelled": "CANCELLED", "verified": "COMPLETED"}[outcome]
    assert captured[0].status == expected
    report = json.loads(next((tmp_path / ".cloudcode" / "runs").glob("*.json")).read_text())
    assert report["status"] == expected and report["execution_steps"]
    assert any(call.args[1] == "Task Completed" for call in hub.push.call_args_list) == (outcome == "verified")


def test_production_bridge_unknown_action_is_failure(tmp_path):
    from legacynode.bridge_adapter import BridgeAdapter, BridgeMode
    bridge = BridgeAdapter(mode=BridgeMode.LOCAL, workspace_root=str(tmp_path))
    result = bridge.core_call("nonexistent_action")
    assert not result.ok and "Unsupported" in result.error


@pytest.mark.parametrize("fails", [False, True])
def test_remote_dispatch_uses_actual_gateway_job_result(monkeypatch, fails):
    from unittest.mock import Mock, AsyncMock
    from node_core.tools import NodeLink
    from nodelink.gateway import NodeLink as Gateway
    from nodelink.models import RemoteJobHandle
    gateway = Gateway()
    submit = AsyncMock(return_value=RemoteJobHandle("test", "backend-job-456", "PENDING"))
    if fails:
        submit.side_effect = RuntimeError("backend rejected job")
    monkeypatch.setattr(gateway, "_create_adapter", lambda *args: Mock(run_remote=submit))
    monkeypatch.setattr(NodeLink, "get_gateway", lambda: gateway)
    result = NodeLink.dispatch_remote("test", {"config": {"endpoint": "http://local-test.invalid"}, "command": "python verify.py"})
    if fails:
        assert result["status"] == "error" and "backend rejected" in result["error"]
    else:
        assert result["status"] == "PENDING"
        assert result["job_id"] == "backend-job-456"
        assert json.loads(json.dumps(result))["handle"]["target_id"] == "test"
    submit.assert_awaited_once_with("python verify.py", None)


@pytest.mark.asyncio
async def test_real_legacy_autogen_team_writes_and_verifies(tmp_path, monkeypatch):
    from unittest.mock import Mock, AsyncMock
    from autogen_ext.models.replay import ReplayChatCompletionClient
    from autogen_core import FunctionCall
    from autogen_core.models import CreateResult, RequestUsage
    from legacynode.core.agent_controller import AgentController
    from legacynode.core.state_manager import StateManager
    monkeypatch.setattr(StateManager, "_instance", None)
    state = StateManager(str(tmp_path / ".legacynode" / "state.db"))
    controller = AgentController(Mock(), state, Mock(push=AsyncMock()), workspace_root=str(tmp_path))
    scaffold(workflow(tmp_path))
    (tmp_path / ".cloudcode").mkdir()
    (tmp_path / ".cloudcode" / "acceptance.json").write_text(json.dumps({"required_files": ["extra.txt"], "checks": [{"requirement": "Creates requested text", "command": python_command("-c", "from pathlib import Path; assert Path('extra.txt').read_text()=='actual tool write'")}]}))
    client = ReplayChatCompletionClient([
        CreateResult(finish_reason="function_calls", content=[FunctionCall(id="write1", name="write_file", arguments=json.dumps({"path": "extra.txt", "content": "actual tool write"}))], usage=RequestUsage(prompt_tokens=0, completion_tokens=0), cached=False),
        "TASK_COMPLETE",
    ], model_info={"vision": False, "function_calling": True, "json_output": False, "family": "unknown"})
    monkeypatch.setattr(controller, "_build_model_client", lambda: client)
    await controller.run_task(TASK)
    assert (tmp_path / "extra.txt").read_text() == "actual tool write"
    report = json.loads(next((tmp_path / ".cloudcode" / "runs").glob("*.json")).read_text())
    assert report["status"] == "COMPLETED"
    assert report["verification"][-1]["passed"]
    assert any(step["action"] == "write_file" for step in report["execution_steps"])


def test_terminal_tail_and_both_streams(tmp_path):
    res = NodePulse.execute_command(python_command("-c", "import sys; print('x'*150000); print('FINAL_FAILURE'); print('stderr detail',file=sys.stderr); sys.exit(7)"), cwd=str(tmp_path))
    assert res["exit_code"] == 7 and res["truncated"]
    assert "FINAL_FAILURE" in res["stdout"] and "stderr detail" in res["stderr"]
    assert len(res["stdout"]) <= 100000


def test_runtimeerror_is_not_replayed():
    calls = []
    async def action():
        calls.append(1)
        raise RuntimeError("original failure")
    with pytest.raises(RuntimeError, match="original failure"):
        _run_async(action())
    assert calls == [1]


def test_setup_failure_retains_report(tmp_path):
    result = core.NodeCore(workspace_root=str(tmp_path), llm_config={"mock": True}).start_task(TASK, acceptance=[])
    assert result["status"] == "FAILED"
    report = json.loads(Path(result["report_path"]).read_text())
    assert "specification must be an object" in report["termination_reason"]
    assert report["rounds"] == 0


def test_full_autogen_repair_then_complete(tmp_path, monkeypatch):
    coder = ConversableAgent("CoderAgent", llm_config=False, human_input_mode="NEVER")
    probe = workflow(tmp_path)
    scaffold(probe)
    (tmp_path / "app_logic.py").write_text("def add(a,b): return a-b")
    messages = iter(["TERMINATE", "TERMINATE", "TERMINATE",
                     "```python\n# filename: app_logic.py\ndef add(a,b): return a+b\n```", "TASK_COMPLETE"])
    prompts = []
    def respond(recipient, messages=None, **kwargs):
        prompts.append(messages[-1]["content"])
        return True, next(responses)
    responses = messages
    coder.register_reply([ConversableAgent, None], respond, position=0)
    monkeypatch.setattr(core, "create_coder_agent", lambda _: coder)
    acceptance = {"checks": [{"requirement": "Adds numbers", "command": python_command("-c", "from app_logic import add; assert add(4,5)==9")}]}
    result = core.NodeCore(workspace_root=str(tmp_path), llm_config={"mock": True}).start_task(TASK, max_rounds=5, acceptance=acceptance)
    assert result["status"] == "COMPLETED"
    assert len(prompts) == result["rounds"] == result["responses_received"] == 5
    assert "AssertionError" in prompts[3]
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["messages"] and report["verification"][-1]["passed"]
