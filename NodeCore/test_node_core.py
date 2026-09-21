"""
Standalone Unit Test Suite for NodeCore (Orchestrator Logic).

Validates decision parsing, state machine transitions, tool dispatch formatting,
retry and backoff cutoff, and telemetry emission using mocks—completely independent
of live LLMs, active network connections, or external subprocesses.

Supported Execution:
    pytest test_node_core.py -v
    python test_node_core.py
"""

import os
import sys
import unittest.mock as mock
from datetime import datetime
from typing import Any, Dict, List

import pytest

# Ensure local module is importable when executed directly
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from node_core.core import (
    ErrorParser,
    NodeCore,
    RemediationGenerator,
)
from node_core.schemas import (
    ErrorDiagnosis,
    ErrorType,
    ExecutionResult,
    NodeCoreState,
    PatchInstruction,
    RemediationFailedException,
    RemediationPlan,
    RemediationStatus,
    TelemetryEvent,
    ToolDispatchCall,
)
from node_core.tools import NodeLog, sanitize_for_console, UNICODE_GLYPH_MAP
from node_core.agents import (
    extract_code_blocks_with_metadata,
    extract_api_contracts,
    prepare_phased_task_prompt,
    detect_project_profile,
    create_coder_agent,
    create_user_proxy_runner
)


# =====================================================================
# 1. Test Fixtures
# =====================================================================

@pytest.fixture
def mock_syntax_error_trace() -> str:
    """Simulated raw Python SyntaxError traceback."""
    return """Traceback (most recent call last):
  File "src/calc.py", line 14
    def compute_sum(a, b)
                         ^
SyntaxError: expected ':'
"""


@pytest.fixture
def mock_module_not_found_trace() -> str:
    """Simulated raw Python ModuleNotFoundError traceback."""
    return """Traceback (most recent call last):
  File "src/api.py", line 3, in <module>
    import requests
ModuleNotFoundError: No module named 'requests'
"""


@pytest.fixture
def mock_dom_error_trace() -> str:
    """Simulated raw DOM / HTML element lookup failure."""
    return """DOMException: Element with selector '#submit-btn' was not found in 'index.html'.
"""


@pytest.fixture
def mock_broken_pulse_output(mock_syntax_error_trace: str) -> Dict[str, Any]:
    """Broken script execution output from NodePulse (exit_code=1, stderr with error trace)."""
    return {
        "status": "error",
        "action": "run_command",
        "command": "python -m pytest tests/test_calc.py",
        "cwd": "/workspace/project",
        "exit_code": 1,
        "stdout": "Running tests...\nF",
        "stderr": mock_syntax_error_trace,
        "duration_ms": 145,
        "timestamp": "2026-09-07T00:00:00Z",
        "error_message": "Process failed with exit code 1",
        "truncated": False,
    }


@pytest.fixture
def mock_success_pulse_output() -> Dict[str, Any]:
    """Successful run output from NodePulse (exit_code=0)."""
    return {
        "status": "success",
        "action": "run_command",
        "command": "python -m pytest tests/test_calc.py",
        "cwd": "/workspace/project",
        "exit_code": 0,
        "stdout": "tests/test_calc.py . [100%]\n1 passed in 0.08s",
        "stderr": "",
        "duration_ms": 98,
        "timestamp": "2026-09-07T00:00:01Z",
        "error_message": None,
        "truncated": False,
    }


@pytest.fixture
def mock_llm_patch_proposal() -> Dict[str, Any]:
    """Mock LLM inference return proposing a specific structured patch."""
    return {
        "target_file": "src/calc.py",
        "target_tools": ["NodeForge"],
        "instructions": [
            {
                "instruction_id": "patch_inst_001",
                "target_file": "src/calc.py",
                "action": "apply_patch",
                "line_start": 14,
                "line_end": 14,
                "original_content": "def compute_sum(a, b)",
                "patch_content": "def compute_sum(a, b):",
                "description": "Add missing colon to function definition header",
            }
        ],
    }


@pytest.fixture
def telemetry_sink():
    """Captures all emitted telemetry events for schema and payload verification."""
    events: List[Dict[str, Any]] = []

    def sink(event_dict: Dict[str, Any]) -> None:
        events.append(event_dict)

    return events, sink


