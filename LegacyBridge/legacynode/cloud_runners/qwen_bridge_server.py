"""Corrected Colab bridge for an ALREADY loaded Transformers model/tokenizer.

No model downloads, tunnel creation or runtime changes happen on import.
Use create_app(model, tokenizer) from the notebook's server cell.
"""
import re
import threading
import time
import uuid


class BridgeRequestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class QwenBackend:
    def __init__(self, model, tokenizer, max_output_tokens=16384, inference_context=None):
        self.model, self.tokenizer = model, tokenizer
        self.loaded_model = str(model.config._name_or_path)
        match = re.fullmatch(r'Qwen/Qwen2\.5-Coder-(\d+(?:\.\d+)?)B-Instruct', self.loaded_model, re.I)
        # Identity comes from the loaded model, never from the request or a
        # hardcoded benchmark label. Unknown models retain their real repo id.
        self.model_id = f'qwen2.5-coder:{match.group(1).lower()}b' if match else self.loaded_model
        self.parameter_count = int(model.num_parameters())
        if match and not .75 * float(match.group(1)) * 1e9 <= self.parameter_count <= 1.3 * float(match.group(1)) * 1e9:
            raise ValueError('Loaded model parameter count disagrees with its configured identity')
        self.context_limit = int(model.config.max_position_embeddings)
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ValueError('max_output_tokens must be a positive integer')
        self.max_output_tokens = max_output_tokens
        self.inference_context = inference_context
        self.lock = threading.Lock()

    def metadata(self):
        return {'id': self.model_id, 'object': 'model', 'owned_by': 'loaded-transformers-model',
                'loaded_model': self.loaded_model, 'parameter_count': self.parameter_count,
                'context_window': self.context_limit, 'max_output_tokens': self.max_output_tokens,
                'supports_full_history': True, 'usage_source': 'tokenizer',
                'bridge_version': 'cloudcode-qwen-2'}

    def complete(self, request):
        if not isinstance(request, dict):
            raise BridgeRequestError('Request must be a JSON object')
        if request.get('model') != self.model_id:
            raise BridgeRequestError(f'Requested model is unavailable; actually loaded: {self.model_id}', 404)
        if request.get('stream', False):
            raise BridgeRequestError('This bridge supports stream=false only')
        messages = request.get('messages')
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or m.get('role') not in {'system', 'user', 'assistant'} or
            not isinstance(m.get('content'), str) for m in messages
        ):
            raise BridgeRequestError('messages must contain system/user/assistant text messages')
        requested = request.get('max_tokens', 16384)
        if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= self.max_output_tokens:
            raise BridgeRequestError(f'max_tokens must be 1..{self.max_output_tokens}; no silent cap is applied')
        temperature = request.get('temperature', .2)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise BridgeRequestError('temperature must be a number in [0, 2]')
        # Keep ALL messages and roles; strip only transport-only metadata.
        conversation = [{'role': m['role'], 'content': m['content']} for m in messages]
        with self.lock:
            text = self.tokenizer.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
            inputs = self.tokenizer(text, return_tensors='pt', add_special_tokens=False).to(self.model.device)
            prompt_tokens = int(inputs['input_ids'].shape[1])
            if prompt_tokens + requested > self.context_limit:
                raise BridgeRequestError(f'Context overflow: {prompt_tokens} prompt + {requested} requested output '
                                         f'exceeds {self.context_limit}. Reduce history or output budget.', 413)
            context = self.inference_context
            if context is None:
                import torch
                context = torch.inference_mode
            kwargs = {'max_new_tokens': requested, 'do_sample': temperature > 0}
            if temperature > 0:
                kwargs['temperature'] = temperature
            with context():
                outputs = self.model.generate(**inputs, **kwargs)
            generated = outputs[0][prompt_tokens:]
            count = len(generated)
            eos = getattr(getattr(self.model, 'generation_config', None), 'eos_token_id', None)
            if eos is None:
                eos = getattr(self.tokenizer, 'eos_token_id', None)
            eos_ids = set(eos if isinstance(eos, (list, tuple)) else [eos])
            stopped = bool(count and int(generated[-1]) in eos_ids)
            finish = 'stop' if stopped or count < requested else 'length'
            answer = self.tokenizer.decode(generated, skip_special_tokens=True)
        return {'id': 'chatcmpl-' + uuid.uuid4().hex, 'object': 'chat.completion',
                'created': int(time.time()), 'model': self.model_id,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': finish}],
                'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': count, 'total_tokens': prompt_tokens + count}}


def create_app(model, tokenizer, **options):
    from fastapi import FastAPI, HTTPException
    app = FastAPI(title='Cloud Code Qwen Bridge', version='2.0')
    backend = QwenBackend(model, tokenizer, **options)
    app.state.backend = backend

    @app.get('/')
    @app.get('/models')
    @app.get('/v1/models')
    @app.get('/api/tags')
    def models():
        meta = backend.metadata()
        return {'object': 'list', 'status': 'healthy', 'data': [meta], 'models': [meta]}

    @app.post('/v1/chat/completions')
    @app.post('/chat/completions')
    def complete(request: dict):
        # Sync route runs in FastAPI's worker pool. GPU requests are serialized
        # by the backend lock while health requests remain responsive.
        try:
            return backend.complete(request)
        except BridgeRequestError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    return app
