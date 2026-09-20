"""NYSE/Nasdaq/NYSE American security master, separate from the fixed watchlist.

Nasdaq Trader documents the public symbol directories and field definitions:
https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs
Liquidity/history screening belongs to scanner.py after OHLCV download.
"""
from __future__ import annotations
from io import StringIO
import json
import logging
from pathlib import Path
import re
import time

import pandas as pd
import requests

from .data import _atomic_json

LOGGER = logging.getLogger(__name__)
DIRECTORY_URLS = ["https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                  "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"]


def fetch_directory(url: str, timeout: float) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_directory(nasdaq_text: str, other_text: str, exchanges: list[str]) -> list[dict]:
    items = {}
    excluded = re.compile(r"\b(warrants?|rights?|units?|preferred|preference|debentures?|notes?|bonds?|fund|etf|etn|trust units?)\b", re.I)
    for content, source in ((nasdaq_text, "NASDAQ"), (other_text, "OTHER")):
        frame = pd.read_csv(StringIO(content), sep="|", dtype=str, keep_default_na=False)
        required = {"Security Name", "Test Issue", "ETF", "Symbol" if source == "NASDAQ" else "ACT Symbol"}
        if not required.issubset(frame.columns):
            raise RuntimeError("universe_directory_schema_changed")
        for row in frame.to_dict("records"):
            symbol = row.get("Symbol", row.get("ACT Symbol", ""))
            exchange = "NASDAQ" if source == "NASDAQ" else {"N": "NYSE", "A": "AMEX"}.get(row.get("Exchange"))
            name = row["Security Name"]
            if exchange not in exchanges or row["Test Issue"] != "N" or row["ETF"] != "N":
                continue
            if row.get("NextShares") == "Y" or row.get("Financial Status", "N") not in ("", "N"):
                continue
            if excluded.search(name) or not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", symbol):
                continue
            # Positive common/ordinary/depositary-share identification excludes
            # structured products with ambiguous names, while retaining ADRs.
            if not re.search(r"common|ordinary|depositary|shares of beneficial interest", name, re.I):
                continue
            ticker = symbol.replace(".", "-")
            items[ticker] = {"ticker": ticker, "exchange": exchange, "name": name, "sector_etf": None}
    return sorted(items.values(), key=lambda item: item["ticker"])


def load_universe(cfg: dict, client=None) -> tuple[list[dict], dict]:
    settings = cfg.get("scanner", {})
    if settings.get("universe_source", "nasdaq_directory") != "nasdaq_directory":
        raise RuntimeError("unsupported_universe_source")
    exchanges = settings.get("exchanges", ["NASDAQ", "NYSE", "AMEX"])
    root = Path(cfg.get("data", {}).get("cache_dir", "cache"))
    path = root / "universe.json"
    ttl = float(settings.get("universe_cache_hours", 24)) * 3600
    max_stale = float(settings.get("universe_max_stale_hours", 72)) * 3600
    cached = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("schema") != 1:
            cached = None
    except (OSError, ValueError):
        pass
    stats = {"source": "nasdaq_directory", "request_count": 0, "failures": [], "cache_hit": False,
             "stale_cache": False, "truncated": False, "sector_metadata": "shortlist_enrichment_via_data_client"}
    if cached and time.time() - cached["timestamp"] <= ttl:
        items = cached["items"]
        stats["cache_hit"] = True
    else:
        try:
            documents = []
            for url in DIRECTORY_URLS:
                text = None
                for attempt in range(4):
                    stats["request_count"] += 1
                    try:
                        text = fetch_directory(url, float(cfg.get("data", {}).get("timeout_seconds", 30)))
                        break
                    except Exception as exc:
                        stats["failures"].append({"source": url.rsplit("/", 1)[-1], "error_type": type(exc).__name__, "retry": attempt})
                        if attempt < 3:
                            time.sleep(2 ** attempt)
                if text is None:
                    raise RuntimeError("universe_directory_unavailable")
                documents.append(text)
            items = parse_directory(*documents, ["NASDAQ", "NYSE", "AMEX"])
            if not items:
                raise RuntimeError("empty_universe_directory")
            _atomic_json(path, {"schema": 1, "timestamp": time.time(), "items": items})
        except Exception as exc:
            if not cached or time.time() - cached["timestamp"] > max_stale:
                raise RuntimeError("universe_unavailable_no_fresh_cache") from None
            items = cached["items"]
            stats.update(cache_hit=True, stale_cache=True)
            LOGGER.warning("universe stale cache used error_type=%s", type(exc).__name__)
    items = [dict(item) for item in items if item["exchange"] in exchanges]
    for item in items:
        item["sector_etf"] = settings.get("sector_overrides", {}).get(item["ticker"], item.get("sector_etf"))
    stats["universe_count"] = len(items)
    stats["exchange_counts"] = {exchange: sum(i["exchange"] == exchange for i in items) for exchange in exchanges}
    limit = settings.get("max_universe")
    if limit is not None and len(items) > int(limit):
        if not settings.get("allow_partial_universe", False):
            raise RuntimeError("universe_truncation_requires_explicit_allow_partial_universe")
        items = items[:int(limit)]
        stats["truncated"] = True
    stats["selected_count"] = len(items)
    if not items:
        raise RuntimeError("universe_has_no_selected_exchanges")
    LOGGER.info("universe count=%s source=%s truncated=%s", len(items), stats["source"], stats["truncated"])
    return items, stats
