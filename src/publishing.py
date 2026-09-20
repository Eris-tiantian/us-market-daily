"""GitHub report assets with strict state reads and a bounded reachable history."""
from __future__ import annotations
import base64
from pathlib import Path
import re
import time
import requests
from .state import validate_session


class GitHubAssets:
    def __init__(self, repository: str, token: str, branch: str = 'report-assets'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid GITHUB_REPOSITORY')
        if not token:
            raise ValueError('Missing GitHub Actions token')
        self.repository, self.branch = repository, branch
        self._token = token
        self.head = None
        self.last_sent = None
        self.published_session = None

    def _api(self, method, path, payload=None, missing_ok=False):
        # Only retry reads. A timed-out mutation may have succeeded remotely.
        attempts = 3 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                response = requests.request(
                    method, f'https://api.github.com/repos/{self.repository}{path}',
                    json=payload, timeout=30,
                    headers={'Authorization': f'Bearer {self._token}',
                             'Accept': 'application/vnd.github+json',
                             'X-GitHub-Api-Version': '2022-11-28'},
                )
                if response.status_code == 404 and missing_ok:
                    return None
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError):
                if attempt + 1 == attempts:
                    raise RuntimeError(f'GitHub API {method} failed; remote state must be checked') from None
                time.sleep(2 ** attempt)

    def _head(self):
        ref = self._api('GET', f'/git/ref/heads/{self.branch}', missing_ok=True)
        return ref['object']['sha'] if ref else None

    def load_state(self):
        if self._api('GET', '').get('private') is not False:
            raise RuntimeError('Image publication requires a public repository')
        self.head = self._head()
        if not self.head:
            self.last_sent = None
            return None
        item = self._api('GET', f'/contents/sent_session.txt?ref={self.head}', missing_ok=True)
        if not item or item.get('encoding') != 'base64':
            raise RuntimeError('Existing report branch has no readable sent marker; refusing duplicate risk')
        try:
            value = base64.b64decode(item['content']).decode('utf-8').strip()
            self.last_sent = validate_session(value) if value else None
        except (ValueError, UnicodeError, KeyError):
            raise RuntimeError('Corrupt remote sent marker; refusing duplicate risk') from None
        return self.last_sent

    def _move(self, commit, force):
        if self._head() != self.head:
            raise RuntimeError('Report branch changed concurrently; refusing overwrite')
        if self.head:
            self._api('PATCH', f'/git/refs/heads/{self.branch}', {'sha': commit, 'force': force})
        else:
            self._api('POST', '/git/refs', {'ref': f'refs/heads/{self.branch}', 'sha': commit})
        # An acknowledged write is verified before any downstream side effect.
        actual = self._head()
        if actual != commit:
            raise RuntimeError('Remote report branch verification failed')
        self.head = commit

    def publish(self, png: str | Path, metadata: str | Path, session: str):
        validate_session(session)
        blob = self._api('POST', '/git/blobs', {
            'content': base64.b64encode(Path(png).read_bytes()).decode('ascii'), 'encoding': 'base64'})['sha']
        tree = self._api('POST', '/git/trees', {'tree': [
            {'path': 'latest.png', 'type': 'blob', 'mode': '100644', 'sha': blob},
            {'path': 'latest.json', 'type': 'blob', 'mode': '100644',
             'content': Path(metadata).read_text(encoding='utf-8')},
            {'path': 'sent_session.txt', 'type': 'blob', 'mode': '100644',
             'content': self.last_sent + '\n' if self.last_sent else ''},
        ]})['sha']
        # A fresh root keeps daily image history from growing forever.
        commit = self._api('POST', '/git/commits', {
            'message': f'Publish NYSE report {session}', 'tree': tree, 'parents': []})['sha']
        self._move(commit, force=True)
        self.published_session = session
        return f'https://raw.githubusercontent.com/{self.repository}/{commit}/latest.png'

    def mark_sent(self, session):
        validate_session(session)
        if session != self.published_session or not self.head:
            raise RuntimeError('Cannot mark a session without this run publishing its report')
        # This method is called exclusively after ServerChan code=0 by delivery.deliver.
        commit_info = self._api('GET', f'/git/commits/{self.head}')
        tree = self._api('POST', '/git/trees', {
            'base_tree': commit_info['tree']['sha'],
            'tree': [{'path': 'sent_session.txt', 'type': 'blob', 'mode': '100644',
                      'content': session + '\n'}],
        })['sha']
        commit = self._api('POST', '/git/commits', {
            'message': f'ServerChan accepted NYSE report {session}',
            'tree': tree, 'parents': [self.head]})['sha']
        self._move(commit, force=False)
        self.last_sent = session
