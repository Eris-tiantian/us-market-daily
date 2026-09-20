"""Repeatable levels from confirmed pivots, the weekly MA and observed highs."""
from __future__ import annotations

import numpy as np
import pandas as pd


def find_levels(df: pd.DataFrame, ma30: float = np.nan, breakout_level: float = np.nan,
                cfg: dict | None = None) -> dict:
    lc = (cfg or {}).get("levels", {})
    span = int(lc.get("pivot_window", 5))
    history = df.tail(int(lc.get("lookback_days", 252))).copy()
    if history.empty:
        return {"support": np.nan, "resistance": np.nan, "support_source": "数据不足", "resistance_source": "数据不足"}
    price = float(history.Close.iloc[-1])
    high, low = history.get("High", history.Close), history.get("Low", history.Close)
    pivots_high = (high == high.rolling(span*2+1, center=True).max())
    pivots_low = (low == low.rolling(span*2+1, center=True).min())
    # A flat run is one level; require an actual swing on at least one side.
    pivots_high &= high > high.rolling(span*2+1, center=True).min()
    pivots_low &= low < low.rolling(span*2+1, center=True).max()
    supports = [(float(value), "近期 pivot low") for value in low[pivots_low]]
    resistance = [(float(value), "近期 pivot high") for value in high[pivots_high]]
    supports += [(float(ma30), "30WMA"), (float(breakout_level), "平台突破位")]
    historical_high = high.iloc[:-1].max() if len(high) > 1 else np.nan
    resistance.append((float(historical_high), "52周已观察高点"))
    supports = [(v, s) for v, s in supports if np.isfinite(v) and 0 < v < price]
    resistance = [(v, s) for v, s in resistance if np.isfinite(v) and v > price]
    support = max(supports, default=(np.nan, "无可靠支撑"), key=lambda x: x[0])
    ceiling = min(resistance, default=(np.nan, "无已知上方压力"), key=lambda x: x[0])
    return {"support": support[0], "support_source": support[1],
            "resistance": ceiling[0], "resistance_source": ceiling[1],
            "support_distance": price / support[0] - 1. if np.isfinite(support[0]) else np.nan,
            "resistance_distance": ceiling[0] / price - 1. if np.isfinite(ceiling[0]) else np.nan}