@pytest.fixture
def node_core_instance(telemetry_sink) -> NodeCore:
    """Fresh NodeCore orchestrator instance with configured telemetry sink."""
    events, sink = telemetry_sink
    return NodeCore(
        max_retries=3,
        initial_backoff=0.5,
        backoff_factor=2.0,
        workspace_root="/workspace/project",
        telemetry_sink=sink,
    )


# =====================================================================
# 2. Required Test Cases (Task Specification)
# =====================================================================

def test_initial_state_idle(node_core_instance: NodeCore):
    """
    Test 1: test_initial_state_idle()
    Verifies initialization and default configuration settings.
    """
    assert node_core_instance.state == NodeCoreState.IDLE
    assert node_core_instance.status == RemediationStatus.IDLE.value
    assert node_core_instance.retry_count == 0
    assert node_core_instance.max_retries == 3
    assert node_core_instance.initial_backoff == 0.5
    assert node_core_instance.backoff_factor == 2.0
    assert node_core_instance.workspace_root == "/workspace/project"
    assert node_core_instance.current_diagnosis is None
    assert node_core_instance.current_plan is None
    assert len(node_core_instance.history) == 0


def test_error_handling_flow(
    node_core_instance: NodeCore,
    mock_broken_pulse_output: Dict[str, Any]
):
    """
    Test 2: test_error_handling_flow()
    Supplies a simulated execution failure and confirms NodeCore transitions to
    ANALYZING and prepares an inspection call to NodeInsight.
    """
    with mock.patch.object(NodeLog, "emit") as mock_emit:
        dispatch_call = node_core_instance.handle_execution_failure(mock_broken_pulse_output)

        # 1. Verify state transition to ANALYZING
        assert node_core_instance.state == NodeCoreState.ANALYZING
        assert node_core_instance.status == RemediationStatus.IN_PROGRESS.value

        # 2. Verify diagnosis was parsed
        diagnosis = node_core_instance.current_diagnosis
        assert diagnosis is not None
        assert isinstance(diagnosis, ErrorDiagnosis)
        assert diagnosis.error_type == ErrorType.SYNTAX_ERROR.value
        assert diagnosis.file_path == "src/calc.py"
        assert diagnosis.line_number == 14
        assert "expected ':'" in diagnosis.message

        # 3. Verify tool dispatch prepares inspection call to NodeInsight
        assert isinstance(dispatch_call, ToolDispatchCall)
        assert dispatch_call.target_node == "NodeInsight"
        assert dispatch_call.action == "read"
        assert dispatch_call.parameters["file_path"] == "src/calc.py"
        assert dispatch_call.parameters["line_number"] == 14
        assert "error_type" in dispatch_call.parameters

        # 4. Verify telemetry signals emitted
        assert mock_emit.called
        event_types = [call[0][0] for call in mock_emit.call_args_list]
        assert "STATE_TRANSITION" in event_types
        assert "ERROR_DIAGNOSED" in event_types


def test_remediation_plan_generation(
    node_core_instance: NodeCore,
    mock_broken_pulse_output: Dict[str, Any],
    mock_llm_patch_proposal: Dict[str, Any]
):
    """
    Test 3: test_remediation_plan_generation()
    Verifies that parsed errors yield explicit patch instructions rather than unstructured text.
    """
    # Ingest error first
    node_core_instance.handle_execution_failure(mock_broken_pulse_output)
    assert node_core_instance.state == NodeCoreState.ANALYZING

    # Generate remediation plan with mock LLM suggestion
    with mock.patch.object(NodeLog, "emit") as mock_emit:
        plan = node_core_instance.generate_remediation_plan(
            llm_suggestion=mock_llm_patch_proposal
        )

        # 1. Verify transition to PATCHING
        assert node_core_instance.state == NodeCoreState.PATCHING
        assert node_core_instance.current_plan is plan

        # 2. Verify plan is a strongly-typed schema, not unstructured text
        assert isinstance(plan, RemediationPlan)
        assert plan.plan_id.startswith("plan_")
        assert len(plan.instructions) == 1
        assert "NodeForge" in plan.target_tools

        # 3. Verify explicit patch instructions
        instruction = plan.instructions[0]
        assert isinstance(instruction, PatchInstruction)
        assert instruction.instruction_id == "patch_inst_001"
        assert instruction.target_file == "src/calc.py"
        assert instruction.action == "apply_patch"
        assert instruction.line_start == 14
        assert instruction.line_end == 14
        assert instruction.original_content == "def compute_sum(a, b)"
        assert instruction.patch_content == "def compute_sum(a, b):"
        assert instruction.description == "Add missing colon to function definition header"

        # 4. Verify patch dispatch formatting targeting NodeForge
        patch_call = node_core_instance.prepare_patch_dispatch()
        assert isinstance(patch_call, ToolDispatchCall)
        assert patch_call.target_node == "NodeForge"
        assert patch_call.action == "apply_patch"
        assert patch_call.parameters["target_file"] == "src/calc.py"
        assert patch_call.parameters["patch_content"] == "def compute_sum(a, b):"

        # 5. Verify telemetry event emitted
        assert mock_emit.called
        event_types = [call[0][0] for call in mock_emit.call_args_list]
        assert "PLAN_GENERATED" in event_types


