"""Recompute dynamic candidates each run without modifying the fixed watchlist."""
from __future__ import annotations

from collections import Counter
import logging
from typing import Callable

import numpy as np
import pandas as pd

from .levels import find_levels
from .market import stage_fields
from .risk_reward import calculate_risk_reward
from .stage import classify_stage

LOG = logging.getLogger(__name__)
STAGE2 = {"Stage 2", "Stage 2 Early"}


def _number(row, name: str) -> float:
    try:
        return float(row.get(name, np.nan))
    except (ValueError, TypeError):
        return np.nan


def _sector_ok(snapshot: pd.DataFrame, sector: str | None) -> bool:
    if not sector or sector not in snapshot.index:
        return False
    row = snapshot.loc[sector]
    return bool(row.get("stage") in STAGE2 and _number(row, "rs20") > 0
                and _number(row, "rs60") > 0 and _number(row, "ma30_slope") > 0)


def _assign(snapshot: pd.DataFrame, ticker: str, fields: dict) -> None:
    for key, value in fields.items():
        if key not in snapshot:
            snapshot[key] = pd.Series(index=snapshot.index, dtype=object if isinstance(value, str) or value is None else float)
        snapshot.at[ticker, key] = value


def _enrich(ticker: str, data: dict, snapshot: pd.DataFrame, cfg: dict, sector: str | None) -> dict:
    row = snapshot.loc[ticker].to_dict()
    # Only the potential launch of a young trend needs industry confirmation.
    if (_number(row, "breakout_level") > 0
            and _number(row, "weeks_in_stage") <= cfg.get("stage", {}).get("early_stage_max_weeks", 6)):
        result = classify_stage(data[ticker], cfg=cfg, rs20=_number(row, "rs20"),
                                rs60=_number(row, "rs60"), industry_ok=_sector_ok(snapshot, sector))
        row.update(stage_fields(result, _number(row, "price")))
    levels = find_levels(data[ticker], _number(row, "ma30"), _number(row, "breakout_level"), cfg)
    row.update(levels)
    row.update(calculate_risk_reward(_number(row, "price"), levels, cfg))
    row["sector_etf"] = sector or "未知"
    row["sector_status"] = "行业强势" if _sector_ok(snapshot, sector) else "行业未确认" if not sector else "行业未通过"
    reason = _quality_rejection(row, snapshot, sector, cfg)
    if reason is None:
        row["structure_status"] = "符合"
    elif row.get("stage") in ("Stage 4", "Stage 4?") or _number(row, "rs20") < 0:
        row["structure_status"] = "不符合"
    elif row.get("stage") in STAGE2 and _number(row, "rs20") > 0:
        row["structure_status"] = "接近" if reason in {"volume", "position", "rr", "resistance"} else "等待"
    else:
        row["structure_status"] = "等待"
    row["structure_reason"] = reason or "结构、行业、位置和RR均通过"
    _assign(snapshot, ticker, row)
    return row


def _quality_rejection(row: dict, snapshot: pd.DataFrame, sector: str | None, cfg: dict) -> str | None:
    sc = cfg.get("scanner", {})
    if row.get("stage") not in STAGE2 or not (_number(row, "ma30_distance") > 0 and _number(row, "ma30_slope") > 0):
        return "stage"
    if not (_number(row, "rs20") > 0 and (not sc.get("require_rs60", True) or _number(row, "rs60") > 0)):
        return "rs"
    if not sector or sector not in snapshot.index:
        return "unknown_sector"
    if not _sector_ok(snapshot, sector):
        return "sector"
    if not (0 <= _number(row, "ma30_distance") <= float(sc.get("max_ma30_distance", .20))
            and _number(row, "r5") <= float(sc.get("max_r5", .12))
            and 0 <= _number(row, "support_distance") <= float(sc.get("max_support_distance", .08))):
        return "position"
    if _number(row, "resistance_distance") < float(sc.get("min_rr_space", .03)):
        return "resistance"
    volume = _number(row, "vol_ratio")
    if row.get("stage") == "Stage 2 Early":
        volume = max(volume, _number(row, "breakout_volume_ratio"))
        minimum_volume = float(cfg.get("volume", {}).get("breakout_ratio", 1.3))
    else:
        minimum_volume = float(sc.get("min_volume_ratio", .7))
    if not volume >= minimum_volume:
        return "volume"
    if not _number(row, "rr") >= float(cfg.get("rr", {}).get("minimum", 2.)):
        return "rr"
    return None


