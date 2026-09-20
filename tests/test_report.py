from copy import deepcopy
import warnings

import matplotlib.figure
import numpy as np
import pandas as pd
from PIL import Image
import pytest

from src import report


def report_inputs():
    sectors = dict(zip(
        ["SMH", "IGV", "XBI", "XLK", "XLC", "XLY", "XLF", "XLI", "XLE", "XLB", "XLV", "XLP", "XLU", "XLRE"],
        ["半导体", "软件", "生物科技", "科技", "通讯", "可选消费", "金融", "工业", "能源", "材料", "医疗", "必选消费", "公用事业", "房地产"],
    ))
    cfg = {"market": ["SPY", "QQQ", "IWM"], "risk": {"vix": "^VIX"}, "sectors": sectors,
           "watchlist": {"fixed": ["TEM", "RXRX", "ENPH"]},
           "report": {"width": 1600, "height": 2200, "dpi": 150}}
    row = dict(price=100.0, r1=.012, r5=.023, r20=.045, r60=.076, rs20=.034, rs60=.055,
               rs120=.098, ma30=95.0, ma30_distance=.053, ma30_slope=.002, stage="Stage 2",
               stage_started_at="2026-05-22", weeks_in_stage=17, breakout_level=96.0,
               distance_from_breakout=.042, vol_ratio=1.4, avg_volume20=1234567,
               avg_dollar_volume20=123456789, support=97, resistance=115,
               entry=100, entry_low=99, entry_high=100, invalidation=96, target=115,
               risk=4, reward=15, rr=3.75, structure_status="符合", reason="周线趋势向上，等待合理位置。",
               support_source="近期 pivot low", resistance_source="近期 pivot high")
    tickers = cfg["market"] + ["^VIX"] + list(sectors) + cfg["watchlist"]["fixed"]
    snapshot = pd.DataFrame([row.copy() for _ in tickers], index=tickers)
    dates = pd.bdate_range("2026-06-29", periods=60)
    data = {ticker: pd.DataFrame({"Close": np.linspace(80, 100, 60)}, index=dates) for ticker in tickers}
    return data, snapshot, cfg, row


@pytest.mark.parametrize("count", [0, 5])
def test_dense_report_full_content_exact_width_without_clipping(tmp_path, monkeypatch, count):
    data, snapshot, cfg, row = report_inputs()
    original_cfg = deepcopy(cfg)
    candidates = [dict(row, ticker=f"NEW{i}", sector_etf="SMH",
                       reason="行业与个股周线同步走强；突破后回踩支撑，成交量确认。" * 3) for i in range(count)]
    captured = {}
    savefig = matplotlib.figure.Figure.savefig

    def inspect_and_save(fig, *args, **kwargs):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        texts = [obj for obj in fig.findobj() if isinstance(obj, matplotlib.text.Text) and obj.get_visible() and obj.get_text()]
        captured["texts"] = [obj.get_text() for obj in texts]
        for obj in texts:
            box = obj.get_window_extent(renderer)
            assert box.x0 >= -1 and box.y0 >= -1, obj.get_text()
            assert box.x1 <= fig.bbox.width + 1 and box.y1 <= fig.bbox.height + 1, obj.get_text()
        return savefig(fig, *args, **kwargs)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", inspect_and_save)
    output = tmp_path / "report.png"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report.create_report(data, snapshot, cfg, "2026-09-18", "偏强", 1.0, output,
                             candidates=candidates,
                             diagnostics={"scanner": {"universe_count": 3000, "downloaded": 2900, "final_candidates": count},
                                          "warnings": ["单个股票缺失，已跳过；核心指数完整。"]})
    assert not [w for w in caught if "Glyph" in str(w.message) and "missing" in str(w.message)]
    with Image.open(output) as img:
        assert img.width == 1600
        assert img.height >= 2200
        assert img.height < 6500
    rendered = "\n".join(captured["texts"])
    for expected in ["TEM", "RXRX", "ENPH", "XLRE", "RS120", "60D", "30WMA", "规则化状态", "市场结构补充"]:
        assert expected in rendered
    if count == 0:
        assert "今日无高质量新增候选" in rendered
    else:
        assert "NEW4" in rendered
        assert "失效" in rendered and "目标" in rendered
    assert cfg == original_cfg


def test_report_requires_chinese_font(monkeypatch):
    monkeypatch.setattr(report.font_manager.fontManager, "ttflist", [])
    with pytest.raises(RuntimeError, match="中文字体"):
        report._font_setup()


def test_report_missing_optional_values_remain_visible(tmp_path):
    data, snapshot, cfg, _ = report_inputs()
    snapshot.loc["TEM", ["rr", "resistance", "stage_started_at", "vol_ratio"]] = None
    snapshot = snapshot.drop(index="RXRX")
    report.create_report(data, snapshot, cfg, "2026-09-18", "中性", 0,
                         tmp_path / "missing.png", candidates=[])
    assert (tmp_path / "missing.png").is_file()
