"""Regression tests use real AutoGen routing, local files and local commands."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from autogen import ConversableAgent
from node_core import core
from node_core.agents import create_user_proxy_runner, detect_project_profile
from node_core.workflow import TaskWorkflow, command_output, python_command
from node_core.tools import NodeInsight, NodeForge


TASK = "Create a two-player app with a GUI, main.py, tests, and instructions."


def block(name, code):
    return f"```python\n# filename: {name}\n{code}\n```\nTERMINATE"


@pytest.fixture
def workflow(tmp_path):
    return TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK), max_rounds=12,
                        acceptance={"checks": [{"requirement": "Addition returns the sum",
                                     "command": python_command("-c", "from app_logic import add; assert add(2, 3) == 5")} ]})


def scaffold(w, *, failing=False, no_tests=False):
    sources = {
        "app_logic.py": "def add(a, b):\n    return a + b\n",
        "app_gui.py": "from app_logic import add\ndef display():\n    return str(add(1, 2))\n",
        "main.py": "from app_gui import display\nif __name__ == '__main__':\n    print(display())\n",
        "test_app.py": "import unittest\nfrom app_logic import add\nclass TestApp(unittest.TestCase):\n"
                       f"    def test_add(self):\n        self.assertEqual(add(2, 3), {6 if failing else 5})\n",
        "RunningGUIDE.txt": "Run python main.py. No additional dependencies.\n",
    }
    if no_tests:
        sources["test_app.py"] = "print('pretend success')\n"
    for name, text in sources.items():
        (w.workspace / name).write_text(text, encoding="utf-8")


@pytest.mark.parametrize("phase", [1, 2, 3, 4])
def test_early_terminate_never_completes(workflow, phase):
    workflow.phase = phase
    assert workflow.process("TERMINATE") is not None
    assert workflow.status == "IN_PROGRESS"
    assert workflow.phase == phase


def test_terminate_with_logic_continues(workflow):
    reply = workflow.process(block("app_logic.py", "def add(a,b):\n    return a+b"))
    assert "PHASE 3" in reply
    assert workflow.completed_phases == [1, 2]
    assert workflow.status == "IN_PROGRESS"


def test_phase_two_extracts_existing_valid_contract(workflow):
    scaffold(workflow)
    workflow.phase = 2
    assert "PHASE 3" in workflow.process("TERMINATE")
    assert workflow.status != "COMPLETED"


@pytest.mark.parametrize("token", ["TERMINATE", "TASK_COMPLETE", "PHASE_COMPLETE", "ITERATION_COMPLETE", "Done"])
def test_final_completion_requires_actual_checks(workflow, token):
    scaffold(workflow)
    workflow.process(token)
    workflow.process(token)
    workflow.process(token)
    workflow.process(token)
    assert workflow.status == "COMPLETED"
    assert workflow.completed_phases == [1, 2, 3, 4, 5]
    assert any(c["kind"] == "acceptance" for c in workflow.commands)
    assert workflow.verification[-1]["passed"]


@pytest.mark.parametrize("failing,no_tests", [(True, False), (False, True)])
def test_failed_or_zero_tests_block_phase_four(workflow, failing, no_tests):
    scaffold(workflow, failing=failing, no_tests=no_tests)
    workflow.phase = 4
    assert workflow.process("TERMINATE") is not None
    assert workflow.phase == 4
    assert "test suite" in workflow.remaining[0]


def test_runs_all_test_files(workflow):
    scaffold(workflow)
    (workflow.workspace / "test_second.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail('second file failed')")
    workflow.phase = 4
    workflow.process("TERMINATE")
    assert workflow.phase == 4
    assert "second file failed" in str(workflow.remaining)


def test_missing_required_file_blocks_final(workflow):
    scaffold(workflow)
    workflow.required_files.append("missing.json")
    workflow.phase = 5
    workflow.process("TERMINATE")
    assert workflow.status != "COMPLETED"
    assert "missing.json" in str(workflow.remaining)


def test_final_edits_invalidate_previous_pass(workflow):
    scaffold(workflow)
    workflow.phase = 4
    workflow.process("TERMINATE")
    assert workflow.phase == 5
    workflow.process(block("app_logic.py", "def add(a,b):\n    return 0"))
    assert workflow.status != "COMPLETED"
    assert not workflow.verification[-1]["passed"]


def test_acceptance_failure_blocks_completion(workflow):
    scaffold(workflow)
    workflow.checks[0]["command"] = python_command("-c", "raise AssertionError('feature missing')")
    workflow.phase = 5
    workflow.process("TERMINATE")
    assert workflow.status != "COMPLETED"
    assert "feature missing" in str(workflow.remaining)


def test_absent_acceptance_is_honestly_unverified(tmp_path):
    w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK))
    scaffold(w)
    w.phase = 5
    w.process("TERMINATE")
    assert w.status == "VERIFICATION_FAILED"


def test_write_failure_not_reported_as_success(workflow, monkeypatch):
    monkeypatch.setattr(NodeForge, "write_file", lambda *a, **kw: {"status": "error", "error": "denied"})
    reply = workflow.process(block("app_logic.py", "x = 1"))
    assert "Write failed" in reply
    assert not workflow.files_created
    assert workflow.phase == 1


def test_syntax_error_does_not_overwrite_working_file(workflow):
    scaffold(workflow)
    original = (workflow.workspace / "app_logic.py").read_text()
    workflow.process(block("app_logic.py", "def broken("))
    assert (workflow.workspace / "app_logic.py").read_text() == original
    assert workflow.phase == 1


@pytest.mark.parametrize("content", ["TERMINATE", "", "I am finished", block("unrelated.txt", "same")])
def test_repetitions_stall(workflow, content):
    for _ in range(2 * workflow.stall_limit + 2):
        workflow.process(content)
    assert workflow.status == "STALLED"
    assert workflow.rounds <= 2 * workflow.stall_limit + 1  # Initial window plus bounded recovery window.


def test_read_tool_returns_actual_workspace_file(workflow):
    (workflow.workspace / "existing.txt").write_text("Useful existing project context")
    reply = workflow.process("```read\nexisting.txt\n```")
    assert "Useful existing project context" in reply
    assert "existing.txt" in workflow.files_read


def test_reader_scan_and_errors_are_real(workflow, monkeypatch):
    (workflow.workspace / "real.txt").write_text("hello")
    assert NodeInsight.scan_workspace(str(workflow.workspace))["files"] == ["real.txt"]
    assert NodeInsight.read_file("missing.txt", str(workflow.workspace))["status"] == "error"
    monkeypatch.setattr(NodeInsight, "get_tool", lambda *a: None)
    assert NodeInsight.scan_workspace(str(workflow.workspace))["status"] == "error"


def test_both_output_streams_preserved():
    assert command_output({"stdout": "diagnostic", "stderr": "failure"}) == "diagnostic\nfailure"


def test_cancel_before_writes(workflow):
    workflow.should_stop = lambda: True
    workflow.process(block("app_logic.py", "x=1"))
    assert workflow.status == "CANCELLED"
    assert not (workflow.workspace / "app_logic.py").exists()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limits_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        create_user_proxy_runner(workspace_path=str(tmp_path), max_rounds=limit)


def fake_coder(responses):
    coder = ConversableAgent("CoderAgent", llm_config=False, human_input_mode="NEVER")
    stream = iter(responses)
    coder.register_reply([ConversableAgent, None], lambda *a, **kw: (True, next(stream, None)), position=0)
    return coder


@pytest.mark.parametrize("limit", [1, 2, 35])
def test_configured_limit_and_last_response_processed(tmp_path, monkeypatch, limit):
    responses = [block("app_logic.py", f"x = {i}") for i in range(limit)]
    monkeypatch.setattr(core, "create_coder_agent", lambda _: fake_coder(responses))
    result = core.NodeCore(workspace_root=str(tmp_path), llm_config={"mock": True}).start_task(TASK, max_rounds=limit)
    assert result["status"] == "MAX_ROUNDS_REACHED"
    assert result["rounds"] == limit
    assert f"x = {limit-1}" in (tmp_path / "app_logic.py").read_text()
    assert json.loads(Path(result["report_path"]).read_text())["status"] == "MAX_ROUNDS_REACHED"


def test_normal_early_conversation_exit_is_not_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "create_coder_agent", lambda _: fake_coder([None]))
    result = core.NodeCore(workspace_root=str(tmp_path), llm_config={"mock": True}).start_task(TASK)
    assert result["status"] == "INCOMPLETE"
    assert "before verification" in result["termination_reason"]


def test_completed_on_last_allowed_response(tmp_path, monkeypatch):
    w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK))
    scaffold(w)
    monkeypatch.setattr(core, "create_coder_agent", lambda _: fake_coder(["TERMINATE"] * 4))
    acceptance = {"checks": [{"requirement": "Addition", "command": python_command("-c", "from app_logic import add; assert add(1,2)==3")} ]}
    result = core.NodeCore(workspace_root=str(tmp_path), llm_config={"mock": True}).start_task(TASK, max_rounds=4, acceptance=acceptance)
    assert result["status"] == "COMPLETED"
    assert result["rounds"] == 4


def test_acceptance_snapshot_cannot_be_changed_by_coder(workflow):
    (workflow.workspace / ".cloudcode").mkdir()
    path = workflow.workspace / ".cloudcode" / "acceptance.json"
    path.write_text('{"checks": []}')
    assert workflow.checks
    workflow.process(block(".cloudcode/acceptance.json", "{}"))
    assert "reserved" in str(workflow.remaining)


def test_cloud_base_url_normalization():
    assert core.create_cloud_llm_config("https://example.test/v1/")["config_list"][0]["base_url"] == "https://example.test/v1"


@pytest.mark.parametrize("status", ["COMPLETED", "IN_PROGRESS", "MAX_ROUNDS_REACHED", "FAILED", "ABORTED", "STALLED", "VERIFICATION_FAILED", "TIMEOUT"])
def test_gui_displays_actual_task_status(status):
    import queue
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from launcher_gui import NodeCoreLauncherApp
    app = NodeCoreLauncherApp.__new__(NodeCoreLauncherApp)
    for name in ("start_btn", "stop_btn", "runtime_status_badge", "status_bar", "root",
                 "_refresh_workspace_files", "_append_agent_stream"):
        setattr(app, name, Mock())
    app.log_queue = queue.Queue()
    app.log_queue.put(("task_finished", True, {"status": status}))
    app._process_log_queue()
    assert app.runtime_status_badge.configure.call_args.kwargs["text"] == f"● {status}"
    tag = app._append_agent_stream.call_args.args[1]
    assert (tag == "tag_success") == (status == "COMPLETED")


def test_model_exception_is_failed_and_keeps_evidence(tmp_path, monkeypatch):
    coder = ConversableAgent("CoderAgent", llm_config=False, human_input_mode="NEVER")
    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")
    coder.register_reply([ConversableAgent, None], fail, position=0)
    monkeypatch.setattr(core, "create_coder_agent", lambda _: coder)
    result = core.NodeCore(workspace_root=str(tmp_path), llm_config={"mock": True}).start_task(TASK)
    assert result["status"] == "FAILED"
    assert result["termination_reason"] == "model unavailable"
    assert Path(result["report_path"]).exists()


def test_filename_only_is_not_a_logic_engine(workflow):
    workflow.process("```text\napp_logic.py\n```")
    assert workflow.phase == 1
    assert not (workflow.workspace / "app_logic.py").exists()
    assert workflow.remaining


def test_nearest_heading_selects_file_not_earlier_import_reference(workflow):
    text = "Implement app_gui.py using methods in `app_logic.py`.\n\n### app_gui.py\n\n```python\nfrom app_logic import Game\n```"
    workflow.phase = 3
    workflow.process(text)
    assert (workflow.workspace / "app_gui.py").read_text() == "from app_logic import Game"
    assert not (workflow.workspace / "app_logic.py").exists()


def test_truncated_file_is_not_written(workflow):
    reply = workflow.process("```python\n# filename: app_logic.py\ndef partial():\n    return 1")
    assert "truncated" in reply
    assert not (workflow.workspace / "app_logic.py").exists()


@pytest.mark.asyncio
async def test_legacy_session_uses_real_result(tmp_path, monkeypatch):
    from node_core.schemas import SessionHandle, PhaseStatus
    handle = SessionHandle(session_id="regression", project_id="test", workspace_path=str(tmp_path))
    data = {"llm_config": {"mock": True}, "max_rounds": 7, "cancelled": False}
    monkeypatch.setitem(core._ACTIVE_SESSIONS, "regression", data)
    def start(self, prompt, **kwargs):
        assert not kwargs["should_stop"]()
        assert kwargs["max_rounds"] == 7
        return {"status": "MAX_ROUNDS_REACHED", "completed_phases": [1, 2]}
    monkeypatch.setattr(core.NodeCore, "start_task", start)
    updates = [u async for u in core.execute_goal(handle, TASK)]
    assert updates[-1].status == PhaseStatus.FAILED
    assert updates[-1].details["status"] == "MAX_ROUNDS_REACHED"
    assert data["steps_completed"] == 2


def test_sanity_preset_runs_and_verifies_exact_output(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from launcher_gui import DEFAULT_TASK_PROMPT, run_autonomous_orchestrator
    monkeypatch.setattr(core, "create_coder_agent", lambda _: fake_coder([
        block("hello.py", 'print("Hello from NodeCore autonomous runner!")')]))
    result = run_autonomous_orchestrator(str(tmp_path), "https://example.test", DEFAULT_TASK_PROMPT, max_rounds=1)
    assert result["status"] == "COMPLETED"
    assert result["files_expected"] == ["hello.py"]


def test_sanity_terminate_without_file_is_incomplete(tmp_path):
    w = TaskWorkflow(tmp_path, "Create hello.py sanity check", detect_project_profile(TASK))
    w.process("TERMINATE")
    assert w.status != "COMPLETED"


@pytest.mark.parametrize("status,expected", [("COMPLETED", 0), ("STALLED", 1)])
def test_cli_limit_forwarding_and_exit_status(monkeypatch, status, expected):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_orchestrator
    launch = Mock(return_value={"status": status})
    monkeypatch.setattr(run_orchestrator, "launch_orchestrator", launch)
    monkeypatch.setattr(sys, "argv", ["run_orchestrator.py", "--non-interactive", "--max-rounds", "37"])
    assert run_orchestrator.main() == expected
    assert launch.call_args.kwargs["max_rounds"] == 37
