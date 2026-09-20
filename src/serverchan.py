from __future__ import annotations

import re
import requests
import hashlib
import time
from urllib.parse import urlsplit


def _endpoint(sendkey: str) -> str:
    if sendkey.startswith("sctp"):
        m = re.match(r"sctp(\d+)t", sendkey)
        if not m:
            raise ValueError("无效的 Server酱³ SendKey")
        uid = m.group(1)
        return f"https://{uid}.push.ft07.com/send/{sendkey}.send"
    return f"https://sctapi.ftqq.com/{sendkey}.send"


def send_message(sendkey: str, title: str, desp: str = "") -> dict:
    if not sendkey:
        raise ValueError("缺少 SERVERCHAN_SENDKEY")
    payload = {"title": title.replace("\n", " ").strip(), "desp": desp}
    # Never retry a possibly accepted POST automatically; its outcome may be ambiguous.
    # Requests exceptions include the URL (and therefore the secret), so redact at source.
    try:
        resp = requests.post(
            _endpoint(sendkey), json=payload,
            headers={"Content-Type": "application/json;charset=utf-8"}, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f'ServerChan transport failed ({type(exc).__name__}); session unchanged') from None
    if not isinstance(data, dict):
        raise RuntimeError('ServerChan returned invalid JSON; session unchanged')
    if data.get("code") != 0:
        raise RuntimeError('ServerChan rejected the message; session unchanged')
    # Do not expose push/read keys or arbitrary upstream response text in logs/meta.
    return {'code': 0}


def verify_public_image(url: str, expected_sha256: str, attempts: int = 5) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Image must have an anonymous HTTPS URL')
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=30, headers={'Cache-Control': 'no-cache'})
            if (response.status_code == 200
                    and response.content.startswith(b'\x89PNG\r\n\x1a\n')
                    and hashlib.sha256(response.content).hexdigest() == expected_sha256):
                return
        except requests.RequestException:
            pass
        if attempt + 1 < attempts:
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError('Public PNG unavailable or content hash mismatch; session unchanged')
