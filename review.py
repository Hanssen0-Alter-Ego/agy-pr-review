#!/usr/bin/env python3
"""Direct operator/local-timer entry point. Never exposed by the MCP server."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from common import ReviewError, MAX_BUNDLE, allow_repo, bounded_run, config, pr_number

SAFE_HOST_ENV = ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG',
                 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH', 'XDG_RUNTIME_DIR')

def docker(args, *, fetch=False, input=None, timeout=240, limit=1_000_000):
    env = {k: os.environ[k] for k in SAFE_HOST_ENV if k in os.environ}
    if fetch and os.environ.get('GH_TOKEN'):
        env['GH_TOKEN'] = os.environ['GH_TOKEN']
    return bounded_run(['docker', 'compose', '-f', str(ROOT/'compose.yaml'), *args],
                       env=env, timeout=timeout, input=input, cwd=str(ROOT), limit=limit)

def fetch(mode, repo, pr):
    return json.loads(docker(['run', '--rm', '--no-deps', '-T', '-e', 'GH_TOKEN',
                             'fetcher', mode, repo, str(pr)], fetch=True))

def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(temp, 0o640)
    temp.replace(path)

def review(repo, pr):
    allow_repo(repo, config(ROOT/'config.json'))
    pr = pr_number(pr)
    state = ROOT/'state'
    state.mkdir(exist_ok=True, mode=0o750)
    with (state/'operator.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReviewError('Another local review is running') from None
        bundle = fetch('prepare', repo, pr)
        reports = state/'reports'
        reports.mkdir(exist_ok=True, mode=0o750)
        path = reports/(bundle['id']+'.json')
        if path.exists():
            print('Already reviewed: ' + bundle['id'])
            return
        raw = json.dumps(bundle, ensure_ascii=False).encode()
        if len(raw) > MAX_BUNDLE:
            raise ReviewError('Snapshot exceeds input limit')
        # Last freshness check immediately before paying for/model reviewing snapshot.
        current = fetch('refs', repo, pr)
        if (current['head'], current['base']) != (bundle['head'], bundle['base']):
            raise ReviewError('PR moved before review; retry')
        output = json.loads(docker(['exec', '-T', 'runner', 'python3', '/app/run_agy.py'],
                                   input=raw, timeout=660))
        try:
            current = fetch('refs', repo, pr)
            freshness = 'current-at-completion' if (current['head'], current['base']) == (bundle['head'], bundle['base']) else 'stale-at-completion'
        except ReviewError:
            freshness = 'unknown-at-completion'
        report = {k: bundle[k] for k in ('id', 'repo', 'pr', 'url', 'head', 'base', 'merge_base', 'scope')}
        report.update({'created_at': int(time.time()), 'freshness': freshness,
                       'body': output['response'], 'untrusted_model_output': True,
                       'executed_code': False, 'published_to_github': False})
        atomic_json(path, report)
        print(f"Saved report {bundle['id']} ({freshness})")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('review')
    p.add_argument('repository')
    p.add_argument('pr', type=int)
    args = parser.parse_args()
    try:
        review(args.repository, args.pr)
    except (ReviewError, ValueError, KeyError) as error:
        print(f'Review stopped: {error}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
