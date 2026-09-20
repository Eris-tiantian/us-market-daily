"""Returns and benchmark-relative returns measured on matching dates."""
from __future__ import annotations

import numpy as np
import pandas as pd


def pct_return(close: pd.Series, days: int) -> float:
    values = close.dropna().sort_index()
    if len(values) <= days or values.iloc[-1-days] <= 0:
        return np.nan
    return float(values.iloc[-1] / values.iloc[-1-days] - 1.)


def relative_strength(close: pd.Series, benchmark: pd.Series, days: int) -> float:
    """Use benchmark sessions for the horizon; never silently shift dates.

    Interior missing stock bars do not change either endpoint. Missing endpoint
    bars or a stale benchmark produce unavailable RS instead of stale strength.
    """
    stock, bench = close.dropna().sort_index(), benchmark.dropna().sort_index()
    if stock.empty or len(bench) <= days or stock.index[-1] != bench.index[-1]:
        return np.nan
    first, last = bench.index[-days-1], bench.index[-1]
    if first not in stock.index or last not in stock.index:
        return np.nan
    if stock.loc[first] <= 0 or bench.loc[first] <= 0:
        return np.nan
    return float(stock.loc[last] / stock.loc[first] - bench.loc[last] / bench.loc[first])