def test_retry_limit_cutoff(
    node_core_instance: NodeCore,
    mock_broken_pulse_output: Dict[str, Any]
):
    """
    Test 4: test_retry_limit_cutoff()
    Simulates consecutive execution failures and asserts that NodeCore stops
    at the configured threshold (e.g., 3 attempts) with a RemediationFailed status.
    """
    max_retries = node_core_instance.max_retries  # 3

    with mock.patch.object(NodeLog, "emit") as mock_emit:
        # Initial failure -> ANALYZING
        node_core_instance.handle_execution_failure(mock_broken_pulse_output)
        assert node_core_instance.state == NodeCoreState.ANALYZING

        # Run through repeated failing remediation cycles
        for attempt in range(1, max_retries + 1):
            # Formulate patch -> PATCHING
            node_core_instance.generate_remediation_plan()
            assert node_core_instance.state == NodeCoreState.PATCHING

            # Prepare validation -> VALIDATING
            val_call = node_core_instance.prepare_validation(command="pytest")
            assert node_core_instance.state == NodeCoreState.VALIDATING
            assert val_call.target_node == "NodePulse"
            assert val_call.action == "run_command"

            # Simulate validation failing again
            next_state = node_core_instance.evaluate_validation_result(mock_broken_pulse_output)

            if attempt < max_retries:
                # Should transition back to ANALYZING for next attempt
                assert next_state == NodeCoreState.ANALYZING
                assert node_core_instance.state == NodeCoreState.ANALYZING
                assert node_core_instance.retry_count == attempt
                assert node_core_instance.status == RemediationStatus.IN_PROGRESS.value
            else:
                # Should halt runaway loop at max_retries
                assert next_state == NodeCoreState.RETRY_LIMIT
                assert node_core_instance.state == NodeCoreState.RETRY_LIMIT
                assert node_core_instance.retry_count == max_retries
                assert node_core_instance.status == RemediationStatus.RemediationFailed.value

        # Confirm runaway loop halted and did not exceed max_retries
        assert node_core_instance.retry_count == 3
        assert node_core_instance.state == NodeCoreState.RETRY_LIMIT
        assert node_core_instance.status == RemediationStatus.RemediationFailed.value

        # Verify critical failure event emitted
        event_types = [call[0][0] for call in mock_emit.call_args_list]
        assert "REMEDIATION_FAILED" in event_types


def test_success_termination(
    node_core_instance: NodeCore,
    mock_broken_pulse_output: Dict[str, Any],
    mock_success_pulse_output: Dict[str, Any]
):
    """
    Test 5: test_success_termination()
    Simulates a failed run followed by a successful run post-patch,
    verifying proper state reset to SUCCESS.
    """
    with mock.patch.object(NodeLog, "emit") as mock_emit:
        # Step 1: Initial failure
        node_core_instance.handle_execution_failure(mock_broken_pulse_output)
        assert node_core_instance.state == NodeCoreState.ANALYZING

        # Step 2: Formulate patch
        node_core_instance.generate_remediation_plan()
        assert node_core_instance.state == NodeCoreState.PATCHING

        # Step 3: Trigger validation
        node_core_instance.prepare_validation(command="pytest")
        assert node_core_instance.state == NodeCoreState.VALIDATING

        # Step 4: Simulate successful validation post-patch
        final_state = node_core_instance.evaluate_validation_result(mock_success_pulse_output)

        # Step 5: Assert state and status reset to SUCCESS
        assert final_state == NodeCoreState.SUCCESS
        assert node_core_instance.state == NodeCoreState.SUCCESS
        assert node_core_instance.status == RemediationStatus.SUCCESS.value
        assert node_core_instance.retry_count == 0

        # Step 6: Verify REMEDIATION_SUCCESS telemetry emitted
        event_types = [call[0][0] for call in mock_emit.call_args_list]
        assert "REMEDIATION_SUCCESS" in event_types


