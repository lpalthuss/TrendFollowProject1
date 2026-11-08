import json

import pandas as pd
import pytest

from trendfollow.backtest import features
from trendfollow.demo import generate
from trendfollow.relative_strength import decision_rule_settings
from trendfollow.risk import Account,Limits,allocate
from trendfollow.stop_limit import apply_stop_limit,compare_stop_limit
from trendfollow.strategy import Rules,screen
from trendfollow.workflow import run_comparison


@pytest.fixture
def data(tmp_path):
    generate(tmp_path)
    p=pd.read_csv(tmp_path/'prices.csv',dtype={'symbol':str})
    u=pd.read_csv(tmp_path/'universe.csv',dtype={'symbol':str,'benchmark':str})
    return p,u,tmp_path


def widened(p,fraction=.82):
    p=p.copy()
    dates=sorted(p.date.unique())[-20:]
    mask=p.symbol.eq('DEMO01') & p.date.isin(dates)
    p.loc[mask,'low']=p.loc[mask,'close']*fraction
    return p


def test_wide_atr_is_excluded_only_when_cap_is_on_and_stop_is_identical(data):
    p,u,_=data;p=widened(p)
    on=screen(p,u,'2026-11-06',Rules()).set_index('symbol')
    off=screen(p,u,'2026-11-06',Rules(stop_limit_enabled=False)).set_index('symbol')
    assert on.loc['DEMO01','stop_fraction']>.15
    assert not on.loc['DEMO01','signal'] and off.loc['DEMO01','signal']
    assert on.loc['DEMO01','stop_price']==off.loc['DEMO01','stop_price']
    assert 'stop_limit_exceeded' in on.loc['DEMO01','reasons']
    pd.testing.assert_series_equal(on.atr,off.atr)
    for enabled,expected in [(True,on),(False,off)]:
        f,_=features(p,u,Rules(stop_limit_enabled=enabled))
        latest=f[f.date=='2026-11-06'].set_index('symbol')
        pd.testing.assert_series_equal(expected.signal.sort_index(),latest.signal.sort_index())


def test_disabled_cap_still_rejects_invalid_nonpositive_stops(data):
    p,u,_=data;p=widened(p,.01)
    f=screen(p,u,'2026-11-06',Rules(stop_limit_enabled=False)).set_index('symbol')
    assert f.loc['DEMO01','stop_price']<0 and not f.loc['DEMO01','signal']
    history,_=features(p,u,Rules(stop_limit_enabled=False))
    assert not history[(history.symbol=='DEMO01')&(history.date=='2026-11-06')].signal.iloc[0]


def test_exact_fifteen_percent_is_allowed_and_sixteen_percent_is_blocked():
    f=pd.DataFrame([dict(symbol=s,signal=True,signal_rs=True,signal_mansfield=True,
        reference_price=10000,stop_price=stop,reasons='') for s,stop in [('A',8500),('B',8400)]])
    assert apply_stop_limit(f,Rules()).signal.tolist()==[True,False]
    assert apply_stop_limit(f,Rules(stop_limit_enabled=False)).signal.tolist()==[True,True]


def test_wider_stop_reduces_size_instead_of_raising_account_risk(data):
    p,u,_=data
    rules=Rules(stop_limit_enabled=False)
    ordinary=screen(p,u,'2026-11-06',rules)
    wide=screen(widened(p),u,'2026-11-06',rules)
    limits=Limits(units=5,cost_rate=0,entry_slippage=0,exit_slippage=0)
    account=Account(10000000,10000000)
    narrow=allocate(ordinary,account,limits).set_index('symbol').loc['DEMO01']
    broad=allocate(wide,account,limits).set_index('symbol').loc['DEMO01']
    assert 0<broad.quantity<narrow.quantity
    assert broad.planned_risk<=account.equity*limits.max_trade_risk


