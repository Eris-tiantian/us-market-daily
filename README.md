# 美股每日行情分析 → PNG → Server酱 → 微信

本项目在北京时间每天 08:00 寻找最近一个已经收盘的 NYSE Session，生成中文 PNG 日报并通过 Server酱推送。状态顺序固定为：生成 → 发布 → 匿名读取并校验 SHA-256 → Server酱明确返回 `code=0` → 写入 `sent_session.txt`。任何一步失败，已发送日期保持不变。

## 分析范围

- 大盘：SPY、QQQ、IWM、VIX；报告含 1/5/20/60 日收益、周线 30WMA、斜率、Stage 和近 60 个交易日标准化曲线。
- 行业：SMH、IGV、XBI、XLK、XLC、XLY、XLF、XLI、XLE、XLB、XLV、XLP、XLU、XLRE。排序依次使用 Stage、RS20、RS60、30WMA 斜率。
- 固定观察池：只包含 `TEM / RXRX / ENPH`，每天追踪但不强行判定为买点。
- 动态候选：从 NASDAQ Trader 的 NASDAQ、NYSE、NYSE American 普通股目录每日重算，最多 5 只，也可以为 0。动态候选不会写回固定观察池。

扫描分三层执行：价格、历史长度和 20 日平均成交额；随后 Stage、RS20/60、30WMA；最后行业、支撑压力、位置、成交量和真实可观察目标对应的 RR。默认要求 RR ≥ 2；没有可靠压力目标时 RR 不可计算，股票不会进入候选。

## 数据层

上层只调用 `DataClient.get_daily_bars()`、`get_quote()` 和 `get_market_snapshot()`。统一输出日期索引及 Open/High/Low/Close/Volume/Adj Close；OHLC 使用同一复权基准，成交量保留数据源报告值。

默认自动顺序为 `FactSet → FTShare → Yahoo`。只有凭据和 SDK/接口实际可用的 provider 才参与；无商业凭据时自动采用 Yahoo。Yahoo 批量请求有超时、至少三次指数退避重试、逐 ticker 校验、失败统计和分 ticker fallback。若两个 provider 同时可用，只对 SPY/QQQ/IWM 做交叉校验。TradingView 没有稳定授权 API 时明确标记为不可用，不使用网页抓取或 Cookie。

缓存位于 `cache/`，包括行情、股票目录、provider 状态和少量候选行业元数据。增量更新遇到复权历史变化时会整段刷新，避免拼接不同复权基准。GitHub Actions 使用缓存减少重复下载，但每次必须取得目标 Session 的最新日线。

Yahoo Finance 适合本项目的低频个人研究，但不是交易所官方或带 SLA 的生产行情。严格审计或商业使用应配置已授权的 FactSet/FTShare 数据。

## Weinstein Stage 与指标

Stage 只使用已经完成的 NYSE 周线。普通周在最后一个 Session 结束前不会纳入；提前休市、Good Friday、夏令时和冬令时由正式 NYSE 日历处理。

`Stage 2 Early` 必须同时满足：充分的平坦平台、30WMA 由平转升、实际突破平台、突破仍年轻且未远离、突破量能达标、RS20/60 为正、行业确认。长期 Stage 2 再创新高仍是 continuation，不会重置为 Early。

RS20/60/120 定义为股票同期收益减 SPY 同期收益。量比为当日成交量除以前 20 日均量。支撑来自 30WMA、确认 pivot low 或突破位；压力来自确认 pivot high 或 52 周高点。报告同时显示参考介入、失效、目标、风险、收益和 RR，并注明这些是机械规则状态，不是预测或交易建议。

## 配置

所有阈值位于 `config.yaml`。固定池结构必须是：

```yaml
watchlist:
  fixed: [TEM, RXRX, ENPH]
```

调整扫描流动性、位置和 RR 时修改 `scanner.min_price`、`scanner.min_history_days`、`scanner.min_avg_dollar_volume_20d`、`scanner.max_ma30_distance`、`scanner.max_support_distance` 和 `rr.minimum`。不要为了每天产生候选而放松联合条件。

## GitHub Actions 部署

仓库必须公开，才能让微信匿名读取 PNG。Actions Secret 只需 `SERVERCHAN_SENDKEY`。不要将 SendKey 写入代码、配置、日志或提交。

工作流同时支持手动运行和：

```yaml
schedule:
  - cron: "0 8 * * *"
    timezone: "Asia/Shanghai"
```

定时任务在默认分支执行；GitHub 平台繁忙时可能延迟。`report-assets` 分支只保留 `latest.png`、`latest.json` 和 `sent_session.txt` 的短历史，不会无限保存日报。

## 本地验证

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m src.app prepare --config config.yaml --state .state/sent_session.txt --output reports/latest.png --meta reports/latest.json
```

`prepare` 只生成并验证报告，不发送消息。完整推送仅在 GitHub Actions 的 `run-github` 命令中执行，以确保图片地址和事务状态来自同一仓库。

## 故障排查

- `Missing GitHub Actions Secret`：确认仓库 Actions Secret 名为 `SERVERCHAN_SENDKEY`。
- `download coverage below configured minimum`：查看 Actions artifact 中 `latest.json` 的 provider/失败统计；不要直接降低覆盖阈值掩盖故障。
- `Public PNG unavailable or content hash mismatch`：公网缓存尚未返回本次 commit 的确切图片；任务失败且不会更新 Session。
- `ServerChan ... session unchanged`：检查 Server酱额度、通道绑定和 SendKey；重新运行 workflow 会重试本交易日。
- `No new completed NYSE session`：周末、休市或本 Session 已推送，属于正常跳过。
- 中文字体缺失：Linux 安装 `fonts-noto-cjk`；Windows 使用 Microsoft YaHei。代码会明确失败，不会生成方框字日报。

完整运行日志包含 UTC/北京时间、NYSE Session、上次 Session、provider/fallback、Universe/下载/过滤/失败数、候选数、报告路径、公网 URL 和 Server酱确认状态；不会输出密钥。
