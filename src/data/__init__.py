"""Unified, validated market data with per-ticker fallback and safe disk cache."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
from pathlib import Path
import time
import uuid

import numpy as np
import pandas as pd

from .base import DataError, normalize_bars
from .factset import FactSetProvider
from .ftshare import FTShareProvider
from .tradingview import TradingViewProvider
from .yahoo import YahooProvider

LOGGER = logging.getLogger(__name__)
SECTOR_ETFS = {"Technology": "XLK", "Communication Services": "XLC", "Consumer Cyclical": "XLY",
               "Financial Services": "XLF", "Industrials": "XLI", "Energy": "XLE",
               "Basic Materials": "XLB", "Healthcare": "XLV", "Consumer Defensive": "XLP",
               "Utilities": "XLU", "Real Estate": "XLRE"}


def _atomic_json(path: Path, payload: dict, compress=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    text = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    if compress:
        temporary.write_bytes(gzip.compress(text.encode("utf-8")))
    else:
        temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


class DataClient:
    def __init__(self, cfg: dict, providers=None):
        self.cfg = cfg
        self.settings = cfg.get("data", {})
        self.cache_dir = Path(self.settings.get("cache_dir", "cache"))
        self.min_rows = int(self.settings.get("min_rows", 80))
        if providers is None:
            classes = {p.name: p for p in [FactSetProvider, FTShareProvider, YahooProvider, TradingViewProvider]}
            order = list(self.settings.get("fallback_order", ["factset", "ftshare", "yahoo"]))
            chosen = self.settings.get("provider", "auto")
            if chosen != "auto":
                order = [chosen] + [p for p in order if p != chosen]
            if any(p not in classes for p in order):
                raise DataError("unknown_provider_configuration")
            self.providers = [classes[p](self.settings) for p in dict.fromkeys(order)]
        else:
            self.providers = providers
        self.diagnostics = {"providers": {}, "request_count": 0, "ticker_requests": 0,
                            "requested_tickers": [], "successful_tickers": [], "failed_tickers": [],
                            "ticker_providers": {}, "fallbacks": [], "errors": [], "cache_hits": 0,
                            "full_refreshes": 0, "crosschecks": [], "metadata_requests": 0}
        for provider in self.providers:
            try:
                status = provider.availability()
            except Exception:
                status = {"available": False, "reason": "availability_check_failed"}
            self.diagnostics["providers"][provider.name] = status
        # No credentials, tokens, session cookies or raw exception messages are persisted.
        _atomic_json(self.cache_dir / "provider_metadata.json", self.diagnostics["providers"])

    def _path(self, ticker: str) -> Path:
        return self.cache_dir / "bars" / (hashlib.sha256(ticker.encode()).hexdigest()[:24] + ".json.gz")

    def _read_cache(self, ticker: str):
        try:
            payload = json.loads(gzip.decompress(self._path(ticker).read_bytes()))
            if payload.get("schema") != 1 or payload.get("ticker") != ticker:
                return None
            if not all(key in payload for key in ("provider", "full_session", "attrs", "dates", "data", "columns")):
                return None
            pd.Timestamp(payload["full_session"])
            frame = pd.DataFrame(payload["data"], columns=payload["columns"], index=pd.to_datetime(payload["dates"]))
            frame.attrs = payload["attrs"]
            return frame, payload
        except (OSError, ValueError, KeyError, TypeError, EOFError):
            return None

    def _save(self, ticker, frame, provider, full_session):
        frame.attrs["provider"] = provider
        payload = {"schema": 1, "ticker": ticker, "provider": provider,
                   "full_session": full_session, "updated_at": datetime.now(timezone.utc).isoformat(),
                   "dates": frame.index.strftime("%Y-%m-%d").tolist(),
                   "columns": frame.columns.tolist(), "data": frame.to_numpy().tolist(), "attrs": frame.attrs}
        _atomic_json(self._path(ticker), payload, compress=True)

    def _fetch(self, provider, tickers, start, session, min_rows):
        result = {}
        pending = list(tickers)
        terminal_codes = {"insufficient_history", "nonfinite_ohlcv",
                          "invalid_price_or_volume", "invalid_ohlc_geometry",
                          "no_session_trading_volume"}
        retries = max(3 if provider.name == "yahoo" else 0, int(self.settings.get("retries", 3)))
        for attempt in range(retries + 1):
            self.diagnostics["request_count"] += 1
            self.diagnostics["ticker_requests"] += len(pending)
            try:
                raw = provider.fetch_daily(pending, start, session)
            except Exception as exc:
                raw = {}
                self._error(provider.name, pending, type(exc).__name__, attempt)
            terminal = set()
            errors = defaultdict(list)
            for ticker in pending:
                try:
                    frame = normalize_bars(raw.get(ticker), session, min_rows=min_rows)
                    if not ticker.startswith("^") and frame["Volume"].iloc[-1] <= 0:
                        raise DataError("no_session_trading_volume")
                    result[ticker] = frame
                except (DataError, ValueError, TypeError, AttributeError, KeyError) as exc:
                    # All DataError messages here are static validation codes.
                    kind = str(exc) if isinstance(exc, DataError) else type(exc).__name__
                    errors[kind].append(ticker)
                    if ticker in raw and kind in terminal_codes:
                        terminal.add(ticker)
            for kind, failed in errors.items():
                self._error(provider.name, failed, kind, attempt)
            pending = [t for t in pending if t not in result and t not in terminal]
            if not pending:
                break
            if attempt < retries:
                time.sleep(float(self.settings.get("backoff_seconds", 1)) * 2 ** attempt)
        if result:
            self.diagnostics["providers"][provider.name]["network_verified"] = True
        return result

    def _error(self, provider, tickers, kind, attempt):
        entry = {"provider": provider, "tickers": tickers, "error_type": kind, "retry": attempt}
        self.diagnostics["errors"].append(entry)
        LOGGER.warning("data provider=%s type=%s retry=%s count=%s", provider, kind, attempt, len(tickers))

    def get_daily_bars(self, tickers: list[str], session: str) -> dict[str, pd.DataFrame]:
        session = pd.Timestamp(session).strftime("%Y-%m-%d")
        tickers = list(dict.fromkeys(str(t).strip().upper() for t in tickers if str(t).strip()))
        if not tickers:
            return {}
        start = (pd.Timestamp(session) - pd.Timedelta(days=int(self.settings.get("history_days", 900)))).strftime("%Y-%m-%d")
        results, caches = {}, {}
        full_age = int(self.settings.get("full_refresh_days", 30))
        for ticker in tickers:
            cached = self._read_cache(ticker)
            if cached:
                frame, meta = cached
                if (pd.Timestamp(session) - pd.Timestamp(meta["full_session"])).days < full_age:
                    try:
                        results[ticker] = normalize_bars(frame, session, min_rows=self.min_rows)
                        self.diagnostics["cache_hits"] += 1
                        self.diagnostics["ticker_providers"][ticker] = meta["provider"]
                    except DataError:
                        caches[ticker] = cached
        pending = [t for t in tickers if t not in results]
        available = [p for p in self.providers if self.diagnostics["providers"][p.name]["available"]]
        for provider_index, provider in enumerate(available):
            if not pending:
                break
            groups = defaultdict(list)
            for ticker in pending:
                fetch_start = start
                if ticker in caches and caches[ticker][1]["provider"] == provider.name:
                    old = caches[ticker][0]
                    fetch_start = max(start, (old.index[-1] - pd.Timedelta(days=int(self.settings.get("refresh_overlap_days", 14)))).strftime("%Y-%m-%d"))
                groups[fetch_start].append(ticker)
            for fetch_start, group in groups.items():
                size = min(provider.max_batch_size, max(1, int(self.settings.get("batch_size", 64))))
                for offset in range(0, len(group), size):
                    batch = group[offset:offset + size]
                    fetched = self._fetch(provider, batch, fetch_start, session, 1 if fetch_start != start else self.min_rows)
                    for ticker, frame in fetched.items():
                        full_session = session
                        if fetch_start != start and ticker in caches:
                            old, meta = caches[ticker]
                            overlap = old.index.intersection(frame.index)
                            # Corporate actions revise old adjusted prices and split-adjusted volumes.
                            # Any overlapping revision triggers a full refresh, never a mixed-basis splice.
                            changed = not len(overlap) or not np.allclose(old.loc[overlap].to_numpy(), frame.loc[overlap].to_numpy(), rtol=1e-5, atol=1e-8)
                            if changed:
                                self.diagnostics["full_refreshes"] += 1
                                refreshed = self._fetch(provider, [ticker], start, session, self.min_rows)
                                if ticker not in refreshed:
                                    continue
                                frame = refreshed[ticker]
                            else:
                                attrs = frame.attrs.copy()
                                frame = pd.concat([old.loc[old.index < frame.index[0]], frame]).loc[start:]
                                frame.attrs = attrs
                                frame = normalize_bars(frame, session, min_rows=self.min_rows)
                                full_session = meta["full_session"]
                        results[ticker] = frame
                        self._save(ticker, frame, provider.name, full_session)
                        self.diagnostics["ticker_providers"][ticker] = provider.name
                        if provider_index:
                            self.diagnostics["fallbacks"].append({"ticker": ticker, "to": provider.name})
            pending = [t for t in pending if t not in results]
            LOGGER.info("data provider=%s success=%s remaining=%s", provider.name, len(results), len(pending))
        requested = set(self.diagnostics["requested_tickers"]) | set(tickers)
        successful = set(self.diagnostics["successful_tickers"]) | set(results)
        self.diagnostics.update(requested_tickers=sorted(requested), successful_tickers=sorted(successful),
                                failed_tickers=sorted(requested - successful))
        self.diagnostics["successful_count"] = len(successful)
        self.diagnostics["failed_count"] = len(requested - successful)
        if not results:
            raise DataError("all_providers_failed_no_fresh_valid_bars")
        if self.settings.get("crosscheck", True) and len(available) >= 2:
            self._crosscheck(results, session, available)
        _atomic_json(self.cache_dir / "provider_metadata.json", self.diagnostics["providers"])
        return results

    def _crosscheck(self, data, session, available):
        checked = {r["ticker"] for r in self.diagnostics["crosschecks"] if r["session"] == session}
        for ticker in ("SPY", "QQQ", "IWM"):
            if ticker not in data or ticker in checked:
                continue
            primary = self.diagnostics["ticker_providers"][ticker]
            others = [p for p in available if p.name != primary]
            if not others:
                continue
            start = (pd.Timestamp(session) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
            comparison = self._fetch(others[0], [ticker], start, session, 1)
            record = {"ticker": ticker, "session": session, "provider": others[0].name, "available": ticker in comparison}
            if ticker in comparison:
                deviation = abs(float(comparison[ticker]["Close"].iloc[-1] / data[ticker]["Close"].iloc[-1] - 1))
                record.update(deviation=deviation, warning=deviation > float(self.settings.get("max_price_deviation", .02)))
                if record["warning"]:
                    LOGGER.warning("crosscheck ticker=%s deviation=%.4f", ticker, deviation)
            self.diagnostics["crosschecks"].append(record)

    def get_quote(self, ticker: str, session: str) -> dict:
        frame = self.get_daily_bars([ticker], session)[ticker.upper()]
        return {"ticker": ticker.upper(), "date": frame.index[-1].strftime("%Y-%m-%d"),
                **{key: float(value) for key, value in frame.iloc[-1].items()},
                "provider": frame.attrs.get("provider"), "adjustment": frame.attrs.get("adjustment")}

    def get_market_snapshot(self, tickers: list[str], session: str) -> dict[str, dict]:
        return {ticker: {"date": frame.index[-1].strftime("%Y-%m-%d"),
                         **{key: float(value) for key, value in frame.iloc[-1].items()}}
                for ticker, frame in self.get_daily_bars(tickers, session).items()}

    def get_sector_etf(self, ticker: str) -> str | None:
        override = self.cfg.get("scanner", {}).get("sector_overrides", {}).get(ticker)
        if override:
            return override
        path = self.cache_dir / "sectors" / (hashlib.sha256(ticker.encode()).hexdigest()[:24] + ".json")
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - cached["timestamp"] < int(self.settings.get("sector_cache_days", 30)) * 86400:
                return cached.get("sector_etf")
        except (OSError, ValueError, KeyError):
            pass
        yahoo = next((p for p in self.providers if p.name == "yahoo" and hasattr(p, "get_sector")), None)
        if yahoo is None:
            return None
        self.diagnostics["metadata_requests"] += 1
        try:
            sector = yahoo.get_sector(ticker)
            etf = SECTOR_ETFS.get(sector)
        except Exception as exc:
            self._error("yahoo", [ticker], type(exc).__name__, 0)
            return None
        _atomic_json(path, {"timestamp": time.time(), "sector": sector, "sector_etf": etf})
        return etf


__all__ = ["DataClient", "DataError", "normalize_bars"]