# =====================================================================
# 3. Exhaustive Error Diagnosis Parsing Tests
# =====================================================================

def test_traceback_parser_syntax_error(mock_syntax_error_trace: str):
    """Tests parsing of Python SyntaxError with file, line, and code context."""
    diagnosis = ErrorParser.parse(mock_syntax_error_trace)
    assert diagnosis.error_type == ErrorType.SYNTAX_ERROR.value
    assert diagnosis.file_path == "src/calc.py"
    assert diagnosis.line_number == 14
    assert diagnosis.offending_symbol == "def compute_sum(a, b)"
    assert "expected ':'" in diagnosis.message


def test_traceback_parser_module_not_found(mock_module_not_found_trace: str):
    """Tests parsing of Python ModuleNotFoundError with missing module name."""
    diagnosis = ErrorParser.parse(mock_module_not_found_trace)
    assert diagnosis.error_type == ErrorType.MODULE_NOT_FOUND.value
    assert diagnosis.offending_symbol == "requests"
    assert "No module named 'requests'" in diagnosis.message
    assert diagnosis.file_path == "src/api.py"
    assert diagnosis.line_number == 3


def test_traceback_parser_dom_error(mock_dom_error_trace: str):
    """Tests parsing of DOMException and HTML selector errors."""
    diagnosis = ErrorParser.parse(mock_dom_error_trace)
    assert diagnosis.error_type == ErrorType.DOM_ERROR.value
    assert diagnosis.offending_symbol == "#submit-btn"
    assert diagnosis.file_path == "index.html"
    assert "#submit-btn" in diagnosis.message


def test_traceback_parser_generic_runtime_error():
    """Tests parsing of standard Python runtime exceptions."""
    raw_trace = """Traceback (most recent call last):
  File "src/server.py", line 88, in handle_request
    result = 10 / divisor
ZeroDivisionError: division by zero
"""
    diagnosis = ErrorParser.parse(raw_trace)
    assert diagnosis.error_type == "ZeroDivisionError"
    assert diagnosis.file_path == "src/server.py"
    assert diagnosis.line_number == 88
    assert "division by zero" in diagnosis.message


def test_traceback_parser_empty_or_invalid():
    """Tests parser resilience against None and empty strings."""
    d1 = ErrorParser.parse("")
    assert d1.error_type == ErrorType.UNKNOWN.value

    d2 = ErrorParser.parse(None)  # type: ignore
    assert d2.error_type == ErrorType.UNKNOWN.value


# =====================================================================
# 4. Tool Dispatch Selection Tests
# =====================================================================

def test_tool_dispatch_node_insight(node_core_instance: NodeCore):
    """Tests tool dispatch formatting when routing inspection actions to NodeInsight."""
    call = node_core_instance.dispatch_tool_action(
        intent="read",
        context={"action": "read", "file_path": "src/utils.py", "line_number": 42}
    )
    assert call.target_node == "NodeInsight"
    assert call.action == "read"
    assert call.parameters["file_path"] == "src/utils.py"
    assert call.parameters["line_number"] == 42


def test_tool_dispatch_node_forge(node_core_instance: NodeCore):
    """Tests tool dispatch formatting when routing modification actions to NodeForge."""
    call = node_core_instance.dispatch_tool_action(
        intent="apply_patch",
        context={
            "action": "apply_patch",
            "target_file": "src/utils.py",
            "patch_content": "fixed_code",
            "line_start": 10,
        }
    )
    assert call.target_node == "NodeForge"
    assert call.action == "apply_patch"
    assert call.parameters["target_file"] == "src/utils.py"
    assert call.parameters["patch_content"] == "fixed_code"


def test_tool_dispatch_node_pulse(node_core_instance: NodeCore):
    """Tests tool dispatch formatting when routing command runs to NodePulse."""
    call = node_core_instance.dispatch_tool_action(
        intent="run_command",
        context={"action": "run_command", "command": "python -m pytest"}
    )
    assert call.target_node == "NodePulse"
    assert call.action == "run_command"
    assert call.parameters["command"] == "python -m pytest"