def scan_market(data: dict[str, pd.DataFrame], snapshot: pd.DataFrame,
                universe: list[dict], cfg: dict, regime: str,
                sector_resolver: Callable[[str], str | None] | None = None) -> tuple[list[dict], dict]:
    sc = cfg.get("scanner", {})
    fixed = tuple(cfg.get("watchlist", {}).get("fixed", ("TEM", "RXRX", "ENPH")))
    metadata = {item["ticker"]: item for item in universe}
    sector_cache = dict(sc.get("sector_overrides", {}))
    rejects: Counter = Counter()
    stats = {"universe_count": len(metadata),
             "downloaded_count": sum(t in data and not data[t].empty for t in metadata),
             "liquidity_pass": 0, "stage_rs_pass": 0, "final_count": 0}

    def resolve(ticker: str) -> str | None:
        sector = metadata.get(ticker, {}).get("sector_etf") or sector_cache.get(ticker)
        if not sector and sector_resolver is not None:
            try:
                sector = sector_resolver(ticker)
            except Exception as exc:
                LOG.warning("Sector resolution failed ticker=%s error=%s", ticker, type(exc).__name__)
            sector_cache[ticker] = sector
        return sector

    for ticker in fixed:
        if ticker in snapshot.index and ticker in data:
            _enrich(ticker, data, snapshot, cfg, resolve(ticker))
    candidates = []
    for ticker, item in metadata.items():
        if ticker in fixed:
            rejects["fixed_watchlist"] += 1
            continue
        if not sc.get("enabled", True):
            rejects["scanner_disabled"] += 1
            continue
        if ticker not in data or data[ticker].empty:
            rejects["missing_data"] += 1
            continue
        if item.get("exchange") not in sc.get("exchanges", ["NASDAQ", "NYSE", "AMEX"]):
            rejects["exchange"] += 1
            continue
        frame = data[ticker]
        close = pd.to_numeric(frame.get("Close"), errors="coerce")
        volume = pd.to_numeric(frame.get("Volume"), errors="coerce")
        price = float(close.iloc[-1]) if close is not None and len(close) else np.nan
        average_dollar = float((close * volume).tail(20).mean()) if volume is not None else np.nan
        if not (price > float(sc.get("min_price", 5.))
                and len(frame) >= int(sc.get("min_history_days", 252))
                and average_dollar >= float(sc.get("min_avg_dollar_volume_20d", 20e6))):
            rejects["liquidity"] += 1
            continue
        stats["liquidity_pass"] += 1
        if ticker not in snapshot.index:
            rejects["missing_metrics"] += 1
            continue
        row = snapshot.loc[ticker].to_dict()
        if regime not in sc.get("allowed_regimes", ["强势", "偏强", "中性"]):
            rejects["market_regime"] += 1
            continue
        if row.get("stage") not in STAGE2 or not (_number(row, "ma30_distance") > 0 and _number(row, "ma30_slope") > 0):
            rejects["stage"] += 1
            continue
        if not (_number(row, "rs20") > 0 and (not sc.get("require_rs60", True) or _number(row, "rs60") > 0)):
            rejects["rs"] += 1
            continue
        stats["stage_rs_pass"] += 1
        sector = resolve(ticker)
        row = _enrich(ticker, data, snapshot, cfg, sector)
        rejection = _quality_rejection(row, snapshot, sector, cfg)
        if rejection:
            rejects[rejection] += 1
            continue
        row["ticker"] = ticker
        row["reason"] = (f"{row['stage_detail'] if row.get('stage_detail') else row['stage']}；"
                         f"RS20 {row['rs20']:+.1%} / RS60 {row['rs60']:+.1%}；"
                         f"30WMA {row['ma30']:.2f} 向上；{sector} 行业通过；"
                         f"支撑 {row['support']:.2f} / 压力 {row['resistance']:.2f}；"
                         f"RR {row['rr']:.2f}；量比 {row['vol_ratio']:.2f}")
        candidates.append(row)
    # Rank only after all hard gates; zero candidates is a valid result.
    candidates.sort(key=lambda r: (r["stage"] == "Stage 2 Early", r["rr"], r["rs60"], r["rs20"]), reverse=True)
    maximum = max(0, min(5, int(sc.get("max_candidates", 5))))
    if len(candidates) > maximum:
        rejects["candidate_limit"] += len(candidates) - maximum
    candidates = candidates[:maximum]
    stats.update(final_count=len(candidates), rejection_reasons=dict(rejects))
    return candidates, stats
