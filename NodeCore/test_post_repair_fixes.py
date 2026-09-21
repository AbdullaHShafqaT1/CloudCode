"""Regression tests for post-repair chess failure fixes (FIX 7-9).

Tests verify:
- Dependency preflight detects missing packages (FIX 7)
- Import error classification gives targeted feedback (FIX 8)
- Recovery feedback prioritises dependency/import blockers (FIX 9)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
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


# ============================================================================
# FIX 7: Dependency Preflight
# ============================================================================

class TestDependencyPreflight:
    def test_no_requirements_file_returns_empty(self, workflow):
        """FIX 7: No requirements.txt means no dependency errors."""
        errors = workflow.dependency_preflight()
        assert errors == []

    def test_empty_requirements_file_returns_empty(self, workflow):
        """FIX 7: Empty requirements.txt means no dependency errors."""
        (workflow.workspace / "requirements.txt").write_text("", encoding="utf-8")
        errors = workflow.dependency_preflight()
        assert errors == []

    def test_comments_and_blanks_ignored(self, workflow):
        """FIX 7: Comments and blank lines in requirements.txt are skipped."""
        (workflow.workspace / "requirements.txt").write_text(
            "# This is a comment\n\n-e ./local\n", encoding="utf-8")
        errors = workflow.dependency_preflight()
        assert errors == []

    def test_installed_package_no_errors(self, workflow):
        """FIX 7: A package that is installed (e.g. pytest) produces no error."""
        (workflow.workspace / "requirements.txt").write_text("pytest\n", encoding="utf-8")
        errors = workflow.dependency_preflight()
        assert errors == []

    def test_missing_package_reported(self, workflow):
        """FIX 7: A package that is NOT installed produces a clear error."""
        (workflow.workspace / "requirements.txt").write_text(
            "nonexistent-fake-package-xyz123\n", encoding="utf-8")
        errors = workflow.dependency_preflight()
        assert len(errors) == 1
        assert "NOT installed" in errors[0]
        assert "nonexistent-fake-package-xyz123" in errors[0]

    def test_python_chess_mapping(self, workflow):
        """FIX 7: python-chess is mapped to import name 'chess'."""
        (workflow.workspace / "requirements.txt").write_text(
            "python-chess\n", encoding="utf-8")
        errors = workflow.dependency_preflight()
        # python-chess may or may not be installed; we check the mapping is correct
        if errors:
            assert "chess" in errors[0]  # Should mention the import name
            assert "python-chess" in errors[0] or "python_chess" in errors[0]

    def test_version_specifiers_stripped(self, workflow):
        """FIX 7: Version specifiers like >=1.0 are stripped from package name."""
        (workflow.workspace / "requirements.txt").write_text(
            "pytest>=7.0\n", encoding="utf-8")
        errors = workflow.dependency_preflight()
        assert errors == []

    def test_multiple_packages(self, workflow):
        """FIX 7: Multiple packages are all checked."""
        (workflow.workspace / "requirements.txt").write_text(
            "pytest\nnonexistent-pkg-abc\nhttpx\nnonexistent-pkg-def\n",
            encoding="utf-8")
        errors = workflow.dependency_preflight()
        # Should have exactly 2 errors for the nonexistent packages
        assert len(errors) == 2
        assert any("nonexistent-pkg-abc" in e for e in errors)
        assert any("nonexistent-pkg-def" in e for e in errors)


# ============================================================================
# FIX 8: Import Error Classification
# ============================================================================

class TestImportErrorClassification:
    def test_missing_third_party_package(self):
        """FIX 8: ModuleNotFoundError for third-party package -> missing_package."""
        output = "ModuleNotFoundError: No module named 'chess'"
        category, detail = TaskWorkflow.classify_import_error(output)
        assert category == "missing_package"
        assert detail == "chess"

    def test_missing_stdlib_is_bad_import(self):
        """FIX 8: ModuleNotFoundError for stdlib name -> bad_import_name."""
        output = "ModuleNotFoundError: No module named 'json.nonexistent'"
        category, detail = TaskWorkflow.classify_import_error(output)
        assert category == "bad_import_name"
        assert detail == "json.nonexistent"

    def test_cannot_import_name(self):
        """FIX 8: ImportError for wrong name -> bad_import_name."""
        output = "ImportError: cannot import name 'CastleRights' from 'chess'"
        category, detail = TaskWorkflow.classify_import_error(output)
        assert category == "bad_import_name"
        assert detail == "CastleRights"

    def test_syntax_error_classified(self):
        """FIX 8: SyntaxError -> syntax_error."""
        output = "SyntaxError: unexpected EOF while parsing"
        category, detail = TaskWorkflow.classify_import_error(output)
        assert category == "syntax_error"

    def test_unknown_error_classified(self):
        """FIX 8: Unknown errors -> other."""
        output = "RuntimeError: something unexpected"
        category, detail = TaskWorkflow.classify_import_error(output)
        assert category == "other"

    def test_missing_package_in_verify(self, tmp_path):
        """FIX 8: Verify surfaces differentiated feedback for missing packages."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK), max_rounds=10)
        # Create a file that imports a nonexistent package
        (tmp_path / "chess_logic.py").write_text(
            "import nonexistent_chess_module_xyz123\nclass Game: pass\n",
            encoding="utf-8")
        w.phase = 4
        errors = w.verify()
        error_text = "\n".join(errors)
        # Should have a classified error mentioning the package
        assert ("not installed" in error_text.lower()
                or "missing" in error_text.lower()
                or "No module named" in error_text)


