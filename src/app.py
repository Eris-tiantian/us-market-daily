from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from .calendar import latest_completed_nyse_session
from .data import DataClient
from .delivery import deliver
from .market import build_snapshot, market_regime
from .publishing import GitHubAssets
from .serverchan import send_message, verify_public_image
from .state import read_sent_session, write_sent_session
from .stage import weekly_frame
from .universe import load_universe

LOGGER = logging.getLogger(__name__)


def load_config(path: str) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(cfg, dict) or not isinstance(cfg.get('watchlist'), dict):
        raise ValueError('config.watchlist.fixed must be an explicit list')
    fixed = cfg['watchlist'].get('fixed')
    if not isinstance(fixed, list) or not fixed or len(fixed) != len(set(fixed)):
        raise ValueError('Fixed watchlist must contain unique symbols')
    if not 0 <= int(cfg.get('scanner', {}).get('max_candidates', 5)) <= 5:
        raise ValueError('scanner.max_candidates must be between 0 and 5')
    return cfg


def all_tickers(cfg: dict) -> list[str]:
    tickers = list(cfg['market']) + [cfg['risk']['vix']]
    tickers += list(cfg['sectors']) + list(cfg['watchlist']['fixed'])
    tickers += [cfg.get('rs', {}).get('benchmark', 'SPY')]
    return list(dict.fromkeys(tickers))


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def write_github_output(path, should_send, session, reason=''):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_reason = reason.replace('\n', ' ').replace('\r', ' ')
    path.write_text(f'should_send={str(should_send).lower()}\nsession={session}\nreason={safe_reason}\n', encoding='utf-8')


def already_sent(session, sent):
    now = datetime.now(timezone.utc)
    LOGGER.info('utc=%s beijing=%s latest_nyse_session=%s last_sent_session=%s',
                now.isoformat(), now.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(), session, sent)
    if sent and sent > session:
        raise RuntimeError('Persisted sent session is newer than latest completed session; refusing overwrite')
    return sent == session


def validate_required_data(data, required, session):
    missing = [t for t in required if t not in data]
    if missing:
        raise RuntimeError('Missing required market/fixed/sector data: ' + ', '.join(missing))
    for ticker in required:
        frame = data[ticker]
        if frame.index[-1].strftime('%Y-%m-%d') != session:
            raise RuntimeError(f'Stale required ticker: {ticker}')
        if len(frame) < 200:
            raise RuntimeError(f'Insufficient weekly history for required ticker: {ticker}')


def select_analysis_data(data, cfg, required):
    """Apply the cheap first scanner layer before weekly Stage calculations."""
    selected = {ticker: data[ticker] for ticker in required if ticker in data}
    scanner = cfg.get('scanner', {})
    min_price = float(scanner.get('min_price', 5))
    min_history = int(scanner.get('min_history_days', 252))
    min_dollar = float(scanner.get('min_avg_dollar_volume_20d', 20_000_000))
    for ticker, frame in data.items():
        if ticker in selected or len(frame) < min_history or 'Close' not in frame or 'Volume' not in frame:
            continue
        close = pd.to_numeric(frame['Close'], errors='coerce')
        volume = pd.to_numeric(frame['Volume'], errors='coerce')
        price = float(close.iloc[-1])
        average_dollar = float((close * volume).tail(20).mean())
        if np.isfinite(price) and np.isfinite(average_dollar) and price > min_price and average_dollar >= min_dollar:
            selected[ticker] = frame
    return selected


