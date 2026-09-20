import numpy as np
import pandas as pd
import pytest

from src.market import build_snapshot, volume_ratio


def bars(close, index):
    return pd.DataFrame({"Open": close, "High": np.asarray(close) * 1.01,
                         "Low": np.asarray(close) * .99, "Close": close, "Volume": 1e6}, index=index)


def test_relative_strength_aligns_same_sessions_and_rejects_stale_benchmark():
    dates = pd.bdate_range("2024-01-01", periods=300)
    spy = bars(np.linspace(50., 100., 300), dates)
    stock = bars(np.linspace(50., 110., 300), dates).drop(dates[-10])
    snap = build_snapshot({"SPY": spy, "XYZ": stock}, {})
    expected = 110 / stock.Close.loc[dates[-21]] - 100 / spy.Close.loc[dates[-21]]
    assert snap.loc["XYZ", "rs20"] == pytest.approx(expected)
    stale = build_snapshot({"SPY": spy.iloc[:-1], "XYZ": stock}, {})
    assert np.isnan(stale.loc["XYZ", "rs20"])


def test_snapshot_has_full_returns_volume_and_stage_metadata():
    dates = pd.bdate_range("2024-01-01", periods=300)
    df = bars(np.linspace(50., 100., 300), dates)
    snap = build_snapshot({"SPY": df}, {})
    for field in ["r60", "rs60", "rs120", "avg_volume20", "avg_dollar_volume20",
                  "stage_started_at", "weeks_in_stage", "breakout_level", "distance_from_breakout"]:
        assert field in snap.columns
    assert snap.loc["SPY", "avg_volume20"] == 1e6
    assert snap.loc["SPY", "rs120"] == pytest.approx(0.)


def test_volume_ratio_uses_previous_twenty_sessions():
    assert volume_ratio(pd.DataFrame({"Volume": [10.] * 20 + [30.]})) == 3.


def test_rr_never_invents_target_above_all_time_high():
    from src.levels import find_levels
    from src.risk_reward import calculate_risk_reward
    dates = pd.bdate_range("2024-01-01", periods=300)
    df = bars(np.linspace(50., 100., 300), dates)
    df.iloc[-1, df.columns.get_loc("Close")] = 105.
    levels = find_levels(df, ma30=90., breakout_level=95.)
    rr = calculate_risk_reward(105., levels, {})
    assert np.isnan(rr["rr"])
    assert np.isnan(rr["target"])
    assert rr["rr_status"] == "RR 无法可靠计算"


def test_rr_uses_observed_resistance_and_invalidation():
    from src.risk_reward import calculate_risk_reward
    rr = calculate_risk_reward(100., {"support": 98., "resistance": 110.}, {"rr": {"stop_buffer": .01}})
    assert rr["target"] == 110.
    assert rr["invalidation"] == pytest.approx(97.02)
    assert rr["rr"] == pytest.approx(10 / 2.98)
