from datetime import datetime
import pytest
from src.calendar import latest_completed_nyse_session

@pytest.mark.parametrize('now, expected', [
    ('2026-09-19T08:00:00+08:00', '2026-09-18'),
    ('2026-09-20T08:00:00+08:00', '2026-09-18'),
    ('2026-09-21T08:00:00+08:00', '2026-09-18'),
    ('2026-09-08T08:00:00+08:00', '2026-09-04'),
    ('2026-01-02T08:00:00+08:00', '2025-12-31'),
    ('2026-03-06T20:59:00+00:00', '2026-03-05'),
    ('2026-03-06T21:01:00+00:00', '2026-03-06'),
    ('2026-03-09T19:59:00+00:00', '2026-03-06'),
    ('2026-03-09T20:01:00+00:00', '2026-03-09'),
    ('2026-11-27T17:59:00+00:00', '2026-11-25'),
    ('2026-11-27T18:01:00+00:00', '2026-11-27'),
])
def test_completed_sessions(now, expected):
    assert latest_completed_nyse_session(datetime.fromisoformat(now)) == expected

def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        latest_completed_nyse_session(datetime(2026, 9, 18))
