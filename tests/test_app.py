import copy
from argparse import Namespace
from unittest.mock import Mock
import pytest
import numpy as np
import pandas as pd
import yaml
from src import app

def test_config_fixed_is_exact_and_not_universe():
    cfg = app.load_config('config.yaml')
    original = copy.deepcopy(cfg)
    assert cfg['watchlist']['fixed'] == ['TEM', 'RXRX', 'ENPH']
    tickers = app.all_tickers(cfg)
    assert all(t in tickers for t in ['SPY', 'QQQ', 'IWM', 'TEM', 'RXRX', 'ENPH'])
    assert not any(t in tickers for t in ['AVGO', 'MRVL', 'CRCL', 'PLTR', 'SMCI', 'BE', 'ABCL'])
    assert cfg == original

def test_duplicate_session_skips_before_network(tmp_path, monkeypatch):
    state = tmp_path / 'state.txt'
    state.write_text('2026-09-18\n')
    monkeypatch.setattr(app, 'latest_completed_nyse_session', lambda: '2026-09-18')
    client = Mock(side_effect=AssertionError('Must not fetch'))
    monkeypatch.setattr(app, 'DataClient', client)
    args = Namespace(config='config.yaml', state=str(state), output=str(tmp_path/'a.png'),
                     meta=str(tmp_path/'a.json'), github_output=str(tmp_path/'output.txt'))
    assert app.cmd_prepare(args) == 0
    assert 'should_send=false' in (tmp_path/'output.txt').read_text()
    client.assert_not_called()

def test_future_marker_fails_closed():
    with pytest.raises(RuntimeError):
        app.already_sent('2026-09-18', '2026-09-21')

def test_missing_required_quote_fails():
    with pytest.raises(RuntimeError, match='QQQ'):
        app.validate_required_data({'SPY': object()}, ['SPY', 'QQQ'], '2026-09-18')

def test_json_sanitizes_missing_metrics():
    assert app.json_safe({'a': float('nan'), 'b': float('inf')}) == {'a': None, 'b': None}


def test_stage_analysis_prefilters_liquidity_but_preserves_core():
    dates = pd.bdate_range('2025-01-01', periods=260)
    def bars(price, volume):
        close = np.full(len(dates), price)
        return pd.DataFrame({'Open': close, 'High': close, 'Low': close,
                             'Close': close, 'Volume': volume}, index=dates)
    cfg = app.load_config('config.yaml')
    data = {'SPY': bars(2, 1), 'LIQUID': bars(20, 2_000_000),
            'PENNY': bars(2, 50_000_000), 'THIN': bars(20, 1_000)}
    selected = app.select_analysis_data(data, cfg, ['SPY'])
    assert set(selected) == {'SPY', 'LIQUID'}
