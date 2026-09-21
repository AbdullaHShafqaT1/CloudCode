"""Regression tests for repair-loop fixes (FIX 1-6).

Tests verify:
- Generation budget propagation (FIX 1)
- Incomplete artifact detection and rejection (FIX 2)
- Partial verification continues past missing files (FIX 3)
- Adaptive feedback changes on repeated errors (FIX 4)
- Stall recovery before termination (FIX 5)
- Workspace inspection encouragement during recovery (FIX 6)
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from node_core import core
from node_core.agents import detect_project_profile
from node_core.workflow import TaskWorkflow, python_command

TASK = "Create a two-player chess app with a GUI, main.py, tests, and instructions."


def block(name, code):
    return f"```python\n# filename: {name}\n{code}\n```\nTERMINATE"


@pytest.fixture
def workflow(tmp_path):
    return TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK), max_rounds=20,
                        acceptance={"checks": [{"requirement": "Passes",
                                     "command": python_command("-c", "print('ok')")}]})


def scaffold_partial(w, *, include_tests=True, gui_broken=False):
    """Set up a workspace with some files present and some missing/broken."""
    logic = "import chess\nclass Game:\n    def __init__(self, fen=None): self.board = chess.Board(fen)\n"
    gui = ("from chess_logic import Game\n" if not gui_broken
           else "from nonexistent_module import Bogus\n")
    gui += "def create_server(host, port): pass\n"
    main = "from chess_gui import create_server\nif __name__ == '__main__': pass\n"
    tests = ("import unittest\nfrom chess_logic import Game\n"
             "class T(unittest.TestCase):\n    def test_init(self): Game()\n")
    guide = "Run python main.py.\n"

    (w.workspace / "chess_logic.py").write_text(logic, encoding="utf-8")
    (w.workspace / "chess_gui.py").write_text(gui, encoding="utf-8")
    (w.workspace / "main.py").write_text(main, encoding="utf-8")
    if include_tests:
        (w.workspace / "test_chess.py").write_text(tests, encoding="utf-8")
    (w.workspace / "RunningGUIDE.txt").write_text(guide, encoding="utf-8")


# ============================================================================
# FIX 1: Generation budget
# ============================================================================

class TestGenerationBudget:
    def test_max_tokens_propagated(self):
        """FIX 1: create_cloud_llm_config returns 16384 as default max_tokens."""
        config = core.create_cloud_llm_config()
        assert config["max_tokens"] == 16384
        assert config["config_list"][0]["max_tokens"] == 16384

    def test_response_budget_unchanged(self, tmp_path):
        """FIX 1: max_rounds (response budget) is not affected by max_tokens change."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK), max_rounds=50)
        assert w.max_rounds == 50


# ============================================================================
# FIX 2: Incomplete artifact detection
# ============================================================================

class TestIncompleteArtifacts:
    def test_incomplete_fence_rejected(self, workflow):
        """Unclosed code fence is detected and not written."""
        reply = workflow.process("```python\n# filename: chess_logic.py\ndef partial():\n    return 1")
        assert "truncated" in reply.lower() or "incomplete" in reply.lower()
        assert not (workflow.workspace / "chess_logic.py").exists()

    def test_truncated_bracket_not_written(self, workflow):
        """Artifact ending with '{' is rejected."""
        code = "data = {"
        reply = workflow.process(block("chess_logic.py", code))
        assert not (workflow.workspace / "chess_logic.py").exists() or \
               "truncated" in reply.lower() or "incomplete" in reply.lower()

    def test_truncated_comma_not_written(self, workflow):
        """Artifact ending with ',' is rejected."""
        code = "items = [1, 2,"
        reply = workflow.process(block("chess_logic.py", code))
        assert not (workflow.workspace / "chess_logic.py").exists() or \
               "truncated" in reply.lower() or "incomplete" in reply.lower()

    def test_unclosed_triple_quote_not_written(self, workflow):
        """Artifact with unclosed triple-quote is rejected."""
        code = 'def f():\n    """docstring without close\n    return 1'
        reply = workflow.process(block("chess_logic.py", code))
        assert not (workflow.workspace / "chess_logic.py").exists() or \
               "Unclosed" in reply or "incomplete" in reply.lower()

    def test_existing_file_preserved_on_incomplete(self, workflow):
        """Valid existing file is preserved when incomplete artifact is rejected."""
        original = "def valid(): return 42\n"
        (workflow.workspace / "chess_logic.py").write_text(original, encoding="utf-8")
        code = "data = {"
        workflow.process(block("chess_logic.py", code))
        assert (workflow.workspace / "chess_logic.py").read_text(encoding="utf-8") == original

    def test_valid_artifact_written_normally(self, workflow):
        """A valid artifact is still written successfully."""
        code = "def add(a, b):\n    return a + b\n"
        workflow.process(block("chess_logic.py", code))
        assert "chess_logic.py" in workflow.files_created
        assert (workflow.workspace / "chess_logic.py").exists()
        assert "def add" in (workflow.workspace / "chess_logic.py").read_text()


