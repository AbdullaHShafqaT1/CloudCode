"""Regression coverage for evidence-backed benchmark preparation repairs."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest

from node_core.agents import bounded_model_messages, detect_project_profile
from node_core.workflow import TaskWorkflow, python_command
from benchmarks.measure_chess import requirement_matrix
from benchmarks.run_chess_mission import allocate, preflight, validate_model_provenance


def workflow(tmp_path, **kw):
    task = 'Build an application with GUI, tests and guide'
    return TaskWorkflow(tmp_path, task, detect_project_profile(task), **kw)


@pytest.mark.parametrize('source', [
    'def f(): return 1', 'class A: value = 1', 'value = 1,',
    'def f():\n    return "\\\"\\\"\\\""', '# an unmatched triple quote """\ndef f(): return 2',
])
def test_valid_python_is_not_rejected_by_text_heuristics(tmp_path, source):
    assert workflow(tmp_path).detect_incomplete_artifact(source, 'app_logic.py') == []


@pytest.mark.parametrize('source', ['def f(', 'value = [1,', 'def f():\n', 'value = """unterminated'])
def test_incomplete_python_remains_rejected(tmp_path, source):
    assert workflow(tmp_path).detect_incomplete_artifact(source, 'app_logic.py')


def test_terminal_python_is_verification_python(tmp_path):
    w = workflow(tmp_path)
    result = w.execute('python -c "import sys; print(sys.executable)"')
    assert w.passed(result)
    assert str(Path(sys.executable)).lower() in str(result).replace('\\\\', '\\').lower()


def test_recovery_allows_inspection_then_repair(tmp_path):
    w = workflow(tmp_path, stall_limit=2, max_rounds=12)
    (tmp_path / 'notes.txt').write_text('Real workspace evidence')
    for _ in range(4):
        reply = w.process('TERMINATE')
        if reply and 'STALL RECOVERY' in reply:
            break
    assert w._recovery_attempted
    reply = w.process('```read\nnotes.txt\n```')
    assert w.status == 'IN_PROGRESS' and 'Real workspace evidence' in reply
    w.process('```python\n# filename: app_logic.py\ndef value(): return 42\n```')
    assert w.phase == 3
    evidence = w.report()['recovery_evidence'][0]
    assert evidence['files_changed'] and evidence['phase_advanced']


def test_budget_wins_over_recovery_at_last_response(tmp_path):
    w = workflow(tmp_path, stall_limit=1, max_rounds=1)
    assert w.process('TERMINATE') is None
    assert w.status == 'MAX_ROUNDS_REACHED'
    assert not w._recovery_attempted


def test_inference_history_retains_requirements_and_feedback_without_mutation():
    messages = [{'role': 'user', 'content': 'Original requirements'}]
    messages += [{'role': 'assistant' if i % 2 == 0 else 'user', 'content': str(i) * 2000} for i in range(12)]
    messages.append({'role': 'user', 'content': 'Latest actual feedback'})
    before = copy.deepcopy(messages)
    bounded = bounded_model_messages(messages, max_history_chars=5000)
    assert bounded[0] == messages[0] and bounded[-1] == messages[-1]
    assert sum(len(m['content']) for m in bounded) <= 5000
    assert messages == before


def test_adaptive_feedback_prioritizes_imports_over_missing_tests(tmp_path):
    w = workflow(tmp_path)
    w.consecutive_identical_errors = 2
    reply = w._build_adaptive_prefix(['Import failed: cannot import name Wrong', 'test_app.py: file is missing'])
    assert 'before generating missing files' in reply
    assert 'Produce only' not in reply


def test_fresh_allocation_preserves_old_and_failed_attempts(tmp_path):
    root, outputs = tmp_path / 'Tests', tmp_path / 'outputs'
    root.mkdir(); outputs.mkdir()
    old = root / 'TESTING-0.6'; old.mkdir()
    (old / 'keep.txt').write_text('untouched')
    (outputs / 'Testing-0.8').mkdir()
    workspace, evidence = allocate(root, outputs)
    assert workspace.name == evidence.name == 'Testing-0.9'
    assert list(workspace.iterdir()) == []
    assert (old / 'keep.txt').read_text() == 'untouched'
    assert allocate(root, outputs)[0].name == 'Testing-0.10'


def test_measurement_never_counts_files_or_partial_failed_browser_test():
    matrix = requirement_matrix({'suite.test_17_18_real_browser_playthrough_and_launch': {'status': 'failed'}})
    assert matrix['percent'] == 0
    records = {'suite.test_01_initial_position': {'status': 'passed'}}
    assert requirement_matrix(records)['verified'] == 1
    assert requirement_matrix(records, unchanged=False)['verified'] == 0
    assert requirement_matrix({})['total'] == matrix['total']


def test_feedback_carries_observed_source_and_ends_with_specific_action(tmp_path):
    w = workflow(tmp_path)
    reply = w.process('```python\n# filename: app_logic.py\ndef value(): return 73\n```')
    assert reply.startswith('Original task requirements (unchanged):\n' + w.task)
    assert 'CURRENT FILE app_logic.py:' in reply and 'return 73' in reply
    assert 'NEXT RESPONSE: produce one concise complete app_gui.py' in reply
    assert 'app_logic.py' in w.files_read
    assert w.read_evidence == set(), 'Automatic checkpoints must not disguise a stall as reader progress'


def test_checkpoint_targets_import_failure_file_not_missing_tests(tmp_path):
    w = workflow(tmp_path)
    w.phase = 4
    (tmp_path / 'app_logic.py').write_text('def value(): return 73')
    (tmp_path / 'app_gui.py').write_text('from json import Wrong')
    errors = [f'Import failed: cannot import name Wrong\n  File "{tmp_path / "app_gui.py"}", line 1',
              'test_app.py: file is missing']
    context, action = w.repair_checkpoint(errors)
    assert 'CURRENT FILE app_gui.py' in context
    assert 'blocking import in app_gui.py' in action


