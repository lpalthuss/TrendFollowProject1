from dataclasses import asdict, replace
import json

import numpy as np
import pandas as pd
import pytest

from trendfollow.backtest import features
from trendfollow.cli import main
from trendfollow.relative_strength import decision_rule_settings, mansfield_weekly, multi_period_daily_rs
from trendfollow.operations.queries import _saved_multi_rs


def test_multi_period_daily_rs_is_display_only_and_reports_available_windows():
    days = pd.bdate_range('2026-01-01', periods=250)
    kospi = pd.Series(100., index=days)
    stock = pd.Series(np.linspace(100., 150., len(days)), index=days)
    result = multi_period_daily_rs(stock, kospi)
    assert result['multi_rs_windows'] == '60/120/250'
    assert result['multi_rs'] > 50
    assert result['multi_rs_as_of'] == days[-1]
    short = multi_period_daily_rs(stock.iloc[:80], kospi.iloc[:80])
    assert short['multi_rs_windows'] == '60'
    assert multi_period_daily_rs(stock.iloc[:50], kospi.iloc[:50])['multi_rs'] is None


def test_saved_multi_rs_uses_only_prices_through_selected_day(tmp_path):
    days = pd.bdate_range('2026-01-01', periods=62)
    prices = pd.DataFrame([
        {'date': day.date().isoformat(), 'symbol': symbol, 'close': value}
        for day in days for symbol, value in [('KOSPI', 100), ('000001', 100 if day != days[-1] else 200)]
    ])
    path = tmp_path / 'prices.csv'
    prices.to_csv(path, index=False)
    stat = path.stat()
    earlier = _saved_multi_rs(str(path), stat.st_mtime_ns, stat.st_size, days[-2].date().isoformat())
    later = _saved_multi_rs(str(path), stat.st_mtime_ns, stat.st_size, days[-1].date().isoformat())
    assert earlier['000001']['multi_rs'] == 50
    assert later['000001']['multi_rs'] > 50
    assert earlier['000001']['multi_rs_as_of'] == days[-2].date().isoformat()
from trendfollow.strategy import Rules, screen


def long_market():
    dates=pd.bdate_range(end='2026-11-06',periods=320)
    rows=[]
    for symbol,growth in [('A',.0015),('B',.002),('INDEX',.0003)]:
        values=100*np.exp(np.arange(len(dates))*growth)
        if symbol!='INDEX':
            values[-1]*=1.05
        for i,(day,value) in enumerate(zip(dates,values)):
            rows.append(dict(date=day.strftime('%Y-%m-%d'),symbol=symbol,open=value*.998,
                high=value*1.005,low=value*.995,close=value,
                volume=3000000 if i==len(dates)-1 else 1000000))
    universe=pd.DataFrame([dict(symbol=s,name=s,sector='SECTOR',benchmark='INDEX') for s in ['A','B']])
    return pd.DataFrame(rows),universe


def test_mansfield_formula_includes_current_week_in_average():
    days=pd.date_range('2025-01-03',periods=52,freq='W-FRI')
    stock=pd.Series([100.]*51+[200.],index=days)
    benchmark=pd.Series(100.,index=days)
    result=mansfield_weekly(stock,benchmark)
    assert result.mansfield_rs.iloc[:-1].isna().all()
    assert result.mansfield_rs.iloc[-1]==pytest.approx(96.22641509433961)
    assert result.relative_price_sma.iloc[-1]==pytest.approx(53/52)


def test_mansfield_flat_relative_price_is_zero_and_negative_is_possible():
    days=pd.date_range('2025-01-03',periods=53,freq='W-FRI')
    benchmark=pd.Series(100.,index=days)
    flat=mansfield_weekly(pd.Series(100.,index=days),benchmark)
    assert flat.mansfield_rs.iloc[-1]==0
    assert flat.mansfield_change_1w.iloc[-1]==0
    weak=pd.Series([100.]*52+[80.],index=days)
    assert mansfield_weekly(weak,benchmark).mansfield_rs.iloc[-1]<0


def test_missing_final_stock_session_cannot_use_earlier_weekday():
    days=pd.bdate_range('2026-01-05',periods=15)
    stock=pd.Series(100.,index=days[:-1])
    benchmark=pd.Series(100.,index=days)
    result=mansfield_weekly(stock,benchmark,weeks=3)
    assert pd.isna(result.relative_price.iloc[-1])
    assert pd.isna(result.mansfield_rs.iloc[-1])


def test_partial_week_never_uses_future_friday():
    prices,universe=long_market()
    old=features(prices,universe,Rules(min_turnover=1))[0]
    assert old.mansfield_rs.notna().any()
    changed=prices.copy()
    changed.loc[changed.date=='2026-11-06',['open','high','low','close']]*=3
    after=features(changed,universe,Rules(min_turnover=1))[0]
    cutoff=old.date<='2026-11-05'
    pd.testing.assert_frame_equal(old[cutoff].reset_index(drop=True),
                                 after[after.date<='2026-11-05'].reset_index(drop=True))


