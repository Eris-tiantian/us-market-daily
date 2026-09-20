import json
import numpy as np
import pandas as pd
import pytest

from src.data import DataClient
from src.data.base import DataError, normalize_bars


def bars(end="2026-09-18", rows=100, scale=1.0):
    idx = pd.bdate_range(end=end, periods=rows)
    close = np.linspace(80, 100, rows) * scale
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * .99,
                         "Close": close, "Adj Close": close, "Volume": 1_000_000.0}, index=idx)


class FakeProvider:
    max_batch_size = 64
    def __init__(self, name, response):
        self.name, self.response, self.calls = name, response, []
    def availability(self):
        return {"available": True, "reason": "test"}
    def fetch_daily(self, tickers, start, end):
        self.calls.append((tickers, start, end))
        return self.response(tickers, start, end)


def config(tmp_path):
    return {"data": {"cache_dir": str(tmp_path), "min_rows": 80, "backoff_seconds": 0, "retries": 0}}


def test_adjustment_is_applied_to_all_ohlc():
    raw = bars()
    raw["Adj Close"] = raw["Close"] / 2
    got = normalize_bars(raw, "2026-09-18", adjusted=False)
    assert got.iloc[-1]["Close"] == 50
    assert got.iloc[-1]["Open"] == 50
    assert got.iloc[-1]["High"] == 50.5
    assert got.iloc[-1]["Volume"] == 1_000_000


@pytest.mark.parametrize("kind", ["stale", "nan", "negative", "geometry"])
def test_bad_bars_rejected(kind):
    frame = bars()
    if kind == "stale": frame = frame.iloc[:-1]
    if kind == "nan": frame.iloc[-1, 0] = np.nan
    if kind == "negative": frame.iloc[-1, 5] = -1
    if kind == "geometry": frame.iloc[-1, 1] = 1
    with pytest.raises(DataError):
        normalize_bars(frame, "2026-09-18", adjusted=True)


def test_partial_failure_falls_back_only_missing_ticker(tmp_path):
    primary = FakeProvider("factset", lambda ts, s, e: {"SPY": bars()})
    backup = FakeProvider("yahoo", lambda ts, s, e: {t: bars() for t in ts})
    client = DataClient(config(tmp_path), providers=[primary, backup])
    got = client.get_daily_bars(["SPY", "QQQ"], "2026-09-18")
    assert set(got) == {"SPY", "QQQ"}
    assert backup.calls[0][0] == ["QQQ"]
    assert client.diagnostics["ticker_providers"] == {"SPY": "factset", "QQQ": "yahoo"}
    json.dumps(client.diagnostics)


def test_yahoo_retries_three_times_and_sanitizes_error(tmp_path, caplog):
    def fail(ts, s, e):
        raise RuntimeError("SUPER_SECRET_API_KEY server response text")
    provider = FakeProvider("yahoo", fail)
    client = DataClient(config(tmp_path), providers=[provider])
    with pytest.raises(DataError) as exc:
        client.get_daily_bars(["SPY"], "2026-09-18")
    assert len(provider.calls) == 4
    assert "SUPER_SECRET" not in str(exc.value) + caplog.text + json.dumps(client.diagnostics)


def test_cache_reuses_same_session_and_refreshes_changed_adjustment(tmp_path):
    provider = FakeProvider("yahoo", lambda ts, s, e: {t: bars() for t in ts})
    client = DataClient(config(tmp_path), providers=[provider])
    client.get_daily_bars(["SPY"], "2026-09-18")
    client.get_daily_bars(["SPY"], "2026-09-18")
    assert len(provider.calls) == 1
    provider.response = lambda ts, s, e: {t: bars("2026-09-21", rows=101, scale=.5) for t in ts}
    result = client.get_daily_bars(["SPY"], "2026-09-21")
    assert len(provider.calls) == 3  # overlap revision triggers full replacement
    assert result["SPY"]["Close"].max() == 50


def test_missing_ticker_does_not_discard_success(tmp_path):
    provider = FakeProvider("yahoo", lambda ts, s, e: {"SPY": bars()})
    client = DataClient(config(tmp_path), providers=[provider])
    assert set(client.get_daily_bars(["SPY", "MISSING"], "2026-09-18")) == {"SPY"}
    assert client.diagnostics["failed_tickers"] == ["MISSING"]


def test_deterministic_bad_bars_are_rejected_without_network_retries(tmp_path):
    invalid = bars()
    invalid.iloc[-1, invalid.columns.get_loc("Volume")] = 0
    provider = FakeProvider("yahoo", lambda ts, s, e: {"HALTED": invalid})
    client = DataClient(config(tmp_path), providers=[provider])
    with pytest.raises(DataError):
        client.get_daily_bars(["HALTED"], "2026-09-18")
    assert len(provider.calls) == 1
    assert client.diagnostics["errors"][0]["error_type"] == "no_session_trading_volume"


def test_factset_verified_field_mapping():
    from src.data.factset import FactSetProvider
    rows = [{"requestId": "SPY-US", "date": "2026-09-18", "price": 100,
             "priceOpen": 99, "priceHigh": 101, "priceLow": 98, "volume": 10_000}]
    result = FactSetProvider.parse_response(rows, {"SPY": "SPY-US"})
    frame = normalize_bars(result["SPY"], "2026-09-18")
    assert frame.iloc[0]["Close"] == 100
    assert "DIV_SPIN_SPLITS" in frame.attrs["adjustment"]


def test_ftshare_requires_verified_uniform_forward_adjustment():
    from src.data.ftshare import FTShareProvider
    row = {"date": "2026-09-18", "open": "99", "high": "101", "low": "98",
           "close": "100", "volume": "10000", "fqt": "1"}
    frame = FTShareProvider.parse_response([row], "2026-09-01", "2026-09-18")
    assert normalize_bars(frame, "2026-09-18").iloc[0]["Volume"] == 10_000
    for flag in ("0", "2", None):
        with pytest.raises(DataError):
            FTShareProvider.parse_response([{**row, "fqt": flag}], "2026-09-01", "2026-09-18")


def test_corrupt_cache_is_ignored(tmp_path):
    import gzip
    provider = FakeProvider("yahoo", lambda ts, s, e: {t: bars() for t in ts})
    client = DataClient(config(tmp_path), providers=[provider])
    path = client._path("SPY")
    path.parent.mkdir(parents=True)
    payload = {"schema": 1, "ticker": "SPY", "dates": ["2026-09-18"],
               "data": [[100]], "columns": ["Close"], "attrs": {}}
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))
    assert "SPY" in client.get_daily_bars(["SPY"], "2026-09-18")


def test_sector_lookup_uses_cached_metadata(tmp_path):
    provider = FakeProvider("yahoo", lambda ts, s, e: {})
    calls = []
    provider.get_sector = lambda t: calls.append(t) or "Healthcare"
    client = DataClient(config(tmp_path), providers=[provider])
    assert client.get_sector_etf("TEM") == "XLV"
    assert client.get_sector_etf("TEM") == "XLV"
    assert calls == ["TEM"]