def test_benchmark_rejects_model_label_without_loaded_provenance():
    with pytest.raises(RuntimeError, match='provenance'):
        validate_model_provenance([{'id': 'qwen2.5-coder:32b'}])
    truthful = {'id': 'qwen2.5-coder:32b', 'loaded_model': 'Qwen/Qwen2.5-Coder-32B-Instruct',
                'parameter_count': 32_000_000_000, 'supports_full_history': True,
                'usage_source': 'tokenizer', 'max_output_tokens': 16384}
    assert validate_model_provenance([truthful]) == truthful
    with pytest.raises(RuntimeError, match='parameter count'):
        validate_model_provenance([{**truthful, 'parameter_count': 3_000_000_000}])
    with pytest.raises(RuntimeError, match='output limit'):
        validate_model_provenance([{**truthful, 'max_output_tokens': 1000}])


@pytest.mark.parametrize('prior_command', [False, True])
def test_cancel_before_test_execution_never_corrupts_command_evidence(tmp_path, prior_command):
    w = workflow(tmp_path, should_stop=lambda: True)
    (tmp_path / 'test_app.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_one(self): self.assertTrue(True)')
    if prior_command:
        w.commands.append({'kind': 'prior', 'output': 'untouched'})
    w.verify()
    assert w.status == 'CANCELLED'
    assert w.commands == ([{'kind': 'prior', 'output': 'untouched'}] if prior_command else [])


def test_import_credit_requires_every_final_application_module(tmp_path, monkeypatch):
    from benchmarks.run_chess_mission import assess_workspace
    from node_core.tools import NodePulse
    workspace, out = tmp_path / 'ws', tmp_path / 'out'
    workspace.mkdir(); out.mkdir()
    (workspace / 'chess_logic.py').write_text('def value(): return 1')
    (workspace / 'chess_gui.py').write_text('from chess_logic import value')
    # main.py deliberately missing; the workflow's partial smoke would pass.
    (out / 'independent.json').write_text(json.dumps({'passed': False, 'unchanged': True, 'tests': {}}))
    execute = NodePulse.execute_command
    def observed(command, **kwargs):
        if 'measure_chess.py' in command:
            assert kwargs['timeout'] == 180
            return {'status': 'error', 'exit_code': 1}
        return execute(command, **kwargs)
    monkeypatch.setattr(NodePulse, 'execute_command', observed)
    _, imports_passed = assess_workspace(out / 'checker.py', workspace, out)
    assert not imports_passed
    assert 'main' in (out / 'final-imports.json').read_text()


def test_timed_out_measurement_cannot_retain_pass_evidence(tmp_path, monkeypatch):
    from benchmarks.run_chess_mission import assess_workspace
    from node_core.tools import NodePulse
    workspace, out = tmp_path / 'ws', tmp_path / 'out'
    workspace.mkdir(); out.mkdir()
    (out / 'independent.json').write_text(json.dumps({'passed': True, 'unchanged': True, 'tests': {}}))
    monkeypatch.setattr(NodePulse, 'execute_command', lambda *a, **kw: {'status': 'timeout', 'exit_code': -1})
    measured, imports = assess_workspace(out / 'checker.py', workspace, out)
    assert not measured['passed'] and not measured['unchanged'] and not imports


def test_setup_failure_is_preserved_in_final_benchmark_metadata(tmp_path, monkeypatch):
    from benchmarks import run_chess_mission as mission
    from types import SimpleNamespace
    import launcher_gui
    original = tmp_path / 'original'; original.mkdir()
    (original / 'chess-prompt.txt').write_text('Original fixture task')
    (original / 'chess_acceptance.py').write_text('# independent fixture')
    (tmp_path / 'orchestrator_config.json').write_text('{}')
    monkeypatch.setattr(mission, 'ROOT', tmp_path)
    monkeypatch.setattr(mission, 'ORIGINAL', original)
    monkeypatch.setattr(mission, 'preflight', lambda *a: {'ready': True})
    monkeypatch.setattr(mission.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=b''))
    monkeypatch.setattr(mission.subprocess, 'check_output', lambda *a, **kw: 'fixture-commit')
    monkeypatch.setattr(mission, 'assess_workspace', lambda *a: ({'passed': False, 'unchanged': True, 'tests': {}}, False))
    monkeypatch.setattr(launcher_gui, 'run_autonomous_orchestrator', lambda **kw: {
        'status': 'FAILED', 'rounds': 0, 'remaining_requirements': ['setup failure']})
    monkeypatch.setattr(sys, 'argv', ['runner', '--url', 'https://example.invalid', '--reason', 'fixture',
                                     '--benchmark-root', str(tmp_path / 'Tests')])
    assert mission.main() == 1
    metadata = json.loads((tmp_path / 'benchmark_outputs/Testing-0.1/metadata.json').read_text())
    assert metadata['status'] == 'FAILED' and metadata['generated_files'] == {}
    assert not metadata['independent_acceptance']


def test_browser_pass_does_not_claim_unchecked_unicode_or_visible_winner():
    matrix = requirement_matrix({'suite.test_17_18_real_browser_playthrough_and_launch': {'status': 'passed'}})
    rows = {row['requirement']: row['status'] for row in matrix['requirements']}
    assert rows['All pieces rendered with Unicode chess symbols'] == 'unverified'
    assert rows['Winner visibly identified in browser'] == 'unverified'