def test_comparison_changes_only_cap_setting_and_allocates_each_account_independently(data):
    p,u,root=data;p=widened(p);p.to_csv(root/'prices.csv',index=False)
    summary=compare_stop_limit(root/'prices.csv',root/'universe.csv',root/'account.json','2026-11-06',None,root/'out')
    assert summary['newly_eligible_without_limit']>=1
    left=dict(summary['rules_on']);right=dict(summary['rules_off'])
    assert left.pop('stop_limit_enabled') is True and right.pop('stop_limit_enabled') is False
    assert left==right
    compared=pd.read_csv(root/'out'/'comparison.csv').set_index('symbol')
    assert compared.loc['DEMO01','quantity_on']==0 and compared.loc['DEMO01','quantity_off']>0
    with pytest.raises(FileExistsError):
        compare_stop_limit(root/'prices.csv',root/'universe.csv',root/'account.json','2026-11-06',None,root/'out')


def test_backtest_override_uses_same_config_but_changes_cap(data):
    p,u,root=data
    config=root/'config.json';config.write_text(json.dumps({'rules':{'stop_limit_enabled':True}}))
    summary=run_comparison(root/'prices.csv',root/'universe.csv','2026-09-01','2026-10-01',
        '2026-11-06',root/'backtest',config_path=config,stop_limit_enabled=False)
    assert summary['rules']['stop_limit_enabled'] is False
    assert json.loads(config.read_text())['rules']['stop_limit_enabled'] is True
    assert len(summary['results'])==4


def test_default_preserves_existing_configuration_fingerprint():
    assert 'stop_limit_enabled' not in decision_rule_settings(Rules())
    assert 'stop_limit_enabled' not in decision_rule_settings(Rules(stop_limit_enabled=True))
    assert decision_rule_settings(Rules())!=decision_rule_settings(Rules(stop_limit_enabled=False))
    with pytest.raises(ValueError):Rules(stop_limit_enabled=1).validate()


def test_intraday_plan_uses_same_optional_cap(tmp_path):
    from trendfollow.intraday import prepare_plan
    days=pd.bdate_range('2026-10-30',periods=6)
    p=pd.DataFrame([dict(date=str(d.date()),symbol='000001',open=100,high=101,low=99,close=100,volume=v)
                    for d,v in zip(days,[100,100,100,100,10,10])])
    p.to_csv(tmp_path/'p.csv',index=False)
    row=dict(date='2026-11-06',symbol='000001',name='Alpha',benchmark='069500',sector='S',
        rs_method='percentile',entry_method='setup',weekly_trend=True,eligible_history=True,
        avg_turnover=1e10,stop_price=80,reference_price=100,rs_percentile=99,excess_return=.1,
        setup_phase='base',setup_peak_date='2026-10-30',setup_resistance=110,setup_wave_low=80)
    pd.DataFrame([row]).to_csv(tmp_path/'s.csv',index=False)
    history=tmp_path/'peers.csv'
    pd.DataFrame([dict(date='2026-11-06',symbol=s,sector='S',day_return=.01,benchmark_day_return=0,
        quote_eligible=1,market='KOSPI',benchmark='069500') for s in ['000001','000002','000003','000004']]).to_csv(history,index=False)
    pd.DataFrame([dict(symbol='000001',benchmark='069500',sector='S',industry_peer_history=str(history))]).to_csv(tmp_path/'u.csv',index=False)
    results=[]
    for enabled in [True,False]:
        config=tmp_path/f'{enabled}.json'
        config.write_text(json.dumps({'rules':{'entry_method':'setup','setup_range_contraction':1.,'stop_limit_enabled':enabled}}))
        plan=prepare_plan(tmp_path/'s.csv',tmp_path/'p.csv',tmp_path/'u.csv','2026-11-09',
            ['000001'],config,tmp_path/f'plan-{enabled}.json')
        results.append(plan['targets']['000001'])
    assert 'invalid_stop' in results[0]['prior_filter_reasons']
    assert results[1]['prior_filters_pass'] is True