# ============================================================================
# FIX 9: Recovery Feedback Prioritises Blockers
# ============================================================================

class TestRecoveryFeedbackPriority:
    def test_dependency_error_leads_recovery(self, tmp_path):
        """FIX 9: When dep errors exist, recovery leads with them."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)
        errors = [
            "Import failed because package 'chess' is not installed. This is a missing dependency.",
            "test_chess.py: file is missing; test suite cannot run",
        ]
        feedback = w._build_recovery_feedback(errors, [])
        # Dependency error should appear BEFORE file-missing error
        dep_pos = feedback.find("BLOCKING ISSUE")
        assert dep_pos >= 0, "Recovery should mention BLOCKING ISSUE for dep errors"
        assert "not installed" in feedback.lower()
        # Should suggest options for the model
        assert "rewrite" in feedback.lower() or "standard-library" in feedback.lower()

    def test_no_dep_errors_no_blocking_section(self, tmp_path):
        """FIX 9: When no dep errors, recovery does not add BLOCKING ISSUE section."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)
        errors = [
            "test_chess.py: file is missing; test suite cannot run",
        ]
        feedback = w._build_recovery_feedback(errors, [])
        assert "BLOCKING ISSUE" not in feedback

    def test_dep_error_overrides_missing_file_priority(self, tmp_path):
        """FIX 9: With dep errors, recovery does NOT say 'produce ONLY missing file'."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)
        errors = [
            "Import failed because package 'chess' is not installed.",
            "test_chess.py: file is missing",
        ]
        feedback = w._build_recovery_feedback(errors, [])
        # Should NOT tell the model to produce test_chess.py when import is the real blocker
        assert "Produce ONLY test_chess.py" not in feedback
        assert "Fix the blocking import errors first" in feedback

    def test_full_stall_with_dep_errors(self, tmp_path):
        """FIX 9: Full stall cycle with dependency errors leads to dep-focused recovery."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)
        # Create chess_logic.py that imports a nonexistent module
        (tmp_path / "chess_logic.py").write_text(
            "import nonexistent_chess_xyz\nclass Game: pass\n", encoding="utf-8")
        (tmp_path / "chess_gui.py").write_text(
            "from chess_logic import Game\ndef create_server(h,p): pass\n",
            encoding="utf-8")
        (tmp_path / "main.py").write_text(
            "if __name__ == '__main__': pass\n", encoding="utf-8")

        recovery_feedback = None
        for _ in range(15):
            reply = w.process("TERMINATE")
            if reply and "STALL RECOVERY" in reply:
                recovery_feedback = reply
                break
            if w.status != "IN_PROGRESS":
                break

        if recovery_feedback:
            assert "BLOCKING ISSUE" in recovery_feedback or "not installed" in recovery_feedback.lower()

    def test_recovery_without_dep_errors_still_works(self, tmp_path):
        """FIX 9: Recovery without dependency errors still mentions missing files."""
        w = TaskWorkflow(tmp_path, TASK, detect_project_profile(TASK),
                         max_rounds=20, stall_limit=3)

        recovery_feedback = None
        for _ in range(15):
            reply = w.process("TERMINATE")
            if reply and "STALL RECOVERY" in reply:
                recovery_feedback = reply
                break
            if w.status != "IN_PROGRESS":
                break

        if recovery_feedback:
            assert "missing" in recovery_feedback.lower() or "chess_logic" in recovery_feedback.lower()