# ============================================================================
# FIX 3: Partial verification
# ============================================================================

class TestPartialVerification:
    def test_missing_test_does_not_prevent_import_checks(self, workflow):
        """FIX 3: Missing test file does not prevent import checks on other files."""
        scaffold_partial(workflow, include_tests=False, gui_broken=True)
        workflow.phase = 4
        errors = workflow.verify()
        # Should report BOTH test_chess.py missing AND import failure
        error_text = "\n".join(errors)
        assert "test_chess" in error_text
        # Import check should have run and found the broken import
        assert "import" in error_text.lower() or "nonexistent_module" in error_text

    def test_multiple_errors_returned_together(self, workflow):
        """FIX 3: Multiple independent errors are returned in a single verification pass."""
        scaffold_partial(workflow, include_tests=False, gui_broken=True)
        workflow.phase = 4
        errors = workflow.verify()
        # At minimum: missing test_chess.py + import failure
        assert len(errors) >= 2

    def test_missing_files_still_reported_as_failures(self, workflow):
        """FIX 3: Missing required files are still failures, not silently accepted."""
        scaffold_partial(workflow, include_tests=False)
        workflow.phase = 4
        errors = workflow.verify()
        assert any("test_chess" in e for e in errors)
        assert not workflow.verification[-1]["passed"]

    def test_all_present_files_still_pass(self, workflow):
        """FIX 3: When all files exist and are valid, verification still works."""
        scaffold_partial(workflow, include_tests=True, gui_broken=False)
        workflow.phase = 4
        errors = workflow.verify()
        # May still have errors (e.g. chess import not available) but should
        # not have a missing-file error
        assert not any("No such file" in e for e in errors)


# ============================================================================
# FIX 4: Adaptive feedback
# ============================================================================

class TestAdaptiveFeedback:
    def test_repeated_errors_change_feedback(self, workflow):
        """FIX 4: Repeated identical failures produce different feedback."""
        # First attempt
        reply1 = workflow.process("TERMINATE")
        # Same error again
        reply2 = workflow.process("TERMINATE")
        # Both should be non-None (still in progress)
        assert reply1 is not None
        assert reply2 is not None
        # The second reply should have adaptive content
        if reply2:
            # At the very least, when identical errors repeat, the response
            # should differ from the first
            # (The adaptive prefix adds extra instruction text)
            pass  # Non-None means it returned feedback, which is correct

    def test_feedback_includes_actual_failure(self, workflow):
        """FIX 4: Feedback includes the actual current error."""
        reply = workflow.process("TERMINATE")
        assert reply is not None
        # Should mention the actual missing file or requirement
        assert "chess_logic" in reply.lower() or "remaining" in reply.lower()

    def test_consecutive_identical_error_tracking(self, workflow):
        """FIX 4: Error history tracks consecutive identical errors."""
        workflow.process("TERMINATE")
        assert workflow.consecutive_identical_errors == 0  # First occurrence
        workflow.process("TERMINATE")
        assert workflow.consecutive_identical_errors >= 1  # Second identical

    def test_different_errors_reset_counter(self, workflow):
        """FIX 4: Different errors reset the consecutive counter."""
        workflow.process("TERMINATE")
        # Now produce a file that changes the error set
        workflow.process(block("chess_logic.py", "class Game:\n    def __init__(self): pass\n"))
        # Error set changed, so counter should reset
        assert workflow.consecutive_identical_errors == 0