def test_separate_signal_sets_and_screen_backtest_parity():
    prices,universe=long_market()
    rules=Rules(min_turnover=1)
    ranked=screen(prices,universe,'2026-11-06',rules)
    mrs=screen(prices,universe,'2026-11-06',replace(rules,rs_method='mansfield'))
    assert ranked.signal.sum()==1
    assert mrs.signal.sum()==2
    for mode in ['percentile','mansfield']:
        settings=replace(rules,rs_method=mode)
        current=screen(prices,universe,'2026-11-06',settings).set_index('symbol').sort_index()
        prepared=features(prices,universe,settings)[0]
        end=prepared[prepared.date=='2026-11-06'].set_index('symbol').sort_index()
        for col in ['mansfield_rs','mansfield_change_1w','signal_rs','signal_mansfield','signal','allocation_score']:
            pd.testing.assert_series_equal(current[col],end[col],check_names=True)


def test_short_history_does_not_disable_existing_rs_but_explains_missing_mansfield():
    from trendfollow.demo import generate
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);generate(root)
        prices=pd.read_csv(root/'prices.csv')
        universe=pd.read_csv(root/'universe.csv')
        ranked=screen(prices,universe,'2026-11-06')
        mrs=screen(prices,universe,'2026-11-06',Rules(rs_method='mansfield'))
    assert ranked.signal.sum()==2
    assert ranked.mansfield_rs.isna().all()
    assert not mrs.signal.any()
    assert mrs.reasons.str.contains('insufficient_mansfield_history').all()


def test_paper_fingerprint_stays_compatible_for_indicator_only_additions():
    rules=Rules()
    old={key:asdict(rules)[key] for key in ['rs_days','breakout_days','weekly_ma','weekly_slope',
        'min_rs','volume_multiple','min_turnover','atr_days','stop_atr','max_stop_fraction']}
    assert decision_rule_settings(rules)==old
    assert decision_rule_settings(replace(rules,mansfield_weeks=26))==old
    assert decision_rule_settings(replace(rules,rs_method='mansfield'))!=old


def test_comparison_cli_keeps_allocations_separate(tmp_path):
    prices,universe=long_market()
    prices.to_csv(tmp_path/'prices.csv',index=False)
    universe.to_csv(tmp_path/'universe.csv',index=False)
    (tmp_path/'account.json').write_text(json.dumps({'equity':10000000,'cash':10000000}),encoding='utf-8')
    (tmp_path/'config.json').write_text(json.dumps({'rules':{'min_turnover':1}}),encoding='utf-8')
    main(['compare-rs','--prices',str(tmp_path/'prices.csv'),'--universe',str(tmp_path/'universe.csv'),
          '--account',str(tmp_path/'account.json'),'--as-of','2026-11-06',
          '--config',str(tmp_path/'config.json'),'--out',str(tmp_path/'out')])
    result=pd.read_csv(tmp_path/'out/comparison.csv')
    assert {'quantity_rs','quantity_mansfield','signal_rs','signal_mansfield','mansfield_rs'}.issubset(result.columns)
    assert 'signal' not in result
    report=json.loads((tmp_path/'out/comparison.json').read_text(encoding='utf-8'))
    assert report['selected_method'] is None
    assert report['rs_candidates']==1 and report['mansfield_candidates']==2


def test_invalid_rs_settings_rejected():
    for rules in [Rules(rs_method='unknown'),Rules(mansfield_weeks=0),Rules(min_mansfield=float('nan'))]:
        with pytest.raises(ValueError):
            rules.validate()


def test_mansfield_allocation_uses_its_own_score():
    from trendfollow.risk import Account, Limits, allocate
    candidates=pd.DataFrame([
        dict(symbol='A',sector='S',signal=True,reference_price=100.,stop_price=90.,
             rs_percentile=100.,allocation_score=1.,avg_turnover=1e9),
        dict(symbol='B',sector='S',signal=True,reference_price=100.,stop_price=90.,
             rs_percentile=50.,allocation_score=20.,avg_turnover=1e9)])
    limits=Limits(total_risk=.002,cash_reserve=0,cost_rate=0,entry_slippage=0,exit_slippage=0)
    result=allocate(candidates,Account(10000,10000),limits).set_index('symbol')
    assert result.loc['B','quantity']==2
    assert result.loc['A','quantity']==0


def test_changing_method_cannot_reuse_existing_paper_account(tmp_path):
    from trendfollow.demo import generate
    from trendfollow.workflow import paper_checkpoint
    generate(tmp_path)
    state=tmp_path/'paper/state.json'
    paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state)
    previous=state.read_bytes()
    config=tmp_path/'mansfield.json'
    config.write_text(json.dumps({'rules':{'rs_method':'mansfield'}}),encoding='utf-8')
    with pytest.raises(ValueError,match='설정·유니버스가 변경'):
        paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state,config_path=config)
    assert state.read_bytes()==previous


def market_index_inputs():
    prices,u=long_market()
    u['market']=['KOSPI','KOSDAQ']
    benchmark=prices[prices.symbol=='INDEX']
    additions=[]
    for name,rate in [('KOSPI',.004),('KOSDAQ',.0001)]:
        f=benchmark.copy()
        factor=np.exp(np.arange(len(f))*(rate-.0003))
        f[['open','high','low','close']]=f[['open','high','low','close']].mul(factor,axis=0)
        f['symbol']=name
        additions.append(f)
    return pd.concat([prices,*additions],ignore_index=True),u


