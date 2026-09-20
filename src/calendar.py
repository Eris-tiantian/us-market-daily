"""NYSE session selection independent of the machine's local timezone."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import pandas as pd
import pandas_market_calendars as mcal

@lru_cache(maxsize=1)
def nyse_calendar():
    return mcal.get_calendar('NYSE')

def latest_completed_nyse_session(now_utc: datetime | None = None) -> str:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Session selection requires a timezone-aware timestamp')
    now = now.astimezone(timezone.utc)
    schedule = nyse_calendar().schedule(
        start_date=(now - timedelta(days=35)).date(), end_date=now.date())
    completed = schedule.loc[schedule['market_close'] < pd.Timestamp(now)]
    if completed.empty:
        raise RuntimeError('No completed NYSE session found')
    return completed.index[-1].strftime('%Y-%m-%d')