def test_tool_dispatch_node_link(node_core_instance: NodeCore):
    """Tests tool dispatch formatting when routing remote cloud tasks to NodeLink."""
    call = node_core_instance.dispatch_tool_action(
        intent="dispatch_remote",
        context={"action": "dispatch_remote", "target": "cluster_gpu_01"}
    )
    assert call.target_node == "NodeLink"
    assert call.action == "dispatch_remote"
    assert call.parameters["target"] == "cluster_gpu_01"


# =====================================================================
# 5. Retry & Backoff Calculation Tests
# =====================================================================

def test_exponential_backoff_calculation(node_core_instance: NodeCore):
    """
    Verifies exponential backoff delay calculation based on attempt number:
    initial_backoff * (backoff_factor ** (attempt - 1))
    """
    # 0 or negative attempt -> 0.0
    assert node_core_instance.calculate_backoff(0) == 0.0

    # Attempt 1: 0.5 * (2.0 ** 0) = 0.5s
    assert node_core_instance.calculate_backoff(1) == 0.5

    # Attempt 2: 0.5 * (2.0 ** 1) = 1.0s
    assert node_core_instance.calculate_backoff(2) == 1.0

    # Attempt 3: 0.5 * (2.0 ** 2) = 2.0s
    assert node_core_instance.calculate_backoff(3) == 2.0

    # Attempt 4: 0.5 * (2.0 ** 3) = 4.0s
    assert node_core_instance.calculate_backoff(4) == 4.0


def test_custom_retry_limit():
    """Verifies that NodeCore respects custom max_retries configuration."""
    custom_core = NodeCore(max_retries=1)
    custom_core.handle_execution_failure("Error: syntax")
    custom_core.generate_remediation_plan()
    custom_core.prepare_validation()

    # Immediate single failure should trip the limit
    final_state = custom_core.evaluate_validation_result({"exit_code": 1})
    assert final_state == NodeCoreState.RETRY_LIMIT
    assert custom_core.status == RemediationStatus.RemediationFailed.value
    assert custom_core.retry_count == 1


# =====================================================================
# 6. Telemetry & Log Formatting Tests (Schema Strictness)
# =====================================================================

def test_telemetry_schema_strictness(telemetry_sink):
    """
    Verifies that all emitted telemetry events adhere strictly to the
    NodeLog / TelemetryEvent schema (source, event_type, status_level,
    message, phase, payload, timestamp).
    """
    events, sink = telemetry_sink
    core = NodeCore(max_retries=2, telemetry_sink=sink)

    # Perform actions that trigger telemetry
    core.handle_execution_failure("SyntaxError: invalid syntax in test.py line 5")
    core.generate_remediation_plan()
    core.prepare_validation()
    core.evaluate_validation_result({"exit_code": 0})

    assert len(events) > 0

    valid_levels = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "SUCCESS", "CRITICAL"}
    for event_dict in events:
        # Validate through strict Pydantic model
        validated_event = TelemetryEvent(**event_dict)
        assert validated_event.source == "NodeCore"
        assert len(validated_event.event_type) > 0
        assert validated_event.status_level in valid_levels
        assert len(validated_event.message) > 0
        assert validated_event.timestamp is not None
        assert isinstance(validated_event.payload, dict)


def test_rule_based_remediation_fallback(node_core_instance: NodeCore):
    """
    Verifies that RemediationGenerator produces a valid, typed RemediationPlan
    with explicit PatchInstruction even when no LLM inference output is provided.
    """
    diagnosis = ErrorDiagnosis(
        error_type=ErrorType.MODULE_NOT_FOUND.value,
        message="No module named 'numpy'",
        offending_symbol="numpy",
        file_path="src/math.py",
        line_number=2,
    )

    plan = RemediationGenerator.generate(diagnosis, llm_suggestion=None)
    assert isinstance(plan, RemediationPlan)
    assert len(plan.instructions) == 1
    inst = plan.instructions[0]
    assert inst.target_file == "requirements.txt"
    assert "numpy" in (inst.patch_content or "")
    assert inst.action == "apply_patch"


