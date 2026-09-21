"""Fresh, append-only live chess attempts through Cloud Code's normal launcher.

No application source is provided by this runner. --url is always explicit.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'NodeCore'), str(ROOT / 'benchmarks')]
ORIGINAL = ROOT / 'benchmark_outputs' / 'step4-post-repair-20260921'
MODEL = 'qwen2.5-coder:32b'
REQUIRED = ['chess_logic.py', 'chess_gui.py', 'main.py', 'test_chess.py',
            'README.md', 'RunningGUIDE.txt', 'requirements.txt']


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')


def validate_model_provenance(models):
    """The supplied bridge proved that an advertised name alone is insufficient."""
    item = next((m for m in models if m.get('id') == MODEL), None)
    if item is None:
        raise RuntimeError('Required model is not advertised; no substitution allowed')
    if item.get('loaded_model') != 'Qwen/Qwen2.5-Coder-32B-Instruct':
        raise RuntimeError('32B loaded-model provenance is absent or mismatched. '
                           'Use the corrected bridge; an advertised alias alone does not qualify.')
    count = item.get('parameter_count')
    if isinstance(count, bool) or not isinstance(count, int) or not 24_000_000_000 <= count <= 42_000_000_000:
        raise RuntimeError('Loaded parameter count does not establish the required 32B model')
    if item.get('supports_full_history') is not True or item.get('usage_source') != 'tokenizer':
        raise RuntimeError('Bridge must preserve full conversation history and report actual tokenizer usage')
    limit = item.get('max_output_tokens')
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 16384:
        raise RuntimeError('Server output limit is below the required 16384-token configuration')
    return item


def allocate(root, outputs):
    root.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    indices = [int(m.group(1)) for parent in (root, outputs) for p in parent.iterdir()
               if (m := re.fullmatch(r'Testing-0\.(\d+)', p.name, re.I))]
    index = max(indices, default=0) + 1
    while True:
        name = f'Testing-0.{index}'
        try:
            workspace = root / name
            workspace.mkdir(exist_ok=False)
        except FileExistsError:
            index += 1
            continue
        # Never overwrite evidence, including a concurrently reserved attempt.
        evidence = outputs / name
        evidence.mkdir(exist_ok=False)
        return workspace.resolve(), evidence.resolve()


def preflight(endpoint, output):
    import httpx
    api = endpoint.rstrip('/')
    if not api.endswith('/v1'):
        api += '/v1'
    report = {'started_at': now(), 'endpoint': endpoint, 'model': MODEL,
              'python': sys.executable, 'requests': [], 'ready': False}
    try:
        import chess
        from playwright.sync_api import sync_playwright
        report['dependencies'] = {name: importlib.metadata.version(name)
                                  for name in ('python-chess', 'chess', 'playwright', 'pyautogen', 'openai')}
        report['chess_module'] = chess.__file__
        report['api_symbols'] = {name: hasattr(chess, name) for name in ('Board', 'Move', 'Color', 'Status', 'CastleRights')}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            report['browser_version'] = browser.version
            browser.close()
        with httpx.Client(timeout=45) as client:
            for path, payload in [('/models', None), ('/chat/completions', {
                'model': MODEL, 'messages': [{'role': 'user', 'content': 'Reply exactly: LIVE_PREFLIGHT_OK'}],
                'max_tokens': 24, 'temperature': 0})]:
                start = time.monotonic()
                response = client.get(api + path) if payload is None else client.post(api + path, json=payload)
                item = {'url': api + path, 'status': response.status_code,
                        'elapsed_seconds': time.monotonic() - start, 'request': payload}
                report['requests'].append(item)
                try:
                    item['response'] = response.json()
                except ValueError:
                    item['body_excerpt'] = response.text[:1000]
                response.raise_for_status()
                data = item['response']
                if payload is None:
                    report['model_provenance'] = validate_model_provenance(data.get('data', []))
                else:
                    choice = data['choices'][0]
                    if not choice.get('message', {}).get('content', '').strip():
                        raise RuntimeError('Minimal completion probe returned no content')
                    if data.get('model') != MODEL:
                        raise RuntimeError('Probe response model does not match requested model')
        report['ready'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    report['finished_at'] = now()
    save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--reason', required=True, help='Why this changed system merits a new attempt')
    parser.add_argument('--benchmark-root', type=Path, default=Path(r'C:\Users\Acer\Desktop\Projects\Tests'))
    args = parser.parse_args()
    workspace, out = allocate(args.benchmark_root, ROOT / 'benchmark_outputs')
    print(f'ATTEMPT {workspace.name} EVIDENCE {out}', flush=True)
    prompt_source, checker_source = ORIGINAL / 'chess-prompt.txt', ORIGINAL / 'chess_acceptance.py'
    prompt_path, checker = out / 'chess-prompt.txt', out / 'chess_acceptance.py'
    prompt_path.write_bytes(prompt_source.read_bytes())
    checker.write_bytes(checker_source.read_bytes())
    # Freeze every executable project source, including uncommitted repairs.
    sources = {p.relative_to(ROOT).as_posix(): digest(p) for p in ROOT.rglob('*.py')
               if not any(part in {'.cache', '.git', 'benchmark_outputs', 'scratch', 'TESTING', '__pycache__'}
                          for part in p.relative_to(ROOT).parts)}
    save(out / 'source-hashes.json', sources)
    with zipfile.ZipFile(out / 'source-snapshot.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for name in sources:
            archive.write(ROOT / name, name)
    patch = subprocess.run(['git', 'diff', '--binary'], cwd=ROOT, capture_output=True, check=True).stdout
    (out / 'working-tree.patch').write_bytes(patch)
    meta = {'started_at': now(), 'workspace': str(workspace), 'evidence': str(out),
            'reason': args.reason, 'endpoint': args.url, 'model': MODEL, 'response_limit': 50,
            'max_tokens': 16384, 'cache_seed': None, 'manual_application_edits': 0,
            'prompt_sha256': digest(prompt_path), 'checker_sha256': digest(checker),
            'source_manifest_sha256': digest(out / 'source-hashes.json'),
            'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'git_status': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
            'configuration': json.loads((ROOT / 'orchestrator_config.json').read_text()),
            'python': sys.executable, 'status': 'PREFLIGHT', 'responses': 0,
            'browser_result': 'not run', 'launch_result': 'not run', 'independent_acceptance': 'not run'}
    save(out / 'metadata.json', meta)
    check = preflight(args.url, out / 'preflight.json')
    if not check['ready']:
        meta.update(status='PREFLIGHT_FAILED', finished_at=now(), failure_causes=[check.get('error')])
        save(out / 'metadata.json', meta)
        print(json.dumps({'status': meta['status'], 'error': check.get('error'), 'evidence': str(out)}), flush=True)
        return 2
    meta.update(status='RUNNING', preflight_passed=True)
    save(out / 'metadata.json', meta)

    import httpx
    import launcher_gui
    from node_core.tools import NodeLog
    from node_core.workflow import python_command
    from measure_chess import requirement_matrix
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    prompt = prompt_path.read_text(encoding='utf-8')
    acceptance = {'task': prompt, 'required_files': REQUIRED,
                  'checks': [{'requirement': 'Original independent chess engine, launch and browser acceptance',
                              'command': python_command(str(checker), str(workspace))}]}
    (workspace / '.cloudcode').mkdir()
    save(workspace / '.cloudcode' / 'acceptance.json', acceptance)
    factory, send = launcher_gui.create_cloud_llm_config, httpx.Client.send
    request_origin = urlsplit(args.url).netloc

    def append(name, value):
        with (out / name).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, default=str) + '\n')

    def config(*a, **kw):
        value = factory(*a, **kw)
        value['cache_seed'] = None
        value['config_list'][0]['max_retries'] = 0
        return value

    def observe(client, request, *a, **kw):
        if request.url.netloc.decode() != request_origin:
            return send(client, request, *a, **kw)
        start = time.monotonic()
        record = {'started_at': now(), 'url': str(request.url)}
        try:
            record['request'] = json.loads(request.content)
            response = send(client, request, *a, **kw)
            record.update(http_status=response.status_code)
            raw = response.read()
            try:
                record['response'] = json.loads(raw)
                data = record['response']
                choice = (data.get('choices') or [{}])[0]
                text = choice.get('message', {}).get('content', '') or ''
                record['metrics'] = {'usage': data.get('usage'), 'finish_reason': choice.get('finish_reason'),
                                     'response_chars': len(text), 'requested_max_tokens': record['request'].get('max_tokens'),
                                     'prompt_chars': sum(len(str(m.get('content', ''))) for m in record['request'].get('messages', []))}
            except ValueError:
                record['response_excerpt'] = raw.decode(errors='replace')[:2000]
            return response
        except Exception as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            record['elapsed_seconds'] = time.monotonic() - start
            append('http.jsonl', record)

    def event(name, data):
        append('events.jsonl', {'time': now(), 'event': name, 'data': data})

    started = time.monotonic()
    launcher_gui.create_cloud_llm_config, httpx.Client.send = config, observe
    NodeLog.add_listener(event)
    try:
        result = launcher_gui.run_autonomous_orchestrator(
            workspace_root=str(workspace), base_url=args.url, model=MODEL,
            task_prompt=prompt, max_rounds=50, acceptance=acceptance)
        save(out / 'result.json', result)
    finally:
        launcher_gui.create_cloud_llm_config, httpx.Client.send = factory, send
        NodeLog.remove_listener(event)
    with (out / 'independent.log').open('w', encoding='utf-8') as log:
        try:
            measured = subprocess.run([sys.executable, str(ROOT / 'benchmarks' / 'measure_chess.py'),
                                      str(checker), str(workspace), str(out / 'independent.json')],
                                     cwd=workspace, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            independent = json.loads((out / 'independent.json').read_text(encoding='utf-8'))
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            independent = {'passed': False, 'tests': {}, 'unchanged': False, 'error': str(exc)}
            save(out / 'independent.json', independent)
    imports = [c for c in result.get('commands', []) if c.get('kind') == 'imports']
    imports_passed = bool(imports and imports[-1].get('exit_code') == 0)
    unchanged = (independent.get('unchanged', False) and digest(checker) == meta['checker_sha256']
                 and digest(checker_source) == meta['checker_sha256'] and digest(prompt_source) == meta['prompt_sha256'])
    matrix = requirement_matrix(independent['tests'], imports_passed, unchanged)
    save(out / 'requirements.json', matrix)
    browser = next((v['status'] for k, v in independent['tests'].items()
                    if k.endswith('test_17_18_real_browser_playthrough_and_launch')), 'unverified')
    meta.update(finished_at=now(), elapsed_seconds=time.monotonic() - started,
                status=result['status'], responses=result['rounds'], generated_files=result['files_actual'],
                independent_acceptance=independent['passed'], browser_result=browser,
                launch_result='verified' if browser == 'passed' else 'see independent.log',
                functional_completion=matrix, checker_and_prompt_unchanged=unchanged,
                failure_causes=result.get('remaining_requirements', []))
    save(out / 'metadata.json', meta)
    print(json.dumps({'status': result['status'], 'functional': matrix['percent'], 'evidence': str(out)}), flush=True)
    return 0 if matrix['percent'] >= 70 and unchanged else 1


if __name__ == '__main__':
    sys.exit(main())
