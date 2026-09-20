"""Documented FactSet Global Prices v1, API-key Basic authentication.

Reference: github.com/FactSet/enterprise-sdk, Python FactSetGlobalPrices/v1.
No credential means no HTTP request. OAuth/browser sessions are not inferred.
"""
from __future__ import annotations
import importlib.util
import os

import pandas as pd
import requests

from .base import DataError


class FactSetProvider:
    name = "factset"
    max_batch_size = 50  # documented maximum for a multi-day request

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def availability(self) -> dict:
        present = bool(os.getenv("FACTSET_USERNAME") and os.getenv("FACTSET_API_KEY"))
        try:
            sdk = importlib.util.find_spec("fds.sdk.FactSetGlobalPrices") is not None
        except ModuleNotFoundError:
            sdk = False
        return {"available": present, "credentials_present": present, "sdk_installed": sdk,
                "transport": "documented_https_rest",
                "reason": "credentials_ready_entitlement_unverified" if present else "missing_credentials"}

    def fetch_daily(self, tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
        if not self.availability()["available"]:
            raise DataError("factset_credentials_unavailable")
        identifiers = {t: (t + "-US" if not t.startswith("^") else t) for t in tickers}
        response = requests.get("https://api.factset.com/content/factset-global-prices/v1/prices",
                                params={"ids": ",".join(identifiers.values()), "startDate": start,
                                        "endDate": end, "frequency": "AD", "calendar": "FIVEDAY",
                                        "currency": "USD", "adjust": "DIV_SPIN_SPLITS",
                                        "fields": "price,priceOpen,priceHigh,priceLow,volume"},
                                auth=(os.environ["FACTSET_USERNAME"], os.environ["FACTSET_API_KEY"]),
                                timeout=float(self.cfg.get("timeout_seconds", 30)))
        if response.status_code != 200:
            raise DataError("factset_http_" + str(response.status_code))
        rows = response.json().get("data", [])
        return self.parse_response(rows, identifiers)

    @staticmethod
    def parse_response(rows: list[dict], identifiers: dict[str, str]) -> dict[str, pd.DataFrame]:
        result = {}
        # requestId is the input identifier; fsymId is not interchangeable.
        for ticker, identifier in identifiers.items():
            selected = [r for r in rows if r.get("requestId") == identifier]
            if not selected:
                continue
            frame = pd.DataFrame(selected).rename(columns={"date": "Date", "price": "Close",
                    "priceOpen": "Open", "priceHigh": "High", "priceLow": "Low", "volume": "Volume"})
            frame = frame.set_index("Date")
            frame.attrs["adjustment"] = "FactSet DIV_SPIN_SPLITS; provider_reported_volume"
            result[ticker] = frame
        return result