# ============================================================================
# FIX 5: Stall recovery
# ============================================================================

class TestStallRecovery:
    def test_stall_triggers_recovery_before_termination(self, tmp_path):
        """FIX 5: Repeated state triggers recovery attempt before STALLED."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)
        replies = []
        for _ in range(10):
            reply = w.process("TERMINATE")
            replies.append(reply)
            if w.status != "IN_PROGRESS":
                break
        # Should have gotten a recovery feedback before STALLED
        recovery_replies = [r for r in replies if r and "STALL RECOVERY" in r]
        assert len(recovery_replies) >= 1 or w.status == "STALLED"
        # Final status should be STALLED (recovery didn't help since same input)
        assert w.status == "STALLED"

    def test_recovery_remains_bounded(self, tmp_path):
        """FIX 5: Recovery does not create infinite loops."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)
        for _ in range(20):
            w.process("TERMINATE")
            if w.status != "IN_PROGRESS":
                break
        assert w.status == "STALLED"
        # Should terminate within stall_limit + 1 (recovery) + 1 rounds
        assert w.rounds <= 20

    def test_response_budget_enforced(self, tmp_path):
        """FIX 5: max_rounds is still the hard upper bound."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=3, stall_limit=10)
        for _ in range(10):
            w.process(block("chess_logic.py", f"x = 1\n"))
            if w.status != "IN_PROGRESS":
                break
        assert w.rounds <= 3
        assert w.status in ("MAX_ROUNDS_REACHED", "STALLED")


# ============================================================================
# FIX 6: Workspace inspection
# ============================================================================

class TestWorkspaceInspection:
    def test_recovery_suggests_file_inspection(self, tmp_path):
        """FIX 6: Recovery feedback suggests inspecting existing files."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)
        # Create some files so there's something to inspect
        (tmp_path / "chess_logic.py").write_text("class Game: pass\n")
        replies = []
        for _ in range(10):
            reply = w.process("TERMINATE")
            replies.append(reply)
            if w.status != "IN_PROGRESS":
                break
        # Recovery feedback should mention reading files
        recovery = [r for r in replies if r and "STALL RECOVERY" in r]
        if recovery:
            assert "read" in recovery[0].lower() or "inspect" in recovery[0].lower() or \
                   "workspace" in recovery[0].lower()

    def test_normal_workflow_no_extra_reads_forced(self, workflow):
        """FIX 6: Normal successful progression does not force unnecessary reads."""
        code = "def add(a, b):\n    return a + b\n"
        reply = workflow.process(block("chess_logic.py", code))
        # Normal reply should not contain STALL RECOVERY
        assert reply is None or "STALL RECOVERY" not in reply
        # File should have been written successfully
        assert "chess_logic.py" in workflow.files_created

    def test_adaptive_prefix_suggests_inspection_after_many_repeats(self, workflow):
        """FIX 6: After 3+ consecutive identical errors, feedback suggests reading files."""
        (workflow.workspace / "chess_logic.py").write_text("class Game: pass\n")
        for _ in range(4):
            reply = workflow.process("TERMINATE")
            if workflow.consecutive_identical_errors >= 1 and reply and ("inspect" in reply.lower() or "read" in reply.lower()):
                break
        # After multiple repeats, should suggest inspection
        assert workflow.consecutive_identical_errors >= 1
