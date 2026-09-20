import pytest

from src.universe import parse_directory, load_universe


NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
GOODW|Good Inc. - Warrants|S|N|N|100|N|N
FUND|An ETF|Q|N|N|100|Y|N
PREF|Preferred stock|Q|N|N|100|N|N
TEST|Test common stock|Q|Y|N|100|N|N
File Creation Time: 0919202610:00|||||||
"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Class B Common Stock|N|BRK.B|N|100|N|BRK.B
SMALL|Small Company Common Stock|A|SMALL|N|100|N|SMALL
OTC|OTC Common Stock|U|OTC|N|100|N|OTC
"""


def test_reference_directory_filters_non_common_instruments():
    got = parse_directory(NASDAQ, OTHER, ["NASDAQ", "NYSE", "AMEX"])
    assert {x["ticker"] for x in got} == {"AAPL", "BRK-B", "SMALL"}
    assert next(x for x in got if x["ticker"] == "BRK-B")["exchange"] == "NYSE"


def test_universe_cache_and_no_silent_truncation(tmp_path, monkeypatch):
    from src import universe
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return NASDAQ if "nasdaqlisted" in url else OTHER
    monkeypatch.setattr(universe, "fetch_directory", fetch)
    cfg = {"data": {"cache_dir": str(tmp_path)}, "scanner": {"max_universe": 1}}
    with pytest.raises(RuntimeError, match="truncat"):
        load_universe(cfg)
    cfg["scanner"].pop("max_universe")
    items, stats = load_universe(cfg)
    assert len(items) == 3
    assert len(calls) == 2
    assert stats["cache_hit"]
    assert not stats["truncated"]