def build_report(cfg, session, output, meta_path):
    from .report import create_report
    from .scanner import scan_market
    started = time.monotonic()
    client = DataClient(cfg)
    required = all_tickers(cfg)
    core = client.get_daily_bars(required, session)
    validate_required_data(core, required, session)
    LOGGER.info('market_ticker_count=%s core_downloaded=%s', len(required), len(core))
    core_snapshot = build_snapshot(core, cfg)
    regime, score = market_regime(core_snapshot, cfg)
    data = dict(core)
    universe, universe_stats = [], {'universe_count': 0, 'disabled': True}
    candidates, scanner_stats = [], {'disabled': True, 'final_count': 0}
    if cfg.get('scanner', {}).get('enabled', True):
        universe, universe_stats = load_universe(cfg, client)
        tickers = [item['ticker'] for item in universe if item['ticker'] not in data]
        # New listings are valid bars; history sufficiency is a scanner exclusion, not an API error.
        client.min_rows = 1
        if tickers:
            data.update(client.get_daily_bars(tickers, session))
        total = len(universe)
        successful = sum(item['ticker'] in data for item in universe)
        coverage = successful / total if total else 0
        if coverage < float(cfg['scanner'].get('min_download_coverage', .90)):
            raise RuntimeError(f'Scanner download coverage {successful}/{total} below configured minimum')
        # Apply cheap liquidity/history gates first; full weekly Stage work is
        # reserved for plausible stocks plus all mandatory report symbols.
        analysis_data = select_analysis_data(data, cfg, required)
        snapshot = build_snapshot(analysis_data, cfg)
        candidates, scanner_stats = scan_market(data, snapshot, universe, cfg, regime,
                                                sector_resolver=client.get_sector_etf)
        scanner_stats['download_coverage'] = coverage
    else:
        # Still enrich fixed-watchlist structures when scanning is explicitly disabled.
        snapshot = core_snapshot
        candidates, scanner_stats = scan_market(data, snapshot, [], cfg, regime,
                                                sector_resolver=client.get_sector_etf)
    scanner_stats['elapsed_seconds'] = round(time.monotonic() - started, 2)
    warnings = []
    if client.diagnostics.get('failed_tickers'):
        warnings.append(f"部分标的失败：{len(client.diagnostics['failed_tickers'])}；扫描覆盖见统计")
    if universe_stats.get('stale_cache'):
        warnings.append('股票目录使用有时限的备用缓存')
    if any(item.get('warning') for item in client.diagnostics.get('crosschecks', [])):
        warnings.append('核心指数不同数据源收盘价偏差超过阈值')
    client.diagnostics['final_providers'] = sorted(set(client.diagnostics.get('ticker_providers', {}).values()))
    completed_week = weekly_frame(core[cfg.get('rs', {}).get('benchmark', 'SPY')])
    diagnostics = {'data': client.diagnostics, 'universe': universe_stats,
                   'scanner': scanner_stats, 'warnings': warnings,
                   'weekly_asof': completed_week.index[-1].date().isoformat()
                   if not completed_week.empty else None}
    sector_rows = snapshot.reindex(list(cfg['sectors'])).dropna(subset=['price']).copy()
    rank = {'Stage 2 Early': 5, 'Stage 2': 4, 'Stage 2 continuation': 4,
            'Stage 2?': 3, 'Stage 1': 2, 'Stage 3': 1, 'Stage 4': 0}
    sector_rows['_rank'] = sector_rows['stage'].map(rank).fillna(0)
    sector_rows = sector_rows.sort_values(['_rank', 'rs20', 'rs60', 'ma30_slope'], ascending=False)
    meta = {'session': session, 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'regime': regime, 'regime_score': score,
            'market': {t: snapshot.loc[t].to_dict() for t in cfg['market'] + [cfg['risk']['vix']]},
            'fixed_watchlist': {t: snapshot.loc[t].to_dict() for t in cfg['watchlist']['fixed']},
            'top_sectors': [{'ticker': t, 'name': cfg['sectors'][t], **r.drop('_rank').to_dict()}
                            for t, r in sector_rows.head(5).iterrows()],
            'dynamic_candidates': candidates, 'diagnostics': diagnostics,
            'adjustment_basis': 'Provider-normalized adjusted OHLC; strategy uses completed NYSE weeks',
            'data_sources': dict(Counter(client.diagnostics.get('ticker_providers', {}).values()))}
    create_report(data=core, snapshot=snapshot, cfg=cfg, session=session, regime=regime,
                  regime_score=score, output=output, candidates=candidates, diagnostics=diagnostics)
    meta['image_sha256'] = hashlib.sha256(Path(output).read_bytes()).hexdigest()
    write_json(meta_path, meta)
    LOGGER.info('data_sources=%s fallback_count=%s universe=%s successful=%s failed=%s candidates=%s report_path=%s',
                meta['data_sources'], len(client.diagnostics.get('fallbacks', [])), len(universe),
                client.diagnostics.get('successful_count'), client.diagnostics.get('failed_count'),
                len(candidates), str(Path(output).resolve()))
    LOGGER.info('scan_statistics=%s', json.dumps(json_safe(scanner_stats), ensure_ascii=False))
    return json_safe(meta)


