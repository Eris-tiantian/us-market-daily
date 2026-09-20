"""Provider contract: date-indexed, dividend/split adjusted OHLC in USD.

Volume remains the provider's reported shares (not dividend adjusted). Prices
are on today's adjustment basis and must not be used as point-in-time backtests.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd


class DataError(RuntimeError):
    """Safe, deliberately generic error; never include an HTTP response or key."""


class Provider(Protocol):
    name: str
    max_batch_size: int
    def availability(self) -> dict: ...
    def fetch_daily(self, tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]: ...


def normalize_bars(frame: pd.DataFrame, session: str, *, adjusted: bool = True,
                   min_rows: int = 1) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise DataError("empty_bars")
    result = frame.copy()
    result.columns = [str(c).title() for c in result.columns]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in result for c in required):
        raise DataError("missing_ohlcv")
    try:
        result.index = pd.to_datetime(result.index)
        if result.index.tz is not None:
            result.index = result.index.tz_localize(None)
        result.index = result.index.normalize()
    except (TypeError, ValueError):
        raise DataError("invalid_dates") from None
    if result.index.has_duplicates or result.index.isna().any():
        raise DataError("duplicate_or_invalid_dates")
    result = result.sort_index().loc[:pd.Timestamp(session)]
    result = result.dropna(subset=required, how="all")
    if len(result) < min_rows:
        raise DataError("insufficient_history")
    if result.empty or result.index[-1].strftime("%Y-%m-%d") != session:
        raise DataError("stale_session")
    for col in required + (["Adj Close"] if "Adj Close" in result else []):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    if not adjusted:
        if "Adj Close" not in result:
            raise DataError("missing_adjusted_close")
        ratio = result["Adj Close"] / result["Close"]
        result[["Open", "High", "Low", "Close"]] = result[["Open", "High", "Low", "Close"]].mul(ratio, axis=0)
    result["Adj Close"] = result["Close"]
    values = result[required + ["Adj Close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise DataError("nonfinite_ohlcv")
    if (result[["Open", "High", "Low", "Close"]] <= 0).any().any() or (result["Volume"] < 0).any():
        raise DataError("invalid_price_or_volume")
    tolerance = result["Close"].abs() * 1e-6
    if ((result["High"] + tolerance < result[["Open", "Close", "Low"]].max(axis=1)).any()
            or (result["Low"] - tolerance > result[["Open", "Close", "High"]].min(axis=1)).any()):
        raise DataError("invalid_ohlc_geometry")
    result = result[required + ["Adj Close"]]
    result.index.name = "Date"
    result.attrs.update(frame.attrs)
    result.attrs.setdefault("adjustment", "dividend_and_split_adjusted_ohlc; provider_reported_volume")
    return result
