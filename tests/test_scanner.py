from copy import deepcopy

import numpy as np
import pandas as pd


def fixture():
    dates = pd.bdate_range("2024-01-01", periods=300)
    close = np.r_[np.linspace(60., 114., 60), np.linspace(114., 90., 100),
                  np.linspace(90., 97., 125), [98, 97.5, 97, 97.5, 98, 99, 98.5, 98, 98.5, 99, 99, 99, 99, 99, 100]]
    df = pd.DataFrame({"Open": close, "High": close * 1.002,
                       "Low": close * .998, "Close": close, "Volume": 2e6}, index=dates)
    row = {"price": 100., "stage": "Stage 2", "r5": .01, "r20": .06,
           "rs20": .04, "rs60": .10, "rs120": .12, "ma30": 98.,
           "ma30_distance": .02, "ma30_slope": .002,
           "avg_dollar_volume20": 200e6, "avg_volume20": 2e6,
           "vol_ratio": 1.4, "history_days": 300, "session": str(dates[-1].date()),
           "breakout_level": 98., "weeks_in_stage": 8}
    snapshot = pd.DataFrame([{"ticker": "XYZ", **row}, {"ticker": "XLK", **row},
                             *[{"ticker": t, **row} for t in ("TEM", "RXRX", "ENPH")]]).set_index("ticker")
    cfg = {"watchlist": {"fixed": ["TEM", "RXRX", "ENPH"]},
           "scanner": {"enabled": True, "min_price": 5., "min_history_days": 252,
                       "min_avg_dollar_volume_20d": 20e6, "max_candidates": 5},
           "rr": {"minimum": 2.}, "volume": {"breakout_ratio": 1.3}}
    return {t: df.copy() for t in snapshot.index}, snapshot, cfg


def test_dynamic_candidate_enters_exits_and_fixed_stays_immutable():
    from src.scanner import scan_market
    data, snapshot, cfg = fixture()
    before = deepcopy(cfg)
    universe = [{"ticker": "XYZ", "sector_etf": "XLK", "exchange": "NASDAQ"}]
    candidates, stats = scan_market(data, snapshot, universe, cfg, "偏强")
    assert [c["ticker"] for c in candidates] == ["XYZ"]
    assert stats["final_count"] == 1
    assert cfg == before
    assert all(t in snapshot.index for t in cfg["watchlist"]["fixed"])
    assert "structure_status" in snapshot.columns
    snapshot.loc["XYZ", "rs20"] = -.02
    candidates, stats = scan_market(data, snapshot, universe, cfg, "偏强")
    assert candidates == []
    assert stats["rejection_reasons"]["rs"] == 1
    assert cfg == before


def test_unknown_industry_and_weak_market_fail_closed():
    from src.scanner import scan_market
    data, snapshot, cfg = fixture()
    unknown = [{"ticker": "XYZ", "sector_etf": None, "exchange": "NYSE"}]
    candidates, stats = scan_market(data, snapshot, unknown, cfg, "强势")
    assert candidates == []
    assert stats["rejection_reasons"]["unknown_sector"] == 1
    candidates, stats = scan_market(data, snapshot, [{**unknown[0], "sector_etf": "XLK"}], cfg, "防守")
    assert candidates == []
    assert stats["rejection_reasons"]["market_regime"] == 1


def test_fixed_ticker_cannot_become_new_dynamic_candidate():
    from src.scanner import scan_market
    data, snapshot, cfg = fixture()
    universe = [{"ticker": "TEM", "sector_etf": "XLK", "exchange": "NASDAQ"}]
    candidates, _ = scan_market(data, snapshot, universe, cfg, "强势")
    assert candidates == []
