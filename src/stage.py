"""Mechanical Weinstein stages using completed NYSE weekly bars only."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal


@dataclass
class StageResult:
    stage: str
    ma30: float = np.nan
    ma30_distance: float = np.nan
    slope_per_week: float = np.nan
    range_pos_52w: float = np.nan
    stage_started_at: str | None = None
    weeks_in_stage: int = 0
    breakout_level: float = np.nan
    distance_from_breakout: float = np.nan
    breakout_volume_ratio: float = np.nan
    stage_detail: str = ""


@lru_cache(maxsize=16)
def _sessions(start_year: int, end_year: int) -> pd.DatetimeIndex:
    return mcal.get_calendar("NYSE").schedule(
        start_date=f"{start_year}-01-01", end_date=f"{end_year}-12-31"
    ).index


def weekly_frame(df: pd.DataFrame, ma_weeks: int = 30) -> pd.DataFrame:
    """Aggregate adjusted bars, excluding a week until its last NYSE session.

    Data must already stop at a completed daily session. A Thursday before Good
    Friday is complete; an ordinary Thursday is not. Missing terminal sessions
    also exclude that week, avoiding a stale close masquerading as Friday.
    """
    if df.empty or "Close" not in df:
        return pd.DataFrame(columns=["Close", "MA30"])
    daily = df.copy().sort_index()
    daily.index = pd.DatetimeIndex(daily.index).tz_localize(None).normalize()
    end = daily.index.max()
    sessions = _sessions(daily.index.min().year, end.year + 1)
    daily = daily.loc[daily.index.isin(sessions)]
    aggregation = {c: op for c, op in {"Open": "first", "High": "max", "Low": "min",
                   "Close": "last", "Volume": "sum"}.items() if c in daily}
    weekly = daily.resample("W-FRI").agg(aggregation).dropna(subset=["Close"])
    last_sessions = pd.Series(sessions, index=sessions).resample("W-FRI").last()
    expected = last_sessions.reindex(weekly.index)
    complete = (expected <= end) & expected.isin(daily.index)
    weekly = weekly.loc[complete].copy()
    weekly["MA30"] = weekly["Close"].rolling(ma_weeks).mean()
    return weekly


def _run(flags: pd.Series) -> int:
    count = 0
    for value in reversed(flags.fillna(False).tolist()):
        if not value:
            break
        count += 1
    return count


def classify_stage(
    df: pd.DataFrame,
    slope_threshold_per_week: float = .0015,
    flat_band: float = .05,
    early_breakout_lookback_weeks: int = 26,
    early_breakout_distance: float = .08,
    *,
    cfg: dict | None = None,
    rs20: float = np.nan,
    rs60: float = np.nan,
    industry_ok: bool | None = None,
) -> StageResult:
    """Classify structure; Early additionally requires confirmed base breakout.

    A rising MA immediately after a flat base can have a small four-week slope.
    Early therefore requires a positive MA turn, while established trends use
    the configured slope threshold. A new high alone never creates Early.
    """
    cfg = cfg or {}
    sc = cfg.get("stage", {})
    ma_weeks = int(sc.get("ma_weeks", 30))
    slope_weeks = int(sc.get("slope_weeks", 4))
    threshold = float(sc.get("slope_threshold", sc.get("slope_threshold_per_week", slope_threshold_per_week)))
    flat_band = float(sc.get("flat_band", flat_band))
    lookback = int(sc.get("breakout_lookback", sc.get("early_breakout_lookback_weeks", early_breakout_lookback_weeks)))
    max_early = int(sc.get("early_stage_max_weeks", 6))
    max_distance = float(sc.get("early_breakout_distance", early_breakout_distance))
    base_min = int(sc.get("base_min_weeks", 8))
    base_range = float(sc.get("base_max_range", .18))
    required_volume = float(cfg.get("volume", {}).get("breakout_ratio", 1.3))
    w = weekly_frame(df, ma_weeks)
    if len(w) < ma_weeks + slope_weeks:
        return StageResult("N/A", stage_detail="完整周线不足")
    close, ma = w["Close"], w["MA30"]
    slope = (ma / ma.shift(slope_weeks) - 1.) / slope_weeks
    distance = close / ma - 1.
    current_close, current_ma = float(close.iloc[-1]), float(ma.iloc[-1])
    current_slope, current_distance = float(slope.iloc[-1]), float(distance.iloc[-1])
    recent = close.tail(52)
    lo, hi = float(recent.min()), float(recent.max())
    range_pos = (current_close - lo) / (hi - lo) if hi > lo else .5
    bull = (close > ma) & ((slope > threshold) | ((slope > 0) & (distance > flat_band * .75)))
    bear = (close < ma) & (slope < -threshold)
    # Preserve the age through small above-MA consolidations within an uptrend.
    trend_run = _run((close >= ma) & (slope > 0))
    prior_bull = bull.iloc[-27:-1].rolling(6).sum().max() >= 6
    flat = abs(current_slope) <= threshold
    if bull.iloc[-1]:
        stage, age = "Stage 2", max(1, trend_run)
    elif bear.iloc[-1]:
        stage, age = "Stage 4", _run(bear)
    elif flat:
        stage = "Stage 3" if prior_bull and range_pos >= .62 else "Stage 1"
        age = _run(slope.abs() <= threshold)
    elif current_close >= current_ma:
        stage, age = "Stage 2?", max(1, trend_run)
    else:
        stage, age = "Stage 4?", 1

    breakout_level = breakout_ratio = np.nan
    breakout_index = None
    # Search the full current trend for its original launch. A later new high
    # cannot restart the clock because its preceding weeks are not a flat base.
    highs = w.get("High", close)
    volumes = w.get("Volume", pd.Series(np.nan, index=w.index))
    search_start = max(ma_weeks + slope_weeks, len(w) - max(trend_run, max_early) - base_min)
    for j in range(search_start, len(w)):
        base = w.iloc[max(0, j - lookback):j]
        base_slope = slope.iloc[j-base_min:j]
        base_distance = distance.iloc[j-base_min:j]
        if len(base) < base_min or base_slope.isna().any():
            continue
        level = float(highs.iloc[max(0, j-lookback):j].max())
        flat_base = (base_slope.abs() <= threshold).all() and (base_distance.abs() <= flat_band).all()
        compact_base = float(base["Close"].max() / base["Close"].min() - 1.) <= base_range
        crossed = float(close.iloc[j]) > level and float(close.iloc[j-1]) <= level
        turns_up = ma.iloc[j] > ma.iloc[j-1] and slope.iloc[j] > 0
        if flat_base and compact_base and crossed and turns_up:
            breakout_level, breakout_index = level, j
            preceding = volumes.iloc[max(0, j-20):j].mean()
            breakout_ratio = float(volumes.iloc[j] / preceding) if preceding > 0 else np.nan
            break

    breakout_distance = current_close / breakout_level - 1. if np.isfinite(breakout_level) else np.nan
    if breakout_index is not None:
        breakout_age = len(w) - breakout_index
        confirmed = (np.isfinite(rs20) and rs20 > 0 and np.isfinite(rs60) and rs60 > 0
                     and industry_ok is True and breakout_ratio >= required_volume)
        if (current_close > current_ma and current_slope > 0 and breakout_age <= max_early
                and 0 <= breakout_distance <= max_distance and confirmed):
            stage, age = "Stage 2 Early", breakout_age
        elif stage == "Stage 2":
            age = max(age, breakout_age)
    start = w.index[-max(1, age)].date().isoformat()
    return StageResult(stage, current_ma, current_distance, current_slope, range_pos,
                       start, max(1, age), breakout_level, breakout_distance, breakout_ratio,
                       "Stage 2 continuation" if stage == "Stage 2" else stage)
