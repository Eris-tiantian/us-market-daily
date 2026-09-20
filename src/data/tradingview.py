from .base import DataError


class TradingViewProvider:
    name = "tradingview"
    max_batch_size = 1

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def availability(self) -> dict:
        return {"available": False, "reason": "no_authorized_stable_market_data_api"}

    def fetch_daily(self, tickers, start, end):
        raise DataError("tradingview_unsupported")
