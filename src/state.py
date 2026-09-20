"""Strict state validation: malformed/unreadable state must never mean unsent."""
from datetime import date
from pathlib import Path
import os
import re

def validate_session(value: str) -> str:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Invalid persisted NYSE session')
    date.fromisoformat(value)
    return value

def read_sent_session(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    value = path.read_text(encoding='utf-8').strip()
    return validate_session(value) if value else None

def write_sent_session(path: str | Path, session: str) -> None:
    path = Path(path)
    validate_session(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(session + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