def test_reset_functionality(node_core_instance: NodeCore):
    """Verifies that reset() properly clears all state and retry counts."""
    node_core_instance.handle_execution_failure("Error: fail")
    node_core_instance.retry_count = 2

    assert node_core_instance.state == NodeCoreState.ANALYZING
    assert node_core_instance.retry_count == 2

    node_core_instance.reset()

    assert node_core_instance.state == NodeCoreState.IDLE
    assert node_core_instance.status == RemediationStatus.IDLE.value
    assert node_core_instance.retry_count == 0
    assert node_core_instance.current_diagnosis is None
    assert node_core_instance.current_plan is None


# =====================================================================
# 7. Hardening & Phased Pipeline Tests
# =====================================================================

def test_extract_code_blocks_unclosed_trailing_backticks():
    """Verifies that truncated code blocks missing closing triple backticks at EOF are cleanly parsed."""
    truncated_content = """Here is the core logic:
```python
# filename: chess_logic.py
class ChessGame:
    def __init__(self):
        self.board = []
"""
    blocks = extract_code_blocks_with_metadata(truncated_content)
    assert len(blocks) == 1
    assert blocks[0]["filename"] == "chess_logic.py"
    assert blocks[0]["lang"] == "python"
    assert "class ChessGame:" in blocks[0]["code"]
    assert "self.board = []" in blocks[0]["code"]


def test_extract_code_blocks_adjacent_blocks_non_greedy():
    """Verifies that adjacent code blocks are parsed separately without greedily swallowing one another."""
    multi_content = """Phase 1:
```python
# filename: module_a.py
def func_a():
    return 'a'
```

Phase 2:
```python
# filename: module_b.py
def func_b():
    return 'b'
```
"""
    blocks = extract_code_blocks_with_metadata(multi_content)
    assert len(blocks) == 2
    assert blocks[0]["filename"] == "module_a.py"
    assert "func_a" in blocks[0]["code"]
    assert "func_b" not in blocks[0]["code"]

    assert blocks[1]["filename"] == "module_b.py"
    assert "func_b" in blocks[1]["code"]
    assert "func_a" not in blocks[1]["code"]


def test_extract_code_blocks_missing_filename_fallback():
    """Verifies that blocks without explicit headers fallback to structured workspace paths rather than being dropped."""
    code_only = """```python
def compute_delta(a, b):
    return b - a
```"""
    blocks = extract_code_blocks_with_metadata(code_only)
    assert len(blocks) == 1
    assert blocks[0]["filename"] is not None
    assert blocks[0]["filename"].endswith(".py")
    assert "compute_delta" in blocks[0]["code"]

    # Test with default_filename override
    blocks_with_default = extract_code_blocks_with_metadata(code_only, default_filename="custom_target.py")
    assert len(blocks_with_default) == 1
    assert blocks_with_default[0]["filename"] == "custom_target.py"


def test_extract_code_blocks_line_1_explicit():
    """Verifies that '# filename: <path>' on line 1 is extracted correctly."""
    sample = """```python
# filename: game_controller.py
class Controller:
    pass
```"""
    blocks = extract_code_blocks_with_metadata(sample)
    assert len(blocks) == 1
    assert blocks[0]["filename"] == "game_controller.py"
    # Leading directive comment should be stripped from clean code
    assert "# filename:" not in blocks[0]["code"]
    assert "class Controller:" in blocks[0]["code"]


def test_ast_contracts_extraction():
    """Verifies that extract_api_contracts extracts public classes, methods, functions, and constants via AST."""
    sample_code = '''"""Game Logic Engine"""
BOARD_SIZE = 8
UNICODE_PIECES = {"wK": "♔", "bK": "♚"}

class ChessGame:
    """Core chess game state and move engine."""
    def __init__(self, mode: str = "standard"):
        self.mode = mode
        self._private_state = None

    def get_piece(self, row: int, col: int) -> str:
        """Returns piece at position."""
        return "wP"

    def make_move(self, from_pos: tuple, to_pos: tuple) -> bool:
        return True

    def _internal_helper(self):
        pass

def validate_coords(r: int, c: int) -> bool:
    """Checks bounds."""
    return 0 <= r < 8 and 0 <= c < 8

def _private_func():
    pass
'''
    contract = extract_api_contracts(sample_code, is_code=True, source_name="chess_logic.py")

    assert "EXTRACTED API CONTRACT (from chess_logic.py)" in contract
    assert "class ChessGame:" in contract
    assert "- __init__(self, mode: str = 'standard')" in contract
    assert "- get_piece(self, row: int, col: int) -> str" in contract
    assert "- make_move(self, from_pos: tuple, to_pos: tuple) -> bool" in contract
    # Private methods and functions must be excluded
    assert "_internal_helper" not in contract
    assert "_private_func" not in contract
    # Public module function must be included
    assert "- validate_coords(r: int, c: int) -> bool" in contract
    # Constants
    assert "BOARD_SIZE = 8" in contract


