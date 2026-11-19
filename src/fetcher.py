"""Read-only GitHub fetcher. No git checkout, hooks, shell, archives, or code execution."""
import base64
import difflib
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from common import ReviewError, MAX_BUNDLE, allow_repo, bounded_run, clean_env, config, pr_number, sha

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ReviewError('GitHub redirect refused')

class GitHub:
    def __init__(self, repo, token=None):
        self.repo, self.token = repo, token
        self.deadline = time.monotonic() + 180
    def get(self, endpoint):
        if time.monotonic() > self.deadline:
            raise ReviewError('Fetch deadline exceeded')
        path = f'/repos/{self.repo}/{endpoint}'
        # Paths are entirely constructed by this program, never taken from API URLs.
        if self.token:
            with tempfile.TemporaryDirectory() as home:
                raw = bounded_run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', path],
                    env=clean_env({'HOME': home, 'GH_CONFIG_DIR': home, 'GH_TOKEN': self.token,
                                   'GH_PROMPT_DISABLED': '1', 'GH_PAGER': 'cat'}), timeout=30, limit=20_000_000)
        else:
            req = urllib.request.Request('https://api.github.com' + path, headers={
                'Accept': 'application/vnd.github+json', 'User-Agent': 'agy-pr-review',
                'X-GitHub-Api-Version': '2022-11-28'})
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=30) as response:
                    raw = response.read(20_000_001)
            except Exception:
                raise ReviewError('GitHub read failed; check rate limit or repository access') from None
            if len(raw) > 20_000_000:
                raise ReviewError('GitHub response too large')
        return json.loads(raw)

def refs(meta, repo, pr):
    if meta.get('state') != 'open':
        raise ReviewError('PR is no longer open')
    if meta.get('number') != pr or meta.get('base', {}).get('repo', {}).get('full_name', '').lower() != repo.lower():
        raise ReviewError('GitHub PR identity mismatch')
    return (sha(meta['head']['sha']), sha(meta['base']['sha']))

def tree(api, commit):
    data = api.get(f'git/trees/{commit}?recursive=1')
    if data.get('truncated'):
        raise ReviewError('Tree is truncated; cannot establish complete snapshot')
    return {e['path']: e for e in data['tree']}

def blob(api, entry):
    if entry is None:
        return None
    if entry.get('type') != 'blob' or entry.get('mode') not in ('100644', '100755'):
        raise ReviewError('Symlink/submodule changes require manual review')
    if entry.get('size', MAX_BUNDLE+1) > MAX_BUNDLE:
        raise ReviewError('File exceeds bounded review size')
    data = api.get('git/blobs/' + sha(entry['sha']))
    if data.get('encoding') != 'base64':
        raise ReviewError('Unsupported blob encoding')
    raw = base64.b64decode(data['content'])
    if len(raw) > MAX_BUNDLE or b'\0' in raw:
        raise ReviewError('Binary/oversized file requires manual review')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise ReviewError('Non-UTF8 file requires manual review') from None

def prepare(api, repo, pr):
    before = api.get(f'pulls/{pr}')
    head, base = refs(before, repo, pr)
    if before.get('state') != 'open':
        raise ReviewError('Only open pull requests are reviewed')
    if not 1 <= before.get('changed_files', 0) <= 40:
        raise ReviewError('PR must change 1–40 files')
    comparison = api.get(f'compare/{base}...{head}')
    merge = sha(comparison['merge_base_commit']['sha'])
    # Collect only after verifying the initial head/base are still current.
    if refs(api.get(f'pulls/{pr}'), repo, pr) != (head, base):
        raise ReviewError('PR moved before collection; retry with current refs')
    changes = comparison.get('files', [])
    if len(changes) != before['changed_files']:
        raise ReviewError('Incomplete or changed PR diff')
    heads, olds = tree(api, head), tree(api, merge)
    files = []
    for item in changes:
        path = item['filename']
        old_path = item.get('previous_filename', path)
        old, new = blob(api, olds.get(old_path)), blob(api, heads.get(path))
        if old is None and new is None:
            raise ReviewError('Changed file missing from both snapshots')
        files.append({'path': path, 'old_path': old_path, 'status': item['status'],
                      'before': old, 'after': new,
                      'diff': ''.join(difflib.unified_diff((old or '').splitlines(True),
                                (new or '').splitlines(True), fromfile=old_path, tofile=path))})
        if len(json.dumps(files, ensure_ascii=False).encode()) > MAX_BUNDLE - 2000:
            raise ReviewError('Snapshot exceeds 90 KB; split PR or review manually (not truncated)')
    if refs(api.get(f'pulls/{pr}'), repo, pr) != (head, base):
        raise ReviewError('PR moved while collecting; discard snapshot')
    data = {'repo': repo, 'pr': pr, 'head': head, 'base': base, 'merge_base': merge,
            'url': f'https://github.com/{repo}/pull/{pr}', 'files': files,
            'scope': 'Complete text of changed regular UTF-8 files only; no dependency context, execution, or tests.'}
    key = hashlib.sha256(f'{repo}:{pr}:{head}:{base}:{merge}:v1'.encode()).hexdigest()
    data['id'] = key
    return data

def main():
    cfg = config('/config/config.json')
    mode, repo, number = sys.argv[1:4]
    allow_repo(repo, cfg)
    pr = pr_number(number)
    api = GitHub(repo, os.environ.get('GH_TOKEN'))
    if mode == 'prepare':
        data = prepare(api, repo, pr)
    elif mode == 'refs':
        meta = api.get(f'pulls/{pr}')
        head, base = refs(meta, repo, pr)
        data = {'head': head, 'base': base, 'state': meta.get('state')}
    else:
        raise ReviewError('Unsupported fetcher operation')
    print(json.dumps(data, ensure_ascii=False))

if __name__ == '__main__':
    try:
        main()
    except (ReviewError, KeyError, ValueError) as e:
        print(f'Fetcher stopped: {e}', file=sys.stderr)
        sys.exit(1)
