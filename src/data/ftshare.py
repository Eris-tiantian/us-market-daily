"""Verified FTShare endpoint; no guessed API or browser-cookie transport.

Primary contract: FTShare-Lab/FTShare-skill, eastmoney-us-stock-daily-ohlc.
Only fqt=1 (forward-adjusted) records are accepted; mixed/unadjusted responses
fall through to the next provider. Date-free requests avoid its 3-day range cap.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os

import pandas as pd
import requests

from .base import DataError


class FTShareProvider:
    name = "ftshare"
    max_batch_size = 32

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def availability(self) -> dict:
        present = bool(os.getenv("FTSHARE_API_KEY"))
        return {"available": present, "credentials_present": present,
                "sdk_installed": importlib.util.find_spec("ftshare") is not None,
                "transport": "documented_https_rest",
                "reason": "credentials_ready_entitlement_unverified" if present else "missing_credentials"}

    @staticmethod
    def parse_response(rows: list[dict], start: str, end: str) -> pd.DataFrame:
        if not rows or any(str(r.get("fqt")) != "1" for r in rows):
            raise DataError("ftshare_unverified_adjustment")
        frame = pd.DataFrame(rows).rename(columns={"date": "Date", "open": "Open", "high": "High",
                     "low": "Low", "close": "Close", "volume": "Volume"})
        frame.index = pd.to_datetime(frame.pop("Date"))
        frame = frame.sort_index().loc[start:end]
        frame.attrs["adjustment"] = "FTShare Eastmoney fqt=1 forward_adjusted; provider_reported_volume"
        return frame

    def _one(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        rows, page = [], 1
        while True:
            response = requests.get("https://market.ft.tech/gateway/api/v1/market/data/eastmoney-us-stock-daily-ohlc",
                                    params={"stock_code": ticker, "page": page, "page_size": 1000},
                                    headers={"FTSHARE_API_KEY": os.environ["FTSHARE_API_KEY"],
                                             "Content-Type": "application/json"},
                                    timeout=float(self.cfg.get("timeout_seconds", 30)))
            if response.status_code != 200:
                raise DataError("ftshare_http_" + str(response.status_code))
            payload = response.json()
            if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
                raise DataError("ftshare_response_error")
            data = payload["data"]
            records = data.get("records", [])
            rows.extend(records)
            if page >= int(data.get("pages", 1)):
                break
            if not records or page >= 1000:
                raise DataError("ftshare_pagination_incomplete")
            page += 1
        return self.parse_response(rows, start, end)

    def fetch_daily(self, tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
        if not self.availability()["available"]:
            raise DataError("ftshare_credentials_unavailable")
        def fetch(ticker):
            try:
                return ticker, self._one(ticker, start, end)
            except Exception:
                return ticker, None
        with ThreadPoolExecutor(max_workers=min(4, int(self.cfg.get("max_workers", 8)))) as pool:
            return {t: frame for t, frame in pool.map(fetch, tickers) if frame is not None}
