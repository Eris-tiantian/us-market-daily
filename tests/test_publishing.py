from unittest.mock import Mock
import pytest
from src.publishing import GitHubAssets

class MemoryGitHub:
    def __init__(self):
        self.head = 'old'
        self.calls = []
        self.n = 0
    def __call__(self, method, path, payload=None, missing_ok=False):
        self.calls.append((method, path, payload))
        if path == '':
            return {'private': False}
        if method == 'GET' and path.startswith('/git/ref/'):
            return {'object': {'sha': self.head}}
        if method == 'GET' and path.startswith('/git/commits/'):
            return {'tree': {'sha': 'current-tree'}}
        if method == 'POST':
            self.n += 1
            return {'sha': f'sha{self.n}'}
        if method == 'PATCH':
            self.head = payload['sha']
            return {'object': {'sha': self.head}}

def store():
    result = GitHubAssets('owner/repo', 'test-token')
    result._api = MemoryGitHub()
    result.head = 'old'
    result.last_sent = '2026-09-17'
    return result

def test_publish_keeps_previous_marker_until_send(tmp_path):
    assets = store()
    png = tmp_path / 'report.png'
    meta = tmp_path / 'report.json'
    png.write_bytes(b'png')
    meta.write_text('{}')
    url = assets.publish(png, meta, '2026-09-18')
    trees = [p for m, path, p in assets._api.calls if path == '/git/trees']
    marker = next(x for x in trees[0]['tree'] if x['path'] == 'sent_session.txt')
    assert marker['content'] == '2026-09-17\n'
    assert '/sha' in url and url.endswith('/latest.png')
    assert assets.last_sent == '2026-09-17'
    assets.mark_sent('2026-09-18')
    assert assets.last_sent == '2026-09-18'

def test_private_repo_fails_before_publish():
    assets = store()
    assets._api = lambda *a, **k: {'private': True}
    with pytest.raises(RuntimeError, match='public'):
        assets.load_state()

def test_missing_marker_on_existing_branch_fails_closed():
    assets = store()
    def api(method, path, payload=None, missing_ok=False):
        if path == '': return {'private': False}
        if path.startswith('/git/ref/'): return {'object': {'sha': 'old'}}
        return None
    assets._api = api
    with pytest.raises(RuntimeError, match='marker'):
        assets.load_state()

def test_concurrent_branch_change_refuses_overwrite(tmp_path):
    assets = store()
    assets._api.head = 'changed'
    png = tmp_path / 'a.png'
    png.write_bytes(b'png')
    meta = tmp_path / 'a.json'
    meta.write_text('{}')
    with pytest.raises(RuntimeError, match='changed'):
        assets.publish(png, meta, '2026-09-18')
    assert not any(m == 'PATCH' for m, _, _ in assets._api.calls)
