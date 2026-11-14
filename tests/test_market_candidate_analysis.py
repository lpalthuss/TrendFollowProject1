import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from trendfollow import market_candidate_analysis as market_analysis
from trendfollow.candidate_universe import read_selection, selection_home
from trendfollow.operations import Operations


def test_saved_market_dates_are_analyzed_without_changing_account(tmp_path, monkeypatch):
    source = tmp_path / 'account-prices'
    source.mkdir()
    original = pd.DataFrame([
        dict(date=day, symbol=symbol, open=100, high=110, low=90, close=100, volume=1000)
        for day in ('2026-11-11', '2026-11-12') for symbol in ('000001', '069500')
    ])
    original.to_csv(source / 'prices.csv', index=False)
    original_bytes = (source / 'prices.csv').read_bytes()
    (tmp_path / 'config.json').write_text('{"rules": {}}', encoding='utf-8')
    for day, symbols in [('2026-11-11', ['000001']), ('2026-11-12', ['000001', '000002'])]:
        folder = selection_home(tmp_path) / day
        folder.mkdir(parents=True)
        pd.DataFrame([dict(symbol=s, name=s, market='KOSPI', sector='UNCLASSIFIED:'+s,
                           benchmark='069500', recent_turnover_proxy=2e9) for s in symbols]).to_csv(folder / 'universe.csv', index=False)
        pd.DataFrame(columns=['symbol', 'reason']).to_csv(folder / 'added.csv', index=False)
        pd.DataFrame(columns=['symbol', 'reason']).to_csv(folder / 'removed.csv', index=False)
        (folder / 'summary.json').write_text(json.dumps({'as_of': day, 'source': 'pykrx'}), encoding='utf-8')
    membership = tmp_path / 'data' / 'industries' / 'snapshots' / '2026-11-10'
    membership.mkdir(parents=True)
    pd.DataFrame([dict(symbol=s, sector='KOSPI:1', industry_name='테스트')
                  for s in ('000001', '000002')]).to_csv(membership / 'memberships.csv', index=False)
    (membership.parent.parent / 'peer-returns.csv').write_text('date,symbol,sector,day_return,benchmark_day_return,quote_eligible\n')
    monkeypatch.setattr(market_analysis, 'require_published_research', lambda _path: None)
    fetched = []
    class FakeSource:
        def __init__(self, _root): pass
        def daily(self, symbol, _start, _end):
            fetched.append(symbol)
            return pd.DataFrame([dict(date='2026-11-12', symbol=symbol, open=100,
                                      high=110, low=90, close=100, volume=1000)])
    monkeypatch.setattr(market_analysis, 'NaverResearchSource', FakeSource)
    def fake_features(prices, universe, _rules):
        day = prices.date.max()
        return pd.DataFrame([dict(date=pd.Timestamp(day), symbol=s, signal=False, close=100)
                             for s in universe.symbol]), pd.DataFrame()
    monkeypatch.setattr(market_analysis, 'features', fake_features)
    context = SimpleNamespace(root=tmp_path,
                              path=lambda value: tmp_path / value,
                              now=lambda: datetime.fromisoformat('2026-11-14T10:00:00+09:00'))
    workspace = dict(id='account', config='config.json', data_dir='account-prices', mode='normal')
    result = market_analysis.analyze_saved_selections(context, workspace, '2026-11-12', 'run1')
    assert result['analyzed_dates'] == ['2026-11-11', '2026-11-12']
    assert result['account_unchanged'] is True
    assert fetched == ['000002']
    assert read_selection(tmp_path, '2026-11-12')['members'][0]['benchmark'] == '069500'
    assert (source / 'prices.csv').read_bytes() == original_bytes
    assert pd.read_csv(market_analysis.latest_analysis(tmp_path, 'account', '2026-11-12') / 'signals.csv',
                       dtype={'symbol': str}).symbol.tolist() == ['000001', '000002']
    assert market_analysis.analyze_saved_selections(context, workspace, '2026-11-12', 'run2')['status'] == 'already_present'


def test_market_analysis_job_is_allowed_on_frozen_comparison_account(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(market_analysis, 'analyze_saved_selections',
                        lambda _context, _workspace, as_of, _key, _progress: captured.append(as_of) or {'analyzed_dates': [as_of]})
    market = tmp_path / 'data' / 'market'
    market.mkdir(parents=True)
    (market / 'universe.csv').write_text('symbol,benchmark\n000001,069500\n')
    ops = Operations(tmp_path, clock=lambda: datetime.fromisoformat('2026-11-14T10:00:00+09:00'))
    try:
        workspace = ops.create_workspace({'name': '비교 계좌', 'rules': {}, 'limits': {}})
        ops.update_workspace(dict(workspace, mode='recalculation'))
        job = ops.submit({'workspace': workspace['id'], 'action': 'market_analyze', 'as_of': '2026-11-13'})
        ops.pool.shutdown(wait=True)
        assert job['as_of'] == '2026-11-13'
        assert captured == ['2026-11-13']
    finally:
        ops.close()
