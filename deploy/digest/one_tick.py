"""Reviewable single-executor schedule/work tick; dry-run unless --execute."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='private digest state root')
    parser.add_argument('--max-jobs', type=int, default=3)
    parser.add_argument('--model-env-file', type=Path, help='explicit private model configuration file')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.max_jobs <= 8:
        parser.error('--max-jobs must be 1 to 8')
    code_root = Path(__file__).resolve().parents[2]
    state_root = args.root.resolve()
    prefix = [sys.executable, '-X', 'utf8', '-m', 'ai_notes.digest_runtime']
    def command(name, job_id=None):
        return [*prefix, name, '--root', str(state_root), *(['--id',job_id] if job_id else []),
                *(['--model-env-file', str(args.model_env_file.resolve())] if args.model_env_file else [])]
    if not args.execute:
        print(json.dumps({'status': 'preview_only', 'cwd': str(code_root),
                          'schedule': command('schedule'), 'work': command('work'),
                          'max_jobs': args.max_jobs, 'notifications': 'outbox only; no send adapter'}, ensure_ascii=False, indent=2))
        return 0
    from ai_notes.digest_sources import SourceError, _run_lock
    def run(name, job_id=None):
        process = subprocess.run(command(name,job_id), cwd=code_root, text=True, encoding='utf-8', capture_output=True)
        if process.stdout:
            print(process.stdout, end='')
        if process.stderr:
            print(process.stderr, end='', file=sys.stderr)
        if process.returncode and name!='work':
            raise RuntimeError(f'digest_runtime {name} exited {process.returncode}')
        return json.loads(process.stdout)
    try:
        with _run_lock(state_root / 'data/weekly_digest/executor'):
            scheduled = run('schedule')
            if not scheduled.get('dispatch', {}).get('actions'):
                return 0  # Off-slot means no work, including previously queued jobs.
            failed=False
            for job in scheduled['jobs'][:args.max_jobs]:
                result = run('work',job['job_id'])
                if result.get('status') in ('failed', 'waiting_input'):
                    failed=True
        return 1 if failed else 0
    except (SourceError, RuntimeError, ValueError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