def test_coder_agent_system_prompt_hardening():
    """Verifies that CoderAgent's system prompt enforces all strict negative constraints."""
    agent = create_coder_agent(llm_config={"config_list": [{"model": "dummy", "api_key": "dummy"}]})
    sys_msg = agent.system_message

    assert "ZERO CONVERSATIONAL ADVICE" in sys_msg
    assert "COMPLETE RECOVERY ARTIFACTS" in sys_msg
    assert "FORBIDDEN STUBS & PLACEHOLDERS" in sys_msg
    assert "# TODO" in sys_msg
    assert "# filename: <filepath>" in sys_msg
    assert "Never output conversational advice" in sys_msg


def test_windows_unicode_glyph_sanitization():
    """Verifies that sanitize_for_console converts non-ASCII chess glyphs and emojis to safe representations."""
    raw_text = "Piece wK: ♔, wQ: ♕, bK: ♚, bQ: ♛, check: ✔, bullet: ●, zap: ⚡"
    sanitized = sanitize_for_console(raw_text)

    # Chess pieces converted to ASCII equivalents
    assert "♔" not in sanitized
    assert "[wK]" in sanitized
    assert "♕" not in sanitized
    assert "[wQ]" in sanitized
    assert "♚" not in sanitized
    assert "[bK]" in sanitized
    assert "♛" not in sanitized
    assert "[bQ]" in sanitized
    # Status symbols converted
    assert "[OK]" in sanitized

    # Verify that safe encoding never crashes even with legacy encodings
    result = sanitize_for_console(raw_text, fallback_encoding="ascii")
    assert isinstance(result, str)
    assert len(result) > 0


def test_phased_task_prompt_preparation():
    """Verifies that prepare_phased_task_prompt scopes Turn 1 strictly to Phase 1."""
    full_prompt = """TASK: Build a Complete GUI-Based 2-Player Chess Game with Full Rules & Save System
Requirements:
1. chess_logic.py: Full chess engine
2. chess_gui.py: GUI interface
3. main.py: Launcher
4. test_chess.py: Automated tests
5. RunningGUIDE.txt: User guide
"""
    scoped = prepare_phased_task_prompt(full_prompt)

    assert "PHASE 1 OF 5 (Interface & Logic Engine)" in scoped
    assert "chess_logic.py" in scoped
    assert "DO NOT generate GUI, main entry point, test suite, or documentation in this turn" in scoped
    assert "ZERO CONVERSATIONAL ADVICE" in scoped

    # A filename or the phrase sanity check must not bypass verification.
    sanity_prompt = "Create hello.py sanity check file"
    assert sanity_prompt in prepare_phased_task_prompt(sanity_prompt)
    assert "Only the orchestrator can declare completion" in prepare_phased_task_prompt(sanity_prompt)


def test_project_profile_detection():
    """Verifies detection of project prefixes and expected file conventions."""
    profile_chess = detect_project_profile("Build a Chess GUI Game")
    assert profile_chess["logic"] == "chess_logic.py"
    assert profile_chess["gui"] == "chess_gui.py"
    assert profile_chess["tests"] == "test_chess.py"

    profile_ludo = detect_project_profile("Create a multiplayer Ludo game")
    assert profile_ludo["logic"] == "ludo_logic.py"
    assert profile_ludo["gui"] == "ludo_gui.py"
    assert profile_ludo["tests"] == "test_ludo.py"

    profile_sudoku = detect_project_profile("Build Sudoku with 3 levels")
    assert profile_sudoku["logic"] == "sudoku_logic.py"
    assert profile_sudoku["gui"] == "sudoku_gui.py"
    assert profile_sudoku["tests"] == "test_sudoku.py"


# =====================================================================
# 8. Executable Entrypoint
# =====================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("Executing NodeCore Standalone Unit Test Suite via PyTest")
    print("=" * 70)
    exit_code = pytest.main(["-v", __file__])
    sys.exit(exit_code)
