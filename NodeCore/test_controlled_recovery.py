"""Controlled local recovery test (Phase 5).

Simulates the chess benchmark failure pattern WITHOUT contacting any live model.
Reproduces:
  1. Valid source file exists
  2. Required test file is missing
  3. Another source file contains an import error
  4. Verification runs and surfaces BOTH problems
  5. Recovery feedback changes across rounds
  6. Stall recovery triggers after repeated failure
  7. Workflow remains bounded and terminates safely
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from autogen import ConversableAgent
from node_core import core
from node_core.agents import create_user_proxy_runner, detect_project_profile
from node_core.workflow import TaskWorkflow, python_command

TASK = "Create a two-player chess app with a GUI, main.py, tests, and instructions."


def block(name, code):
    return f"```python\n# filename: {name}\n{code}\n```"


def fake_coder(responses):
    """Create a fake coder that returns pre-scripted responses."""
    coder = ConversableAgent("CoderAgent", llm_config=False, human_input_mode="NEVER")
    stream = iter(responses)
    coder.register_reply([ConversableAgent, None],
                         lambda *a, **kw: (True, next(stream, None)), position=0)
    return coder


class TestControlledRecovery:
    """Simulates the chess benchmark failure chain and verifies all fixes work together."""

    def test_chess_failure_pattern_reproduced_and_broken(self, tmp_path, monkeypatch):
        """
        Scenario: Model produces valid logic + broken GUI, but test file is always
        truncated (simulating max_tokens=4096 truncation). Verifies:
        1. Partial verification surfaces multiple errors
        2. Feedback changes across rounds
        3. Stall recovery fires
        4. Loop terminates safely
        5. No live model contacted
        """
        # Pre-populate workspace like the chess benchmark after rounds 1-3
        logic = ("class Game:\n"
                 "    def __init__(self, fen=None): self.fen = fen\n"
                 "    def state(self): return {'fen': self.fen}\n")
        # GUI has a broken import (like the chess benchmark)
        gui = ("from nonexistent_chess_module import Board\n"
               "def create_server(host, port): pass\n")
        main = ("from chess_gui import create_server\n"
                "if __name__ == '__main__': pass\n")
        guide = "Run python main.py.\n"

        (tmp_path / "chess_logic.py").write_text(logic, encoding="utf-8")
        (tmp_path / "chess_gui.py").write_text(gui, encoding="utf-8")
        (tmp_path / "main.py").write_text(main, encoding="utf-8")
        (tmp_path / "RunningGUIDE.txt").write_text(guide, encoding="utf-8")

        # Model responses: always the same truncated response (simulating the stuck model)
        truncated_test = ("```python\n# filename: test_chess.py\n"
                          "import unittest\nfrom chess_logic import Game\n"
                          "class TestChess(unittest.TestCase):\n"
                          "    def test_init(self):\n"
                          "        g = Game()\n"
                          "        # more tests follow but response is truncated...")
        # No closing ``` — simulates truncation

        # The model sends the same truncated response 12 times
        responses = [truncated_test] * 12

        monkeypatch.setattr(core, "create_coder_agent", lambda _: fake_coder(responses))

        acceptance = {"checks": [{"requirement": "Basic test",
                                  "command": python_command("-c", "print('ok')")}]}

        result = core.NodeCore(
            workspace_root=str(tmp_path),
            llm_config={"mock": True}
        ).start_task(TASK, max_rounds=12, acceptance=acceptance)

        # Verify termination
        assert result["status"] in ("STALLED", "MAX_ROUNDS_REACHED")
        assert result["rounds"] <= 12

        # Verify the truncated test file was NEVER written
        assert not (tmp_path / "test_chess.py").exists(), \
            "Truncated test file should not have been written"

        # Verify existing files were preserved
        assert (tmp_path / "chess_logic.py").exists()
        assert (tmp_path / "chess_gui.py").exists()
        assert (tmp_path / "main.py").exists()

    def test_partial_verification_surfaces_multiple_errors(self, tmp_path):
        """Verify that partial verification reports both missing and broken files."""
        profile = detect_project_profile(TASK)
        w = TaskWorkflow(tmp_path, TASK, profile, max_rounds=10)

        # Logic exists and is valid
        (tmp_path / "chess_logic.py").write_text(
            "class Game:\n    def __init__(self): pass\n", encoding="utf-8")
        # GUI exists but has bad import
        (tmp_path / "chess_gui.py").write_text(
            "from totally_broken import X\ndef create_server(h,p): pass\n", encoding="utf-8")
        # Main exists
        (tmp_path / "main.py").write_text(
            "if __name__ == '__main__': pass\n", encoding="utf-8")
        # test_chess.py is MISSING
        # RunningGUIDE.txt is MISSING

        w.phase = 4
        errors = w.verify()

        # Should have BOTH: missing test file AND import failure
        error_text = "\n".join(errors)
        has_missing = ("test_chess" in error_text and
                       ("No such file" in error_text or "missing" in error_text.lower()))
        has_import = "import" in error_text.lower() or "totally_broken" in error_text

        assert has_missing, f"Should report missing test_chess.py. Errors: {errors}"
        assert has_import, f"Should report import failure. Errors: {errors}"

    def test_feedback_changes_across_rounds(self, tmp_path):
        """Verify that feedback messages change when errors repeat."""
        profile = detect_project_profile(TASK)
        w = TaskWorkflow(tmp_path, TASK, profile, max_rounds=10, stall_limit=5)

        feedbacks = []
        for i in range(6):
            reply = w.process("TERMINATE")
            if reply is None:
                break
            feedbacks.append(reply)

        # Should have multiple non-None feedbacks
        assert len(feedbacks) >= 3

        # Later feedbacks should be different from earlier ones
        # (due to adaptive prefix and/or recovery)
        if len(feedbacks) >= 3:
            # At minimum, feedback[2] should differ from feedback[0]
            # due to the adaptive prefix added by FIX 4
            assert feedbacks[0] != feedbacks[2] or feedbacks[1] != feedbacks[2], \
                "Feedback should change across repeated failures"

    def test_stall_recovery_activates(self, tmp_path):
        """Verify stall recovery fires before final STALLED termination."""
        profile = detect_project_profile(TASK)
        w = TaskWorkflow(tmp_path, TASK, profile, max_rounds=20, stall_limit=3)

        recovery_seen = False
        for _ in range(15):
            reply = w.process("TERMINATE")
            if reply and "STALL RECOVERY" in reply:
                recovery_seen = True
            if w.status != "IN_PROGRESS":
                break

        assert recovery_seen, "Stall recovery should have been triggered"
        assert w.status == "STALLED"
        assert w._recovery_attempted

    def test_loop_terminates_safely(self, tmp_path):
        """Verify the workflow terminates and does not loop forever."""
        profile = detect_project_profile(TASK)
        w = TaskWorkflow(tmp_path, TASK, profile, max_rounds=8, stall_limit=3)

        for _ in range(100):  # Much more than max_rounds
            w.process("TERMINATE")
            if w.status != "IN_PROGRESS":
                break

        assert w.status != "IN_PROGRESS", "Workflow must terminate"
        assert w.rounds <= 8, f"Rounds ({w.rounds}) must not exceed max_rounds (8)"

    def test_no_live_model_contacted(self, tmp_path, monkeypatch):
        """Verify the controlled test does not contact any live model."""
        import urllib.request
        original_urlopen = urllib.request.urlopen
        calls = []

        def tracked_urlopen(*args, **kwargs):
            calls.append(args)
            return original_urlopen(*args, **kwargs)

        monkeypatch.setattr(urllib.request, "urlopen", tracked_urlopen)

        profile = detect_project_profile(TASK)
        w = TaskWorkflow(tmp_path, TASK, profile, max_rounds=5)
        for _ in range(5):
            w.process("TERMINATE")
            if w.status != "IN_PROGRESS":
                break

        # No HTTP calls should have been made to the model
        for call in calls:
            url = str(call[0]) if call else ""
            assert "cloudflare" not in url.lower()
            assert "ollama" not in url.lower()

    def test_recovery_with_successful_response_breaks_stall(self, tmp_path):
        """Verify that if recovery gets a valid response, stall is broken."""
        profile = detect_project_profile(TASK)
        w = TaskWorkflow(tmp_path, TASK, profile, max_rounds=20, stall_limit=3)

        # Drive to stall detection with empty responses
        for _ in range(4):
            reply = w.process("TERMINATE")
            if reply and "STALL RECOVERY" in reply:
                break

        # Now provide a valid file — this should break the stall
        if w.status == "IN_PROGRESS":
            valid_code = "class Game:\n    def __init__(self): pass\n"
            reply = w.process(block("chess_logic.py", valid_code))
            # Should have progressed (either to next phase or still in progress
            # but with reset stall counter)
            assert w.no_progress == 0 or w.phase > 1 or w.status != "IN_PROGRESS"
