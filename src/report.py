"""Compact Chinese daily report with measured wrapping and exact PNG width."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import math

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, ft2font
from matplotlib.patches import Rectangle
from PIL import ImageFont

INK = "#172738"
MUTED = "#526372"
BLUE = "#174c78"
PALE = "#edf2f6"
RULE = "#d9e1e8"
GREEN = "#176249"
RED = "#a53f39"


def _font_setup():
    """Never silently emit unreadable tofu when a runner lacks a CJK font."""
    names = ["Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans CJK JP",
             "Source Han Sans SC", "SimHei", "Arial Unicode MS", "DengXian"]
    fonts = list(font_manager.fontManager.ttflist)
    fonts.sort(key=lambda font: (names.index(font.name) if font.name in names else len(names),
                                 font.style != "normal", font.weight not in (400, "normal")))
    required = {ord(char) for char in "美股每日行情分析阶段观察强弱撑压风险酱"}
    for font in fonts:
        try:
            if not required.issubset(ft2font.FT2Font(font.fname).get_charmap()):
                continue
        except (RuntimeError, OSError):
            continue
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [font.name]
        plt.rcParams["axes.unicode_minus"] = False
        return font.fname
    raise RuntimeError("缺少中文字体：Windows 请安装 Microsoft YaHei；Linux 请安装 fonts-noto-cjk。")


def _finite(value):
    try:
        return bool(np.isfinite(float(value)))
    except (ValueError, TypeError):
        return False


def _fmt_pct(value):
    return f"{float(value) * 100:+.1f}%" if _finite(value) else "—"


def _fmt_num(value, digits=2):
    return f"{float(value):.{digits}f}" if _finite(value) else "—"


def _fmt_amount(value):
    if not _finite(value):
        return "—"
    value = float(value)
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.1f}万"
    return f"{value:,.0f}"


def _text(value, default="—"):
    if value is None or (isinstance(value, (float, np.floating)) and not _finite(value)):
        return default
    return str(value)


def _short_stage(stage):
    stage = _text(stage)
    return {"Stage 2 Early": "S2 初期", "Stage 2": "S2 延续",
            "Stage 2 continuation": "S2 延续", "Stage 1": "S1 筑底",
            "Stage 3": "S3 顶部", "Stage 4": "S4 下跌",
            "Stage 2?": "S2?", "Stage 4?": "S4?", "Stage 1/3": "S1/3"}.get(stage, stage)


def _sector_order(snapshot, tickers):
    rank = {"Stage 2 Early": 5, "Stage 2": 4, "Stage 2 continuation": 4,
            "Stage 2?": 3, "Stage 1": 2, "Stage 1/3": 1,
            "Stage 3": 0, "Stage 4?": -1, "Stage 4": -2}

    def key(ticker):
        row = snapshot.loc[ticker] if ticker in snapshot.index else {}
        return (rank.get(row.get("stage"), -3),
                *(float(row.get(col)) if _finite(row.get(col)) else -math.inf
                  for col in ("rs20", "rs60", "ma30_slope")))

    return sorted(tickers, key=key, reverse=True)


class _Layout:
    """Lay out in output pixels first, then render onto a correctly sized figure."""

    def __init__(self, width, font_path):
        self.width = width
        self.font_path = font_path
        self.margin = 48
        self.inner = width - 2 * self.margin
        self.y = 36
        self.items = []

    @lru_cache(maxsize=32)
    def font(self, size):
        return ImageFont.truetype(self.font_path, int(size))

    def wrap(self, text, width, size):
        lines = []
        for paragraph in str(text).split("\n"):
            line = ""
            for char in paragraph:
                if line and self.font(size).getlength(line + char) > width:
                    lines.append(line.rstrip())
                    line = char.lstrip()
                else:
                    line += char
            lines.append(line)
        return lines

    def put(self, text, x, y, size=23, color=INK, bold=False, align="left"):
        self.items.append(("text", x, y, str(text), size, color, bold, align))

    def paragraph(self, text, size=23, color=INK, indent=0, gap=5):
        lines = self.wrap(text, self.inner - 2 * indent, size)
        for line in lines:
            self.put(line, self.margin + indent, self.y, size, color)
            self.y += size * 1.38
        self.y += gap

    def box(self, x, y, width, height, color):
        self.items.append(("box", x, y, width, height, color))

    def section(self, number, title, detail=None):
        self.y += 17
        self.box(self.margin, self.y + 2, 5, 30, BLUE)
        self.put(f"{number}. {title}", self.margin + 17, self.y, 28, BLUE, True)
        self.y += 44
        if detail:
            self.paragraph(detail, size=21, color=MUTED, gap=5)

    def table(self, labels, rows, weights, size=22):
        widths = np.array(weights, dtype=float)
        widths = self.inner * widths / widths.sum()
        for i, row in enumerate([labels] + rows):
            cells = [self.wrap(value, width - 12, size) for value, width in zip(row, widths)]
            row_height = max(35, max(map(len, cells)) * size * 1.25 + 10)
            if i == 0 or i % 2:
                self.box(self.margin, self.y, self.inner, row_height, PALE if i == 0 else "#f7f9fb")
            x = self.margin
            for cell, width in zip(cells, widths):
                for j, line in enumerate(cell):
                    self.put(line, x + 8, self.y + 6 + j * size * 1.25,
                             size, BLUE if i == 0 else INK, i == 0)
                x += width
            self.y += row_height
        self.y += 3

    def render(self, height, dpi, data, market, output):
        fig = plt.figure(figsize=(self.width / dpi, height / dpi), dpi=dpi, facecolor="white")
        font = font_manager.FontProperties(fname=self.font_path)
        try:
            for item in self.items:
                if item[0] == "text":
                    _, x, y, text, size, color, bold, align = item
                    fig.text(x / self.width, 1 - y / height, text, va="top", ha=align,
                             fontsize=size * 72 / dpi, color=color, fontproperties=font,
                             weight="bold" if bold else "normal", parse_math=False)
                elif item[0] == "box":
                    _, x, y, width, box_height, color = item
                    fig.add_artist(Rectangle((x / self.width, 1 - (y + box_height) / height),
                                             width / self.width, box_height / height,
                                             transform=fig.transFigure, facecolor=color, edgecolor="none", zorder=0))
                elif item[0] == "chart":
                    _, top, chart_height = item
                    ax = fig.add_axes([(self.margin + 60) / self.width,
                                       1 - (top + chart_height - 32) / height,
                                       (self.inner - 78) / self.width, (chart_height - 44) / height])
                    plotted = False
                    for ticker, color in zip(market, [BLUE, "#a87920", GREEN]):
                        if ticker not in data or "Close" not in data[ticker]:
                            continue
                        close = pd.to_numeric(data[ticker]["Close"], errors="coerce").dropna().tail(60)
                        if len(close) < 2 or close.iloc[0] <= 0:
                            continue
                        ax.plot(close.index, close / close.iloc[0] * 100, label=ticker, color=color, linewidth=1.7)
                        plotted = True
                    if plotted:
                        ax.legend(loc="upper left", ncol=3, frameon=False, fontsize=22 * 72 / dpi)
                    else:
                        ax.text(.5, .5, "指数走势数据缺失", ha="center", transform=ax.transAxes,
                                fontproperties=font, fontsize=23 * 72 / dpi)
                    ax.tick_params(axis="both", labelsize=19 * 72 / dpi, colors=MUTED, length=0)
                    ax.grid(axis="y", color=RULE, linewidth=.6)
                    for spine in ax.spines.values():
                        spine.set_visible(False)
                    ax.margins(x=.01)
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            # tight bounding boxes alter the promised pixel dimensions.
            fig.savefig(output, dpi=dpi, facecolor="white")
        finally:
            plt.close(fig)


def _stock_card(layout, ticker, row, *, candidate=False, sector_map=None, snapshot=None):
    start = layout.y
    box_index = len(layout.items)
    layout.y += 12
    if not row:
        layout.paragraph(f"{ticker}  |  数据缺失，今日无法判断结构", size=25, color=RED, indent=14)
    else:
        status = _text(row.get("structure_status"), "等待")
        layout.paragraph(f"{ticker}  |  {_text(row.get('stage'))}  |  {status}",
                         size=25, color=BLUE, indent=14, gap=5)
        layout.paragraph(
            f"收盘 {_fmt_num(row.get('price'))}    1D {_fmt_pct(row.get('r1'))}    "
            f"5D {_fmt_pct(row.get('r5'))}    20D {_fmt_pct(row.get('r20'))}    60D {_fmt_pct(row.get('r60'))}",
            indent=14, gap=1)
        layout.paragraph(
            f"RS20 {_fmt_pct(row.get('rs20'))}    RS60 {_fmt_pct(row.get('rs60'))}    "
            f"RS120 {_fmt_pct(row.get('rs120'))}    量比 {_fmt_num(row.get('vol_ratio'))}x    "
            f"20日均量 {_fmt_amount(row.get('avg_volume20'))}    均额 ${_fmt_amount(row.get('avg_dollar_volume20'))}",
            indent=14, gap=1)
        layout.paragraph(
            f"30WMA {_fmt_num(row.get('ma30'))}    距均线 {_fmt_pct(row.get('ma30_distance'))}    "
            f"周斜率 {_fmt_pct(row.get('ma30_slope'))}    "
            f"阶段起始 {_text(row.get('stage_started_at'))} / {_fmt_num(row.get('weeks_in_stage'), 0)}周",
            indent=14, gap=1)
        layout.paragraph(
            f"支撑 {_fmt_num(row.get('support'))} ({_text(row.get('support_source'), '未确认来源')})    "
            f"压力 {_fmt_num(row.get('resistance'))} ({_text(row.get('resistance_source'), '未确认来源')})    "
            f"突破位 {_fmt_num(row.get('breakout_level'))} / 距突破 {_fmt_pct(row.get('distance_from_breakout'))}",
            indent=14, gap=1)
        if candidate:
            sector = row.get("sector_etf", row.get("sector", ""))
            industry = snapshot.loc[sector].to_dict() if snapshot is not None and sector in snapshot.index else {}
            layout.paragraph(
                f"行业 {sector} {(sector_map or {}).get(sector, '')}：{_short_stage(industry.get('stage'))}    "
                f"RS20 {_fmt_pct(industry.get('rs20'))} / RS60 {_fmt_pct(industry.get('rs60'))}",
                indent=14, gap=1)
            entry = f"{_fmt_num(row.get('entry_low', row.get('entry')))}–{_fmt_num(row.get('entry_high', row.get('entry')))}"
            rr = _fmt_num(row.get("rr")) if _finite(row.get("rr")) else "无法可靠计算"
            layout.paragraph(
                f"参考介入 {entry}    失效 {_fmt_num(row.get('invalidation'))}    "
                f"首个目标 {_fmt_num(row.get('target'))}    风险 {_fmt_num(row.get('risk'))} / "
                f"潜在收益 {_fmt_num(row.get('reward'))}    RR {rr}", indent=14, gap=1)
        layout.paragraph(f"依据：{_text(row.get('reason'), '等待周线趋势、位置与成交量联合确认。')}",
                         size=22, color=MUTED, indent=14, gap=2)
    layout.y += 9
    layout.items.insert(box_index, ("box", layout.margin, start, layout.inner, layout.y - start, "#f4f7fa"))
    layout.y += 9


def _first(mapping, names, default="—"):
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def _structure_notes(layout, snapshot, cfg, diagnostics):
    sectors = list(cfg.get("sectors", {}))
    valid_sectors = snapshot.loc[[ticker for ticker in sectors if ticker in snapshot.index]]
    if not valid_sectors.empty:
        strong = valid_sectors["stage"].isin(["Stage 2 Early", "Stage 2", "Stage 2 continuation"]).sum()
        rs_positive = (pd.to_numeric(valid_sectors.get("rs20"), errors="coerce") > 0).sum()
        leaders = _sector_order(snapshot, list(valid_sectors.index))[:3]
        layout.paragraph(f"行业广度：{strong}/{len(valid_sectors)} 处于 S2；{rs_positive}/{len(valid_sectors)} 的 RS20 > 0。"
                         f"领先行业：{' / '.join(leaders)}。", size=22, gap=2)
    excluded = set(sectors + list(cfg.get("market", [])) + [cfg.get("risk", {}).get("vix", "^VIX")])
    stocks = snapshot.loc[[ticker for ticker in snapshot.index if ticker not in excluded]]
    if not stocks.empty and "rs20" in stocks:
        leader_rows = stocks.sort_values("rs20", ascending=False).head(3)
        examples = "；".join(f"{ticker} RS20 {_fmt_pct(row.get('rs20'))} / {_short_stage(row.get('stage'))}"
                             for ticker, row in leader_rows.iterrows())
        layout.paragraph(f"已分析股票的相对强弱示例：{examples}。仅作结构解释。", size=22, gap=2)
    scanner = diagnostics.get("scanner", {}) or {}
    universe = diagnostics.get("universe", {}) or {}
    data_diag = diagnostics.get("data", {}) or {}
    if isinstance(scanner, dict):
        total = _first(scanner, ["universe_count", "universe_size", "universe", "requested"],
                       _first(universe, ["universe_count", "count", "selected", "eligible_count"]) if isinstance(universe, dict) else "—")
        downloaded = _first(scanner, ["downloaded", "downloaded_count", "data_count", "successful"])
        tier1 = _first(scanner, ["tier1_count", "first_pass", "liquidity_pass", "liquid_count", "liquidity_passed"])
        tier2 = _first(scanner, ["tier2_count", "second_pass", "trend_pass", "trend_passed"])
        failed = _first(scanner, ["failed_count", "failed", "failed_tickers"], [])
        failed_count = len(failed) if isinstance(failed, (list, dict, set)) else failed
        layout.paragraph(f"扫描覆盖：Universe {total}；下载 {downloaded}；流动性通过 {tier1}；趋势通过 {tier2}；失败 {failed_count}。"
                         "覆盖仅代表本次有效样本。", size=22, gap=2)
    if isinstance(data_diag, dict):
        provider = _first(data_diag, ["final_providers", "final_provider", "provider",
                                      "provider_used", "selected_provider", "providers"])
        if isinstance(provider, dict):
            provider = " / ".join(sorted(set(map(str, provider.values()))))
        if isinstance(provider, list):
            provider = " / ".join(map(str, provider))
        layout.paragraph(f"实际数据源：{provider}。价格与支撑压力沿用统一复权口径。", size=21, color=MUTED, gap=2)
    warnings = diagnostics.get("warnings", []) or []
    if isinstance(warnings, str):
        warnings = [warnings]
    for warning in warnings[:6]:
        layout.paragraph(f"注意：{warning}", size=21, color=RED, gap=2)
    if len(warnings) > 6:
        layout.paragraph(f"另有 {len(warnings) - 6} 条诊断，详见当日运行日志。", size=21, color=RED, gap=2)


def create_report(data: dict[str, pd.DataFrame], snapshot: pd.DataFrame, cfg: dict,
                  session: str, regime: str, regime_score: float, output: str | Path,
                  candidates: list[dict] | None = None, diagnostics: dict | None = None):
    """Render the fixed watchlist and 0–5 independently selected daily candidates."""
    font_path = _font_setup()
    rp = cfg.get("report", {})
    width, dpi = int(rp.get("width", 1600)), int(rp.get("dpi", 150))
    if width < 1000 or dpi <= 0:
        raise ValueError("报告宽度须至少 1000 像素且 dpi 为正数")
    layout = _Layout(width, font_path)
    candidates = list(candidates or [])[:5]
    diagnostics = diagnostics or {}
    layout.paragraph(rp.get("title", "美股每日行情分析"), size=42, color=BLUE, gap=7)
    layout.paragraph(f"NYSE {session}  |  市场状态：{regime}  |  机械评分 {_fmt_num(regime_score)}", size=26, gap=3)
    layout.paragraph("规则化状态判断，不代表市场预测。周线决定方向，日线刻画位置。", size=22, color=MUTED, gap=2)

    layout.section(1, "市场环境")
    market = list(cfg.get("market", ["SPY", "QQQ", "IWM"]))
    vix = cfg.get("risk", {}).get("vix", "^VIX")
    market_display = market + ([vix] if vix and vix not in market else [])
    rows = []
    for ticker in market_display:
        row = snapshot.loc[ticker].to_dict() if ticker in snapshot.index else {}
        rows.append([ticker, _fmt_num(row.get("price")), *[_fmt_pct(row.get(col)) for col in ("r1", "r5", "r20", "r60")],
                     _fmt_num(row.get("ma30")), _fmt_pct(row.get("ma30_distance")), _fmt_pct(row.get("ma30_slope")),
                     _short_stage(row.get("stage"))])
    layout.table(["指数", "收盘", "1D", "5D", "20D", "60D", "30WMA", "距均线", "周斜率", "Stage"], rows,
                 [100, 115, 108, 108, 108, 108, 125, 132, 132, 160])

    layout.section(2, "指数近 60 个交易日走势", "各指数可用交易日起点 = 100；VIX 为波动率指标，方向与股指不同。")
    layout.items.append(("chart", layout.y, 236))
    layout.y += 240

    layout.section(3, "行业 / 主题强弱", "依次按 Stage、RS20、RS60、30WMA 斜率排序；RS = 同期收益率 − SPY 收益率。")
    sector_map = cfg.get("sectors", {})
    rows = []
    for ticker in _sector_order(snapshot, list(sector_map)):
        row = snapshot.loc[ticker].to_dict() if ticker in snapshot.index else {}
        rows.append([f"{ticker} {sector_map[ticker]}", *[_fmt_pct(row.get(col)) for col in
                     ("r1", "r5", "r20", "r60", "rs20", "rs60", "ma30_distance", "ma30_slope")], _short_stage(row.get("stage"))])
    layout.table(["行业", "1D", "5D", "20D", "60D", "RS20", "RS60", "距30WMA", "周斜率", "Stage"], rows,
                 [230, 100, 100, 100, 100, 108, 108, 136, 125, 145], size=22)

    layout.section(4, "固定观察池", "仅 TEM / RXRX / ENPH 持续追踪；观察状态不等同于候选资格。")
    fixed_config = cfg.get("watchlist", {})
    fixed = fixed_config.get("fixed", ["TEM", "RXRX", "ENPH"]) if isinstance(fixed_config, dict) else ["TEM", "RXRX", "ENPH"]
    for ticker in fixed:
        row = snapshot.loc[ticker].to_dict() if ticker in snapshot.index else {}
        _stock_card(layout, ticker, row)

    layout.section(5, f"今日动态候选：{len(candidates)}", "每日重新筛选；参考价格来自可解释结构，条件失效后退出候选。")
    if not candidates:
        layout.paragraph("今日无高质量新增候选", size=27, color=BLUE, gap=5)
        layout.paragraph("市场过滤或 Stage + RS + 行业 + 位置 + 成交量 + RR 联合条件未通过。", size=22, color=MUTED)
    for candidate in candidates:
        ticker = str(candidate.get("ticker", "未知"))
        row = snapshot.loc[ticker].to_dict() if ticker in snapshot.index else {}
        row.update(candidate)
        _stock_card(layout, ticker, row, candidate=True, sector_map=sector_map, snapshot=snapshot)

    layout.section(6, "市场结构补充")
    _structure_notes(layout, snapshot, cfg, diagnostics)
    layout.section(7, "规则与风险提示")
    weekly_asof = _first(diagnostics, ["weekly_asof", "completed_week_asof", "completed_week"])
    if weekly_asof == "—" and "weekly_asof" in snapshot:
        dates = snapshot["weekly_asof"].dropna()
        weekly_asof = str(dates.max()) if not dates.empty else "—"
    layout.paragraph(f"周线口径：已完成周；最新周截至 {weekly_asof}。30WMA 为 30 周均线，周斜率按周计算。"
                     "RS20 / 60 / 120 为交易日收益差；量比使用当日前 20 日均量。", size=21, color=MUTED, gap=2)
    layout.paragraph("Stage 与支撑压力为机械近似；RR = (目标 − 参考介入) / (参考介入 − 失效)。"
                     "无可靠目标则不计算 RR。复权价格不等同于实际委托报价；跳空、流动性和数据延迟会影响结果。本报告用于观察，不构成交易建议。",
                     size=21, color=MUTED, gap=2)
    height = max(int(rp.get("height", 2200)), math.ceil(layout.y + 30))
    layout.render(height, dpi, data, market, output)
