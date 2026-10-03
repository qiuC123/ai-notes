"""Durable, single-host digest execution and model request receipts.

This module never sends a notification. Its outbox is an explicit delivery
boundary; an operator/channel adapter must acknowledge a delivery separately.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import threading
import uuid

import httpx

from . import digest

DB_PATH = Path('data/weekly_digest/runtime.sqlite3')


class RuntimeError(ValueError):
    pass


class ConfigurationRequired(RuntimeError):
    pass


class RequestUncertain(RuntimeError):
    pass


def _model_env(path):
    """Read a single, explicit dotenv file without shell evaluation/interpolation."""
    try:
        lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    except (OSError, UnicodeError, ValueError):
        raise ConfigurationRequired('cannot read model env file as UTF-8') from None
    values = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
        if match is None:
            raise ConfigurationRequired(f'invalid model env assignment at line {number}')
        name, value = match.groups()
        if value.startswith(('"', "'")):
            quoted = re.fullmatch(r'''(?:"([^"]*)"|'([^']*)')\s*(?:#.*)?''', value)
            if quoted is None:
                raise ConfigurationRequired(f'invalid model env quoting at line {number}')
            value = next(part for part in quoted.groups() if part is not None)
        else:
            value = re.split(r'(?:^|\s+)#', value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _completed_at(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


@contextlib.contextmanager
def _db(root, *, write=True):
    path = Path(root) / DB_PATH
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=15)
        con.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
          job_id TEXT PRIMARY KEY, business_key TEXT UNIQUE NOT NULL, action TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL, owner TEXT, lease_until REAL,
          attempts INTEGER NOT NULL DEFAULT 0, checkpoints TEXT NOT NULL DEFAULT '{}',
          result TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (
          request_id TEXT PRIMARY KEY, budget_key TEXT NOT NULL, stage TEXT NOT NULL,
          model TEXT NOT NULL, status TEXT NOT NULL, output TEXT, usage TEXT,
          error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (
          notice_id TEXT PRIMARY KEY, issue_key TEXT NOT NULL, payload TEXT NOT NULL,
          status TEXT NOT NULL, receipt TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS request_retries (
          attempt_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, budget_key TEXT NOT NULL,
          stage TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, output TEXT,
          usage TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, reason TEXT NOT NULL);
        ''')
    else:
        con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)
        con.execute('PRAGMA query_only=ON')
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _job(row):
    if row is None:
        return None
    result = dict(row)
    for field in ('payload', 'checkpoints', 'result'):
        result[field] = json.loads(result[field]) if result[field] else None
    return result


def enqueue(root, action):
    if not isinstance(action, dict) or action.get('action') not in ('collect', 'generate', 'backfill_monthly'):
        raise RuntimeError('unsupported digest action')
    if action['action'] == 'collect':
        digest._date(action.get('period'), 'period')
    else:
        digest.period_window(action.get('ranking_type'), action.get('period'))
    # A delayed monthly draft can retry once on each collection date, rather
    # than permanently completing its one original monthly generation job.
    key = ':'.join([action['action'], action.get('ranking_type', ''), action['period'], action.get('retry_date', '')])
    job_id = _hash(key)
    now = time.time()
    with _db(root) as con, con:
        con.execute('INSERT OR IGNORE INTO jobs(job_id,business_key,action,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (job_id, key, action['action'], _json(action), 'queued', now, now))
        return _job(con.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone())


def schedule(root):
    plan = digest.dispatch(Path(root))  # exactly once; actual current Beijing time
    jobs = []
    for action in plan['actions']:
        value = dict(action)
        if value['action'] == 'backfill_monthly':
            value['retry_date'] = plan['evaluated_at'][:10]
        jobs.append(enqueue(root, value))
    return {'dispatch': plan, 'jobs': jobs}


def claim(root, owner, *, now=None, lease_seconds=300, job_id=None):
    now = time.time() if now is None else now
    if not owner or not 1 <= lease_seconds <= 3600:
        raise RuntimeError('owner and bounded lease are required')
    with _db(root) as con, con:
        con.execute('BEGIN IMMEDIATE')
        query="SELECT * FROM jobs WHERE (status='queued' OR (status='running' AND lease_until<?))"
        parameters=[now]
        if job_id is not None:
            query+=' AND job_id=?'; parameters.append(job_id)
        row=con.execute(query+' ORDER BY created_at,job_id LIMIT 1',parameters).fetchone()
        if row is None:
            return None
        con.execute("UPDATE jobs SET status='running',owner=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE job_id=?",
                    (owner, now + lease_seconds, now, row['job_id']))
        return _job(con.execute('SELECT * FROM jobs WHERE job_id=?', (row['job_id'],)).fetchone())


def _owned(con, job_id, owner):
    row = con.execute("SELECT * FROM jobs WHERE job_id=? AND owner=? AND status='running'", (job_id, owner)).fetchone()
    if row is None:
        raise RuntimeError('job lease belongs to another worker or is no longer running')
    return row


def renew(root, job_id, owner):
    with _db(root) as con, con:
        _owned(con, job_id, owner)
        con.execute('UPDATE jobs SET lease_until=?,updated_at=? WHERE job_id=?', (time.time()+300, time.time(), job_id))


def checkpoint(root, job_id, owner, stage, value):
    with _db(root) as con, con:
        row = _owned(con, job_id, owner)
        data = json.loads(row['checkpoints'])
        data[stage] = value
        con.execute('UPDATE jobs SET checkpoints=?,lease_until=?,updated_at=? WHERE job_id=?',
                    (_json(data), time.time()+300, time.time(), job_id))


def finish(root, job_id, owner, result, *, status='completed', error=None):
    if status not in ('completed', 'failed', 'waiting_input'):
        raise RuntimeError('invalid terminal job state')
    with _db(root) as con, con:
        _owned(con, job_id, owner)
        con.execute('UPDATE jobs SET status=?,result=?,error=?,owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=?',
                    (status, _json(result), error, time.time(), job_id))


def retry(root, job_id):
    with _db(root) as con, con:
        row = con.execute('SELECT status FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None or row['status'] not in ('failed', 'waiting_input'):
            raise RuntimeError('only failed/waiting_input jobs may be retried')
        con.execute("UPDATE jobs SET status='queued',error=NULL,updated_at=? WHERE job_id=?", (time.time(), job_id))
    return {'status': 'queued', 'job_id': job_id}


def queue_notice(root, issue_key, summary):
    allowed = ('items', 'shortfall', 'failures', 'action_required', 'outcome')
    if set(summary) - set(allowed):
        raise RuntimeError('notice accepts semantic changes, not timestamps or raw file hashes')
    body = dict(summary)
    for key in ('items', 'failures'):
        body[key] = sorted(set(body.get(key, [])))
    notice_id = _hash([issue_key, body])
    with _db(root) as con, con:
        con.execute("INSERT OR IGNORE INTO outbox VALUES(?,?,?,'pending',NULL,?,?)", (notice_id, issue_key, _json(body), time.time(), time.time()))
    return {'notice_id': notice_id, 'issue_key': issue_key}


def notices(root):
    if not (Path(root) / DB_PATH).exists():
        return []
    with _db(root, write=False) as con:
        return [{**dict(row), 'payload': json.loads(row['payload'])} for row in con.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY created_at")]


def ack_notice(root, notice_id, receipt):
    if not isinstance(receipt, str) or not receipt.strip():
        raise RuntimeError('delivery receipt or operator acknowledgement is required')
    with _db(root) as con, con:
        updated = con.execute("UPDATE outbox SET status='acknowledged',receipt=?,updated_at=? WHERE notice_id=?", (receipt, time.time(), notice_id))
        if not updated.rowcount:
            raise RuntimeError('unknown notice')


def status(root):
    if not (Path(root) / DB_PATH).exists():
        return {'jobs': [], 'requests': [], 'usage': {'prompt_tokens': 0, 'completion_tokens': 0}, 'pending_notices': []}
    with _db(root, write=False) as con:
        jobs = [_job(row) for row in con.execute('SELECT * FROM jobs ORDER BY created_at')]
        requests = [dict(row) for row in con.execute('SELECT request_id,budget_key,stage,model,status,usage,error FROM requests ORDER BY created_at')]
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='request_retries'").fetchone():
            requests += [dict(row) for row in con.execute('SELECT request_id,budget_key,stage,model,status,usage,error,reason FROM request_retries ORDER BY created_at')]
    usage = {'prompt_tokens': 0, 'completion_tokens': 0}
    for request in requests:
        request['usage'] = json.loads(request['usage']) if request['usage'] else {}
        for key in usage:
            count = request['usage'].get(key, 0)
            if type(count) is int and count >= 0:
                usage[key] += count
    return {'jobs': jobs, 'requests': requests, 'usage': usage, 'cost': 'unknown; configure provider prices separately', 'pending_notices': notices(root)}


class ModelClient:
    """Explicitly configured Chat Completions-compatible endpoint, no defaults.

    Receipts freeze successful JSON before application writes. A pending or
    transport-ambiguous request is NOT resubmitted automatically. No credentials
    or request headers are written into receipts or surfaced in errors.
    """
    def __init__(self, root, *, base_url=None, model=None, api_key=None, client=None,
                 env_file=None, reasoning_effort=None):
        self.root = Path(root)
        env_file = env_file if env_file is not None else os.getenv('DIGEST_MODEL_ENV_FILE')
        # A selected file is a complete configuration source: never combine its
        # provider endpoint with credentials inherited from another provider.
        config = _model_env(env_file) if env_file is not None else os.environ
        key_var = config.get('DIGEST_MODEL_API_KEY_VAR', 'DIGEST_MODEL_API_KEY')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key_var):
            raise ConfigurationRequired('DIGEST_MODEL_API_KEY_VAR must be an environment variable name')
        self.base_url = base_url if base_url is not None else config.get('DIGEST_MODEL_BASE_URL')
        self.model = model if model is not None else config.get('DIGEST_MODEL_NAME')
        self.api_key = api_key if api_key is not None else config.get(key_var)
        effort = reasoning_effort if reasoning_effort is not None else config.get('DIGEST_MODEL_REASONING_EFFORT')
        self.reasoning_effort = effort.strip() if isinstance(effort, str) else effort
        if self.reasoning_effort not in (None, '', 'low', 'medium', 'high', 'max', 'minimal', 'none', 'xhigh'):
            raise ConfigurationRequired('unsupported DIGEST_MODEL_REASONING_EFFORT')
        if not all((self.base_url, self.model, self.api_key)):
            raise ConfigurationRequired('configure DIGEST_MODEL_BASE_URL, DIGEST_MODEL_NAME and the selected API key')
        digest._url(self.base_url, 'model endpoint')
        self.client = client

    def request(self, *, stage, system, material, budget_key, max_requests=60, max_output_tokens=4096):
        if type(max_requests) is not int or not 1 <= max_requests <= 1000:
            raise RuntimeError('request budget must be an integer in 1..1000')
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 16384:
            raise RuntimeError('output token budget must be bounded')
        if len(system) + len(_json(material)) > 60000:
            raise RuntimeError('model input exceeds 60000 character budget')
        fingerprint = {'endpoint': self.base_url, 'model': self.model, 'stage': stage, 'system': system,
                       'material': material, 'max_tokens': max_output_tokens, 'format': 'json-object.v1'}
        if self.reasoning_effort:
            fingerprint['reasoning_effort'] = self.reasoning_effort
        key = _hash(fingerprint)
        with _db(self.root) as con, con:
            con.execute('BEGIN IMMEDIATE')
            previous = con.execute('SELECT * FROM requests WHERE request_id=?', (key,)).fetchone()
            if previous:
                if previous['status'] == 'succeeded':
                    return {'request_id': key, 'output': json.loads(previous['output']), 'usage': json.loads(previous['usage']), 'reused': True, 'completed_at': _completed_at(previous['updated_at'])}
                if previous['status'] in ('pending', 'uncertain'):
                    raise RequestUncertain('request outcome uncertain; inspect provider before explicitly resolving receipt ' + key)
                raise RuntimeError('previous model response failed validation; change the input/prompt after review: ' + key)
            count = con.execute('SELECT count(*) FROM requests WHERE budget_key=?', (budget_key,)).fetchone()[0]
            count += con.execute('SELECT count(*) FROM request_retries WHERE budget_key=?', (budget_key,)).fetchone()[0]
            if count >= max_requests:
                raise RuntimeError('model request budget exhausted')
            con.execute('INSERT INTO requests VALUES(?,?,?,?,?,NULL,NULL,NULL,?,?)',
                        (key, budget_key, stage, self.model, 'pending', time.time(), time.time()))
        payload = {'model': self.model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': _json(material)}],
                   'response_format': {'type': 'json_object'}, 'max_tokens': max_output_tokens}
        if self.reasoning_effort:
            payload['reasoning_effort'] = self.reasoning_effort
        client = self.client or httpx.Client(timeout=90, follow_redirects=False)
        try:
            response = client.post(self.base_url.rstrip('/') + '/chat/completions', json=payload,
                                   headers={'Authorization': 'Bearer ' + self.api_key})
            response.raise_for_status()
            data = response.json()
            usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
            raw = data['choices'][0]['message']['content']
            output = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            if not isinstance(output, dict):
                raise ValueError('response must be a JSON object')
        except httpx.TransportError as exc:
            self._save(key, 'uncertain', error=type(exc).__name__)
            raise RequestUncertain('transport interrupted; request outcome unknown: ' + key) from None
        except (ValueError, KeyError, IndexError, TypeError, httpx.HTTPStatusError) as exc:
            code = getattr(getattr(exc, 'response', None), 'status_code', None)
            self._save(key, 'failed', usage=locals().get('usage', {}), error=type(exc).__name__ + (':' + str(code) if code else ''))
            raise RuntimeError('model response failed HTTP/JSON validation: ' + key) from None
        finally:
            if self.client is None:
                client.close()
        finished = self._save(key, 'succeeded', output=output, usage=usage)
        return {'request_id': key, 'output': output, 'usage': usage, 'reused': False, 'completed_at': _completed_at(finished)}

    def _save(self, key, status, *, output=None, usage=None, error=None):
        finished = time.time()
        with _db(self.root) as con, con:
            con.execute('UPDATE requests SET status=?,output=?,usage=?,error=?,updated_at=? WHERE request_id=?',
                        (status, _json(output) if output is not None else None, _json(usage or {}), error, finished, key))
        return finished


def model_check(root, *, env_file=None):
    model = ModelClient(root, env_file=env_file)
    return {'endpoint': model.base_url, 'model': model.model,
            'reasoning_effort': model.reasoning_effort or None, 'key_configured': bool(model.api_key)}


def model_smoke(root, *, env_file=None):
    result = ModelClient(root, env_file=env_file).request(
        stage='model-smoke', system='Return exactly the JSON object {"ok":true}.',
        material={'ok': True}, budget_key='model-smoke', max_requests=1, max_output_tokens=1024)
    if set(result['output']) != {'ok'} or result['output']['ok'] is not True:
        raise RuntimeError('model smoke response must be exactly the JSON object {"ok":true}')
    return {'status': 'succeeded', **result}


def resolve_request(root, request_id, document):
    """Recover a provider-confirmed response, never silently rebill an unknown request."""
    if set(document) != {'provider_receipt', 'completed_at', 'output', 'usage'}:
        raise RuntimeError('resolution requires provider_receipt, completed_at, output, usage')
    when = digest._timestamp(document['completed_at'], 'completed_at')
    if when > digest._now() or not isinstance(document['provider_receipt'], str) or not document['provider_receipt'].strip():
        raise RuntimeError('resolution requires actual provider receipt and nonfuture completion')
    if not isinstance(document['output'], dict) or not isinstance(document['usage'], dict):
        raise RuntimeError('resolution output and usage must be objects')
    with _db(root) as con, con:
        row = con.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
        if not row or row['status'] not in ('pending', 'uncertain'):
            raise RuntimeError('only pending/uncertain receipts can be resolved')
        if when.timestamp() < row['created_at']:
            raise RuntimeError('provider completion predates request')
        con.execute('CREATE TABLE IF NOT EXISTS resolutions(request_id TEXT PRIMARY KEY, document TEXT NOT NULL, resolved_at REAL NOT NULL)')
        con.execute('INSERT INTO resolutions VALUES(?,?,?)', (request_id, _json(document), time.time()))
        con.execute("UPDATE requests SET status='succeeded',output=?,usage=?,error=NULL,updated_at=? WHERE request_id=?", (_json(document['output']), _json(document['usage']), when.timestamp(), request_id))
    return {'status': 'resolved', 'request_id': request_id}


def retry_request(root, request_id, reason):
    """Explicit retry of an HTTP rejection, retaining the prior attempt and budget."""
    if not isinstance(reason,str) or not reason.strip(): raise RuntimeError('retry requires an operator reason')
    with _db(root) as con, con:
        row=con.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone()
        if not row or row['status']!='failed' or row['error'] not in {'HTTPStatusError:'+str(code) for code in (429,500,502,503,504)}:
            raise RuntimeError('only known transient HTTP failures can be explicitly retried; unknown outcomes require provider resolution')
        con.execute('INSERT INTO request_retries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,*tuple(row),reason))
        con.execute('DELETE FROM requests WHERE request_id=?',(request_id,))
    return {'status':'retry_authorized','request_id':request_id}


@contextlib.contextmanager
def _keep_lease(root, job_id, owner):
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(60):
            try:
                renew(root, job_id, owner)
            except (RuntimeError, sqlite3.Error):
                return  # foreground ownership check remains authoritative
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def work_once(root, *, handlers=None, job_id=None):
    root = Path(root)
    owner = uuid.uuid4().hex
    job = claim(root, owner, job_id=job_id)
    if job is None:
        return {'status': 'idle'}
    try:
        if 'result' in job['checkpoints']:
            result = job['checkpoints']['result']
        else:
            if handlers is None:
                from . import digest_pipeline
                handlers = {'collect': digest_pipeline.collect, 'generate': digest_pipeline.generate,
                            'backfill_monthly': digest_pipeline.generate}
            with _keep_lease(root, job['job_id'], owner):
                result = handlers[job['action']](root, job, owner)
            checkpoint(root, job['job_id'], owner, 'result', result)
        finish(root, job['job_id'], owner, result)
        return {'status': 'completed', 'job_id': job['job_id'], 'result': result}
    except ConfigurationRequired as exc:
        finish(root, job['job_id'], owner, {}, status='waiting_input', error=str(exc))
        queue_notice(root, job['business_key'], {'failures': [str(exc)], 'action_required': True, 'outcome': 'waiting_input'})
        return {'status': 'waiting_input', 'job_id': job['job_id'], 'error': str(exc)}
    except Exception as exc:
        # Do not include response bodies, URLs containing provider query keys,
        # or arbitrary exception text in a user-facing provider failure.
        error = str(exc) if isinstance(exc, (RuntimeError, digest.DigestError)) else type(exc).__name__
        try:
            finish(root, job['job_id'], owner, {}, status='failed', error=error)
        except RuntimeError:
            return {'status': 'ownership_lost', 'job_id': job['job_id']}
        queue_notice(root, job['business_key'], {'failures': [error], 'action_required': True, 'outcome': 'failed'})
        return {'status': 'failed', 'job_id': job['job_id'], 'error': error}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('schedule', 'work', 'status', 'retry', 'notices', 'ack', 'resolve-request', 'retry-request', 'model-check', 'model-smoke'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--model-env-file', type=Path)
    parser.add_argument('--id')
    parser.add_argument('--receipt')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--reason')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    previous_env_file = os.environ.get('DIGEST_MODEL_ENV_FILE')
    if args.model_env_file is not None:
        os.environ['DIGEST_MODEL_ENV_FILE'] = str(args.model_env_file)
    try:
        if args.command == 'schedule': result = schedule(root)
        elif args.command == 'work': result = work_once(root, job_id=args.id)
        elif args.command == 'model-check': result = model_check(root)
        elif args.command == 'model-smoke': result = model_smoke(root)
        elif args.command == 'status': result = status(root)
        elif args.command == 'notices': result = notices(root)
        elif args.command == 'retry': result = retry(root, args.id)
        elif args.command == 'retry-request': result = retry_request(root,args.id,args.reason)
        elif args.command == 'resolve-request':
            if args.input is None: raise RuntimeError('--input is required')
            result = resolve_request(root, args.id, json.loads(args.input.read_text(encoding='utf-8')))
        else:
            ack_notice(root, args.id, args.receipt)
            result = {'status': 'acknowledged', 'notice_id': args.id}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get('status') in ('failed', 'waiting_input') else 0
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 1
    finally:
        if args.model_env_file is not None:
            if previous_env_file is None:
                os.environ.pop('DIGEST_MODEL_ENV_FILE', None)
            else:
                os.environ['DIGEST_MODEL_ENV_FILE'] = previous_env_file


if __name__ == '__main__':
    sys.exit(main())
