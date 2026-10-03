"""Standalone, bounded Jev benchmark client; never part of digest production.

Wire format and pinned model: https://docs.typesafe.ai/api and /models.
Each attempt has an atomic receipt; pending/uncertain attempts never auto-retry.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

import httpx

from . import digest_runtime as runtime
from . import digest_sources as sources

ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
DEFAULT_MODEL = 'jev-1.13.0'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RequestUncertain = runtime.RequestUncertain


class JevError(runtime.RuntimeError):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _read(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('status') not in ('pending', 'uncertain', 'failed', 'succeeded'):
            raise ValueError()
        if not isinstance(data.get('budget_key'), str):
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise JevError('Jev receipt is unreadable; inspect before continuing') from None


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _validate(data, questions):
    if not isinstance(data, dict) or not isinstance(data.get('model'), str) or not data['model']:
        raise ValueError()
    if not isinstance(data.get('answers'), dict) or set(data['answers']) != set(questions):
        raise ValueError()
    usage = data.get('usage')
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
        raise ValueError()
    for key, answer in data['answers'].items():
        kind = questions[key]['type']
        if not isinstance(answer, dict) or answer.get('type') != kind:
            raise ValueError()
        if kind == 'noul':
            if not _number(answer.get('noul'), 0, 1):
                raise ValueError()
            continue
        probabilities = answer.get('probabilities')
        if not isinstance(probabilities, dict) or not probabilities or not all(_number(p, 0, 1) for p in probabilities.values()):
            raise ValueError()
        if not _number(answer.get('confidence'), 0, 1):
            raise ValueError()
        if kind == 'choice' and (not isinstance(answer.get('choice'), str) or answer['choice'] not in probabilities):
            raise ValueError()
        if kind == 'score' and (not _number(answer.get('score'), 0, len(questions[key]['criteria']) - 1) or not isinstance(answer.get('legend'), dict)):
            raise ValueError()
    runtime._json(data)  # Reject non-finite JSON, including unrecognized fields.


class JevClient:
    def __init__(self, root, *, env_file, model=DEFAULT_MODEL, client=None):
        config = runtime._model_env(env_file)
        self._api_key = config.get('TYPESAFE_API_KEY')
        if not isinstance(self._api_key, str) or not self._api_key.strip():
            raise runtime.ConfigurationRequired('configure TYPESAFE_API_KEY in the selected env file')
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise runtime.ConfigurationRequired('configure a bounded Jev model name')
        self.folder, self.model, self.client = Path(root) / 'jev-receipts', model, client

    def request(self, state: dict, questions: dict, budget_key: str, max_requests: int = 32):
        if type(max_requests) is not int or not 1 <= max_requests <= 1000:
            raise JevError('request budget must be an integer in 1..1000')
        if not isinstance(budget_key, str) or not budget_key.strip() or len(budget_key) > 256:
            raise JevError('a bounded budget key is required')
        if not isinstance(state, dict) or not isinstance(questions, dict) or not questions:
            raise JevError('state and nonempty questions must be objects')
        for key, question in questions.items():
            if not isinstance(key, str) or not key or not isinstance(question, dict):
                raise JevError('invalid Jev question')
            kind, criteria = question.get('type'), question.get('criteria')
            if kind not in ('noul', 'choice', 'score') or not isinstance(question.get('instructions'), (str, dict, list)):
                raise JevError('invalid Jev question type or instructions')
            if kind == 'choice' and (not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255):
                raise JevError('Choice requires 1..255 options')
            if kind == 'score' and (not isinstance(criteria, list) or not 2 <= len(criteria) <= 10):
                raise JevError('Score requires 2..10 levels')
        payload = {'model': self.model, 'state': state, 'questions': questions}
        try:
            serialized = runtime._json(payload)
            if len(serialized) > 60000:
                raise ValueError()
        except (ValueError, TypeError):
            raise JevError('Jev input must be finite JSON within 60000 characters') from None
        key = runtime._hash({'endpoint': ENDPOINT, **payload})
        try:
            with sources._run_lock(self.folder):
                return self._locked_request(key, payload, budget_key, max_requests)
        except sources.SourceError:
            raise JevError('another Jev request is active; no request was sent') from None

    def _locked_request(self, key, payload, budget_key, max_requests):
        path = self.folder / (key + '.json')
        if path.exists():
            receipt = _read(path)
            if receipt['status'] in ('pending', 'uncertain'):
                raise RequestUncertain('Jev outcome unknown; inspect provider before resolving: ' + key)
            if receipt['status'] == 'failed':
                raise JevError('previous Jev request failed: ' + key, status_code=receipt.get('status_code'))
            try:
                _validate(receipt['output'], payload['questions'])
                return {name: receipt[name] for name in ('request_id', 'output', 'usage', 'elapsed_seconds', 'model')} | {'reused': True}
            except (ValueError, KeyError, TypeError):
                raise JevError('invalid successful Jev receipt; inspect before continuing') from None
        if sum(_read(p)['budget_key'] == budget_key for p in self.folder.glob('*.json')) >= max_requests:
            raise JevError('Jev request budget exhausted')
        receipt = {'request_id': key, 'budget_key': budget_key, 'endpoint': ENDPOINT, 'model': self.model,
                   'status': 'pending', 'created_at': time.time()}
        sources._write(path, receipt)
        started = time.perf_counter()
        client = self.client or httpx.Client(timeout=60, follow_redirects=False)
        try:
            with client.stream('POST', ENDPOINT, json=payload, headers={'Authorization': 'Bearer ' + self._api_key},
                               timeout=60, follow_redirects=False) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ValueError()
            data = json.loads(raw)
            _validate(data, payload['questions'])
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            self._save(path, receipt, started, 'failed', error='HTTP ' + str(code), status_code=code)
            raise JevError('Jev HTTP ' + str(code) + ': ' + key, status_code=code) from None
        except httpx.TransportError as exc:
            self._save(path, receipt, started, 'uncertain', error=type(exc).__name__)
            raise RequestUncertain('Jev transport interrupted; outcome unknown: ' + key) from None
        except (ValueError, KeyError, TypeError, OverflowError):
            self._save(path, receipt, started, 'failed', error='invalid_response')
            raise JevError('Jev response failed JSON/bounds validation: ' + key) from None
        finally:
            if self.client is None:
                client.close()
        self._save(path, receipt, started, 'succeeded', output=data, usage=data['usage'], model=data['model'])
        return {name: receipt[name] for name in ('request_id', 'output', 'usage', 'elapsed_seconds', 'model')} | {'reused': False}

    @staticmethod
    def _save(path, receipt, started, status, **fields):
        receipt.update(status=status, elapsed_seconds=time.perf_counter() - started, updated_at=time.time(), **fields)
        sources._write(path, receipt)
