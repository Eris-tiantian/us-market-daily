"""Provider-independent market metrics and mechanical market regime."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .calendar import latest_completed_nyse_session
from .rs import pct_return, relative_strength
from .stage import StageResult, classify_stage, weekly_frame
from .state import read_sent_session


def volume_ratio(df: pd.DataFrame, lookback: int = 20) -> float:
    if "Volume" not in df:
        return np.nan
    v = df["Volume"].dropna()
    if len(v) < lookback + 1:
        return np.nan
    base = v.iloc[-lookback-1:-1].mean()
    return float(v.iloc[-1] / base) if np.isfinite(base) and base > 0 else np.nan


def stage_fields(result: StageResult, price: float) -> dict:
    return {"stage": result.stage, "stage_detail": result.stage_detail,
            "ma30": result.ma30, "ma30_slope": result.slope_per_week,
            "ma30_distance": price / result.ma30 - 1. if result.ma30 > 0 else np.nan,
            "range_pos_52w": result.range_pos_52w,
            "stage_started_at": result.stage_started_at,
            "weeks_in_stage": result.weeks_in_stage,
            "breakout_level": result.breakout_level,
            "distance_from_breakout": price / result.breakout_level - 1. if result.breakout_level > 0 else np.nan,
            "breakout_volume_ratio": result.breakout_volume_ratio}


def build_snapshot(data: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    benchmark = cfg.get("rs", {}).get("benchmark", cfg.get("report", {}).get("benchmark", "SPY"))
    bench = data.get(benchmark, pd.DataFrame()).get("Close", pd.Series(dtype=float))
    period = int(cfg.get("volume", {}).get("avg_period", 20))
    broad = set(cfg.get("market", ["SPY", "QQQ", "IWM"])) | set(cfg.get("sectors", {}))
    rows = []
    for ticker, df in data.items():
        if df.empty or "Close" not in df:
            continue
        close = df["Close"].dropna().sort_index()
        if close.empty:
            continue
        price = float(close.iloc[-1])
        volume = df.get("Volume", pd.Series(dtype=float))
        row = {"ticker": ticker, "price": price, "session": close.index[-1].date().isoformat(),
               "history_days": len(close),
               **{f"r{days}": pct_return(close, days) for days in (1, 5, 20, 60)},
               **{f"rs{days}": relative_strength(close, bench, days) for days in (20, 60, 120)},
               "vol_ratio": volume_ratio(df, period),
               "avg_volume20": float(volume.tail(period).mean()) if len(volume) >= period else np.nan,
               "avg_dollar_volume20": float((df["Close"] * volume).tail(period).mean()) if len(volume) >= period else np.nan}
        # Industry verification happens in scanner after the coarse stock gate.
        result = classify_stage(df, cfg=cfg, rs20=row["rs20"], rs60=row["rs60"],
                                industry_ok=ticker in broad)
        row.update(stage_fields(result, price))
        rows.append(row)
    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def market_regime(snapshot: pd.DataFrame, cfg: dict) -> tuple[str, float]:
    scores = []
    for ticker in cfg.get("market", ["SPY", "QQQ", "IWM"]):
        if ticker not in snapshot.index:
            continue
        row = snapshot.loc[ticker]
        score = {"Stage 2 Early": 1., "Stage 2": 1., "Stage 2?": .5,
                 "Stage 4": -1., "Stage 4?": -.5}.get(row["stage"], 0.)
        if np.isfinite(row["r20"]):
            score += .25 * np.sign(row["r20"])
        scores.append(score)
    if len(scores) != len(cfg.get("market", ["SPY", "QQQ", "IWM"])):
        return "数据不足", 0.
    average = float(np.mean(scores))
    label = "强势" if average >= .9 else "偏强" if average >= .35 else "中性" if average > -.35 else "偏弱" if average > -.9 else "防守"
    return label, average
