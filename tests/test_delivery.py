import hashlib
from unittest.mock import Mock
import pytest
import requests
from src.delivery import deliver
from src.state import read_sent_session, write_sent_session
from src.serverchan import send_message, verify_public_image

def test_success_sends_before_state(tmp_path):
    state = tmp_path / 'sent.txt'
    write_sent_session(state, '2026-09-17')
    events = []
    def sender():
        assert read_sent_session(state) == '2026-09-17'
        events.append('send')
        return {'code': 0}
    deliver('2026-09-18', lambda: events.append('publish') or 'url',
            lambda u: events.append('verify'), sender,
            lambda s: (events.append('mark'), write_sent_session(state, s)))
    assert events == ['publish', 'verify', 'send', 'mark']
    assert read_sent_session(state) == '2026-09-18'

@pytest.mark.parametrize('fail_at', ['publish', 'verify', 'send'])
def test_any_failure_preserves_session(tmp_path, fail_at):
    state = tmp_path / 'sent.txt'
    write_sent_session(state, '2026-09-17')
    def operation(name):
        def call(*args):
            if name == fail_at:
                raise RuntimeError(name)
            return {'code': 0} if name == 'send' else 'url'
        return call
    with pytest.raises(RuntimeError):
        deliver('2026-09-18', operation('publish'), operation('verify'),
                operation('send'), lambda s: write_sent_session(state, s))
    assert read_sent_session(state) == '2026-09-17'

def test_nonzero_ack_does_not_mark():
    marker = Mock()
    with pytest.raises(RuntimeError):
        deliver('2026-09-18', lambda: 'url', lambda _: None,
                lambda: {'code': 1}, marker)
    marker.assert_not_called()

def test_invalid_state_fails_closed(tmp_path):
    path = tmp_path / 'sent.txt'
    path.write_text('corrupt')
    with pytest.raises(ValueError):
        read_sent_session(path)

def test_serverchan_errors_do_not_disclose_key(monkeypatch):
    secret = 'SCT_test_secret_no_real_key'
    def fail(*args, **kwargs):
        raise requests.ConnectionError('URL https://sctapi.ftqq.com/' + secret)
    monkeypatch.setattr(requests, 'post', fail)
    with pytest.raises(RuntimeError) as exc:
        send_message(secret, 'test', 'body')
    assert secret not in str(exc.value)
    assert exc.value.__suppress_context__

def test_public_image_matches_exact_published_bytes(monkeypatch):
    content = b'\x89PNG\r\n\x1a\n' + b'image'
    response = Mock(status_code=200, content=content)
    response.headers = {'Content-Type': 'image/png'}
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: response)
    verify_public_image('https://raw.githubusercontent.com/test/repo/abc/latest.png',
                        hashlib.sha256(content).hexdigest(), attempts=1)
    with pytest.raises(RuntimeError):
        verify_public_image('https://raw.githubusercontent.com/test/repo/abc/latest.png',
                            hashlib.sha256(b'old').hexdigest(), attempts=1)
