from __future__ import annotations

import importlib.util
import logging

import pandas as pd

from .base import normalize_bars


class YahooProvider:
    name = "yahoo"
    max_batch_size = 128

    def __init__(self, cfg: dict):
        self.cfg = cfg
        # yfinance otherwise emits response/error bodies. Our client logs only
        # provider, ticker, retry number and exception class, never those bodies.
        logging.getLogger("yfinance").setLevel(logging.CRITICAL + 1)

    def availability(self) -> dict:
        installed = importlib.util.find_spec("yfinance") is not None
        return {"available": installed, "sdk_installed": installed,
                "reason": "sdk_ready_network_unverified" if installed else "missing_yfinance"}

    def fetch_daily(self, tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
        import yfinance as yf
        raw = yf.download(tickers=tickers, start=start,
                          end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                          interval="1d", auto_adjust=True, actions=False,
                          group_by="column", threads=int(self.cfg.get("max_workers", 8)),
                          progress=False, timeout=float(self.cfg.get("timeout_seconds", 30)),
                          ignore_tz=True)
        result = {}
        if raw is None or raw.empty:
            return result
        for ticker in tickers:
            if isinstance(raw.columns, pd.MultiIndex):
                levels = [i for i in range(raw.columns.nlevels)
                          if ticker in raw.columns.get_level_values(i)]
                if not levels:
                    continue
                frame = raw.xs(ticker, level=levels[0], axis=1).dropna(how="all")
            elif len(tickers) == 1:
                frame = raw.copy()
            else:
                continue
            result[ticker] = frame
        return result

    def get_sector(self, ticker: str) -> str | None:
        import yfinance as yf
        return yf.Ticker(ticker).get_info().get("sector")
