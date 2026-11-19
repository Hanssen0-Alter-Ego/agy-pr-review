"""Small, dependency-free validation and bounded subprocess primitives."""
import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

REPO_RE = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}\Z')
SHA_RE = re.compile(r'[0-9a-f]{40}\Z')
MAX_BUNDLE = 90_000
MAX_OUTPUT = 1_000_000

class ReviewError(Exception):
    pass

def repo_name(value):
    if not isinstance(value, str) or not REPO_RE.fullmatch(value) or value.split('/')[1] in ('.', '..'):
        raise ReviewError('Invalid GitHub owner/repository')
    return value

def sha(value):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ReviewError('Expected full 40-character commit SHA')
    return value

def pr_number(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 2**31-1:
        raise ReviewError('Invalid PR number')
    return int(value)

def config(path):
    data = json.loads(Path(path).read_text())
    repos = data.get('repositories')
    if not isinstance(repos, list) or not repos or len(repos) > 100:
        raise ReviewError('Configure a non-empty exact repository allowlist')
    for name in repos:
        repo_name(name)
    return data

def allow_repo(name, cfg):
    repo_name(name)
    if name not in cfg['repositories']:
        raise ReviewError('Repository is not exactly allowlisted')

def clean_env(extra=None):
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    env.update(extra or {})
    return env

def bounded_run(args, *, env, timeout=60, input=None, limit=MAX_OUTPUT, cwd=None):
    """No shell. Kill process group on timeout; output limited on disk and in RAM."""
    import resource
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p = subprocess.Popen(args, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=out, stderr=err, env=env, cwd=cwd, start_new_session=True, preexec_fn=limits)
        try:
            p.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            raise ReviewError('Process timed out') from None
        if p.returncode != 0:
            # Never relay subprocess diagnostics: these may contain credentials or source.
            raise ReviewError(f'Process failed (exit {p.returncode})')
        out.seek(0)
        raw = out.read(limit + 1)
        if len(raw) >= limit:
            raise ReviewError('Process output limit exceeded')
        return raw