def cmd_prepare(args):
    cfg = load_config(args.config)
    session = latest_completed_nyse_session()
    sent = read_sent_session(args.state)
    if already_sent(session, sent):
        write_github_output(args.github_output, False, session, 'latest session already sent')
        LOGGER.info('SKIP session=%s reason=already_sent', session)
        return 0
    build_report(cfg, session, args.output, args.meta)
    write_github_output(args.github_output, True, session, 'new completed session')
    return 0


def _pct(value):
    return '-' if value is None else f'{value*100:+.1f}%'


def _meta_summary(meta):
    lines = [f"**NYSE Session：{meta['session']}**", '', f"市场状态：**{meta['regime']}**（规则状态，非预测）", '', '### 核心指数']
    for ticker, row in meta['market'].items():
        lines.append(f"- {ticker}：1D {_pct(row.get('r1'))} / 20D {_pct(row.get('r20'))} / {row.get('stage')}")
    lines += ['', '### 领先行业（Stage + RS + 均线）']
    for row in meta.get('top_sectors', []):
        lines.append(f"- {row['ticker']} {row['name']}：{row.get('stage')}，RS20 {_pct(row.get('rs20'))} / RS60 {_pct(row.get('rs60'))}")
    lines += ['', '### 固定观察池']
    for ticker, row in meta['fixed_watchlist'].items():
        lines.append(f"- {ticker}：{row.get('stage')} / {row.get('structure_status', '观察')}；RS20 {_pct(row.get('rs20'))}")
    lines += ['', '### 今日新增动态候选']
    for row in meta.get('dynamic_candidates', []):
        lines.append(f"- {row['ticker']}：{row.get('reason', '')}")
    if not meta.get('dynamic_candidates'):
        lines.append('今日无高质量新增候选。未同时满足市场、行业、Stage、RS、位置与 RR 条件。')
    lines += ['', '固定观察池与每日动态候选独立；日报采用复权行情和已完成周线。']
    return '\n'.join(lines)


def cmd_run_github(args):
    cfg = load_config(args.config)
    assets = GitHubAssets(os.environ.get('GITHUB_REPOSITORY', ''),
                          os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN', ''))
    sent = assets.load_state()
    session = latest_completed_nyse_session()
    if already_sent(session, sent):
        LOGGER.info('SKIP session=%s reason=already_sent', session)
        write_github_output(args.github_output, False, session, 'latest session already sent')
        return 0
    sendkey = os.environ.get('SERVERCHAN_SENDKEY', '').strip()
    if not sendkey:
        raise RuntimeError('Missing GitHub Actions Secret SERVERCHAN_SENDKEY')
    meta = build_report(cfg, session, args.output, args.meta)
    public = {}
    def publish():
        public['url'] = assets.publish(args.output, args.meta, session)
        return public['url']
    def send():
        return send_message(sendkey, f"美股日报 {session}｜{meta['regime']}",
                            _meta_summary(meta) + f"\n\n### PNG 日报\n\n![美股每日行情分析]({public['url']})\n\n[查看原图]({public['url']})")
    deliver(session, publish, lambda url: verify_public_image(url, meta['image_sha256']), send, assets.mark_sent)
    write_sent_session(args.state, session)
    write_github_output(args.github_output, True, session, 'ServerChan accepted and state persisted')
    write_json('reports/delivery.json', {'session': session, 'public_image_url': public['url'],
               'serverchan': 'accepted', 'sent_session': assets.last_sent,
               'wechat_device_receipt': 'requires user confirmation'})
    return 0


def parser():
    parser = argparse.ArgumentParser(description='NYSE daily report and safe ServerChan delivery')
    commands = parser.add_subparsers(dest='cmd', required=True)
    for name, action in [('prepare', cmd_prepare), ('run-github', cmd_run_github)]:
        sub = commands.add_parser(name)
        sub.add_argument('--config', default='config.yaml')
        sub.add_argument('--state', default='.state/sent_session.txt')
        sub.add_argument('--output', default='reports/latest.png')
        sub.add_argument('--meta', default='reports/latest.json')
        sub.add_argument('--github-output', default='reports/github_output.txt')
        sub.set_defaults(func=action)
    return parser


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)sZ %(levelname)s %(name)s %(message)s')
    logging.Formatter.converter = time.gmtime
    args = parser().parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        # Provider and transport errors are sanitized at source. Avoid printing arbitrary
        # library exception URLs or headers here; diagnostics contain safe error codes.
        LOGGER.error('Task failed type=%s; session is not advanced by failure', type(exc).__name__)
        if isinstance(exc, (RuntimeError, ValueError)):
            LOGGER.error('%s', str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
