"""Real backend contract tests with tiny model/tokenizer doubles, no GPU required."""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from legacynode.cloud_runners.qwen_bridge_server import QwenBackend, BridgeRequestError


class Batch(dict):
    def to(self, device):
        return self


class Tokenizer:
    eos_token_id = 99

    def apply_chat_template(self, messages, **kwargs):
        self.messages = messages
        return 'templated conversation'

    def __call__(self, text, **kwargs):
        self.tokenize_options = kwargs
        return Batch(input_ids=SimpleNamespace(shape=(1, 7)))

    def decode(self, ids, **kwargs):
        return 'decoded output'


class Model:
    device = 'cpu'
    config = SimpleNamespace(_name_or_path='Qwen/Qwen2.5-Coder-3B-Instruct', max_position_embeddings=32768)
    generation_config = SimpleNamespace(eos_token_id=[99])

    def num_parameters(self):
        return 3_000_000_000

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return [[0] * 7 + self.output]


def backend():
    model, tokenizer = Model(), Tokenizer()
    model.output = [10, 20, 99]
    return QwenBackend(model, tokenizer, inference_context=nullcontext)


def request(**kw):
    return {'model': 'qwen2.5-coder:3b', 'messages': [{'role': 'user', 'content': 'Hi'}], **kw}


def test_loaded_3b_cannot_masquerade_as_32b():
    b = backend()
    assert b.metadata()['id'] == 'qwen2.5-coder:3b'
    assert b.metadata()['parameter_count'] == 3_000_000_000
    with pytest.raises(BridgeRequestError, match='actually loaded'):
        b.complete(request(model='qwen2.5-coder:32b'))


def test_identity_and_actual_parameter_count_must_agree():
    model = Model()
    model.config = SimpleNamespace(_name_or_path='Qwen/Qwen2.5-Coder-32B-Instruct', max_position_embeddings=32768)
    with pytest.raises(ValueError, match='parameter count'):
        QwenBackend(model, Tokenizer())


def test_history_budget_temperature_and_real_usage_are_preserved():
    b = backend()
    messages = [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'remember 731'},
                {'role': 'assistant', 'content': 'recorded'}, {'role': 'user', 'content': 'recall'}]
    result = b.complete(request(messages=messages, max_tokens=16384, temperature=.4))
    assert b.tokenizer.messages == messages
    assert b.model.kwargs['max_new_tokens'] == 16384
    assert b.model.kwargs['do_sample'] is True and b.model.kwargs['temperature'] == .4
    assert b.tokenizer.tokenize_options['add_special_tokens'] is False
    assert result['usage'] == {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}
    assert result['choices'][0]['finish_reason'] == 'stop'


def test_one_token_budget_and_length_finish_reason():
    b = backend(); b.model.output = [10]
    result = b.complete(request(max_tokens=1, temperature=0))
    assert b.model.kwargs['max_new_tokens'] == 1
    assert b.model.kwargs['do_sample'] is False
    assert 'temperature' not in b.model.kwargs
    assert result['choices'][0]['finish_reason'] == 'length'


def test_eos_at_exact_budget_is_stop():
    b = backend(); b.model.output = [99]
    assert b.complete(request(max_tokens=1))['choices'][0]['finish_reason'] == 'stop'


@pytest.mark.parametrize('value', [0, -1, True, 16385, None])
def test_invalid_budget_is_explicitly_rejected(value):
    with pytest.raises(BridgeRequestError, match='max_tokens'):
        backend().complete(request(max_tokens=value))


def test_context_overflow_is_not_silent_truncation():
    b = backend(); b.context_limit = 10
    with pytest.raises(BridgeRequestError, match='Context overflow'):
        b.complete(request(max_tokens=4))


def test_streaming_not_silently_ignored():
    with pytest.raises(BridgeRequestError, match='stream=false'):
        backend().complete(request(stream=True))


def test_http_routes_report_real_model_and_reject_mismatch():
    from fastapi.testclient import TestClient
    from legacynode.cloud_runners.qwen_bridge_server import create_app
    model = Model(); model.output = [10, 99]
    client = TestClient(create_app(model, Tokenizer(), inference_context=nullcontext))
    meta = client.get('/v1/models').json()['data'][0]
    assert meta['id'] == 'qwen2.5-coder:3b' and meta['supports_full_history']
    assert client.post('/v1/chat/completions', json=request(model='qwen2.5-coder:32b')).status_code == 404
    response = client.post('/v1/chat/completions', json=request(max_tokens=32))
    assert response.status_code == 200
    assert response.json()['usage']['completion_tokens'] == 2
    assert response.json()['model'] == meta['id']
