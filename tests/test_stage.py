import numpy as np
import pandas as pd
import pytest

from src.market import classify_stage, weekly_frame


def bars_from_weeks(values, breakout_volume=False):
    weeks = pd.date_range("2023-01-06", periods=len(values), freq="W-FRI")
    index = pd.bdate_range(weeks[0] - pd.Timedelta(days=4), weeks[-1])
    close = pd.Series(np.repeat(values, 5), index=index)
    df = pd.DataFrame({"Open": close, "High": close * 1.001,
                       "Low": close * .999, "Close": close, "Volume": 1_000_000}, index=index)
    if breakout_volume:
        df.loc[df.index[-5:], "Volume"] = 2_000_000
    return df


def test_stage1_sustained_flat_base():
    assert classify_stage(bars_from_weeks([100.] * 60)).stage == "Stage 1"


def test_stage2_early_requires_base_breakout_turning_ma_and_confirmation():
    df = bars_from_weeks([100.] * 60 + [105.], breakout_volume=True)
    result = classify_stage(df, rs20=.05, rs60=.05, industry_ok=True)
    assert result.stage == "Stage 2 Early"
    assert result.weeks_in_stage == 1
    assert result.breakout_level == pytest.approx(100.1)
    assert result.stage_started_at is not None
    assert 0 < result.distance_from_breakout < .08


@pytest.mark.parametrize("kwargs", [{"rs20": -.01, "rs60": .1, "industry_ok": True},
                                  {"rs20": .1, "rs60": .1, "industry_ok": False}, {}])
def test_early_fails_closed_without_rs_or_industry(kwargs):
    df = bars_from_weeks([100.] * 60 + [105.], breakout_volume=True)
    assert classify_stage(df, **kwargs).stage != "Stage 2 Early"


def test_unconfirmed_volume_does_not_mark_early():
    df = bars_from_weeks([100.] * 60 + [105.])
    assert classify_stage(df, rs20=.1, rs60=.1, industry_ok=True).stage != "Stage 2 Early"


def test_long_stage2_new_high_is_continuation():
    df = bars_from_weeks(np.r_[np.repeat(50., 40), np.linspace(51., 100., 40)], True)
    result = classify_stage(df, rs20=.1, rs60=.2, industry_ok=True)
    assert result.stage == "Stage 2"
    assert result.weeks_in_stage > 6


def test_stage3_topping_after_rise():
    assert classify_stage(bars_from_weeks(np.r_[np.linspace(50., 100., 60), np.repeat(100., 30)])).stage == "Stage 3"


def test_stage4_falling():
    assert classify_stage(bars_from_weeks(np.linspace(100., 50., 80))).stage == "Stage 4"


def test_partial_week_is_excluded():
    df = bars_from_weeks([100.] * 60)
    end = df.index.max()
    partial = pd.DataFrame({"Open": [120.], "High": [121.], "Low": [119.],
                            "Close": [120.], "Volume": [1e6]}, index=[end + pd.Timedelta(days=3)])
    assert len(weekly_frame(pd.concat([df, partial]))) == len(weekly_frame(df))


def test_good_friday_week_is_complete_on_thursday():
    idx = pd.bdate_range("2025-04-14", "2025-04-17")
    df = pd.DataFrame({"Close": [100., 101., 102., 103.], "Volume": 1e6}, index=idx)
    w = weekly_frame(df)
    assert len(w) == 1
    assert w.Close.iloc[-1] == 103.