def test_index_selection_only_changes_mansfield_and_maps_each_market():
    prices,u=market_index_inputs()
    base=Rules(min_turnover=1)
    etf=screen(prices,u,'2026-11-06',base).set_index('symbol').sort_index()
    index=screen(prices,u,'2026-11-06',replace(base,mansfield_benchmark='market_index')).set_index('symbol').sort_index()
    pd.testing.assert_series_equal(etf.rs_percentile,index.rs_percentile)
    pd.testing.assert_series_equal(etf.excess_return,index.excess_return)
    pd.testing.assert_series_equal(etf.signal_rs,index.signal_rs)
    assert index.loc['A','mansfield_benchmark']=='KOSPI'
    assert index.loc['B','mansfield_benchmark']=='KOSDAQ'
    assert etf.loc['A','mansfield_rs']>0>index.loc['A','mansfield_rs']
    assert index.loc['B','mansfield_rs']>0
    rules=replace(base,rs_method='mansfield',mansfield_benchmark='market_index')
    current=screen(prices,u,'2026-11-06',rules).set_index('symbol').sort_index()
    computed=features(prices,u,rules)[0]
    latest=computed[computed.date=='2026-11-06'].set_index('symbol').sort_index()
    for column in ['mansfield_rs','signal','mansfield_benchmark']:
        pd.testing.assert_series_equal(current[column],latest[column])


def test_index_mode_requires_market_and_index_data_without_etf_fallback():
    prices,u=long_market()
    rules=Rules(mansfield_benchmark='market_index')
    with pytest.raises(ValueError,match='market'):
        screen(prices,u,'2026-11-06',rules)
    u['market']='KOSPI'
    with pytest.raises(ValueError,match='지수 일봉이 없습니다'):
        screen(prices,u,'2026-11-06',rules)
    with pytest.raises(ValueError,match='지수 일봉이 없습니다'):
        features(prices,u,rules)


def test_naver_index_cache_is_parsed_as_index_points_without_network(tmp_path,monkeypatch):
    import requests
    from trendfollow.market import NaverResearchSource
    (tmp_path/'KOSPI-2026-11-06.xml').write_text(
        '<chartdata><item data="20261106|5263.26|5269.10|4911.28|5059.69|323267" /></chartdata>',encoding='utf-8')
    def forbidden(*a,**k):
        pytest.fail('cached index must not use network')
    monkeypatch.setattr(requests,'get',forbidden)
    result=NaverResearchSource(tmp_path).daily('KOSPI','2026-11-06','2026-11-06')
    assert result.close.iloc[0]==pytest.approx(5059.69)
    assert result.symbol.iloc[0]=='KOSPI'


def test_index_rows_do_not_reset_percentile_paper_account_digest():
    from trendfollow.workflow import paper_input_digest
    prices,u=market_index_inputs()
    stocks=prices[~prices.symbol.isin(['KOSPI','KOSDAQ'])]
    base=Rules()
    index=replace(base,mansfield_benchmark='market_index')
    assert paper_input_digest(stocks,u,base)==paper_input_digest(prices,u,index)
    selected=replace(index,rs_method='mansfield')
    assert paper_input_digest(stocks,u,base)!=paper_input_digest(prices,u,selected)


def test_index_data_command_preserves_original_file(tmp_path,monkeypatch):
    from trendfollow.market import NaverResearchSource
    prices,u=market_index_inputs()
    original=prices[~prices.symbol.isin(['KOSPI','KOSDAQ'])]
    path=tmp_path/'stock.csv';original.to_csv(path,index=False)
    before=path.read_bytes()
    def fake(self,symbol,start,end,refresh=False):
        return prices[prices.symbol==symbol].copy()
    monkeypatch.setattr(NaverResearchSource,'daily',fake)
    out=tmp_path/'with-indices.csv'
    main(['index-data','--prices',str(path),'--out',str(out),'--end','2026-11-06'])
    result=pd.read_csv(out)
    assert {'KOSPI','KOSDAQ'}.issubset(set(result.symbol))
    assert path.read_bytes()==before
    metadata=json.loads(out.with_suffix('.indices.json').read_text(encoding='utf-8'))
    assert metadata['price_unit']=='index points'


def test_missing_index_friday_is_not_replaced_by_thursday():
    prices,u=market_index_inputs()
    prices=prices[~((prices.symbol=='KOSPI')&(prices.date=='2026-10-30'))]
    rules=Rules(min_turnover=1,rs_method='mansfield',mansfield_benchmark='market_index')
    current=screen(prices,u,'2026-11-06',rules).set_index('symbol')
    assert pd.isna(current.loc['A','mansfield_rs'])
    prepared=features(prices,u,rules)[0]
    latest=prepared[(prepared.symbol=='A')&(prepared.date=='2026-11-06')]
    assert latest.mansfield_rs.isna().all()
