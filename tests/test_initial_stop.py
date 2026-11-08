from dataclasses import replace
import json

import pandas as pd
import pytest

from trendfollow.backtest import features,simulate
from trendfollow.demo import generate
from trendfollow.initial_stop import initial_stop,planning_stops,compare_initial_stops
from trendfollow.relative_strength import decision_rule_settings
from trendfollow.risk import Account,Limits,allocate
from trendfollow.strategy import Rules,screen


@pytest.mark.parametrize('atr,method,stop,branch',[
    (200,'fixed',9200,'fixed'),(200,'atr',9600,'atr'),(200,'hybrid',9200,'fixed'),
    (600,'fixed',9200,'fixed'),(600,'atr',8800,'atr'),(600,'hybrid',8800,'atr'),
    (400,'hybrid',9200,'fixed')])
def test_initial_stop_formulas_and_switch(atr,method,stop,branch):
    result=initial_stop(10000,atr,method=method)
    assert result['stop_price']==stop and result['initial_stop_branch']==branch


@pytest.mark.parametrize('kwargs',[{'method':'typo'},{'price':0},{'atr':float('nan')},
    {'multiplier':0},{'fixed_fraction':1}])
def test_invalid_stop_inputs(kwargs):
    args=dict(price=10000,atr=200);args.update(kwargs)
    with pytest.raises(ValueError):initial_stop(**args)


def candidate(method='hybrid',atr=600,cap=False):
    rules=Rules(initial_stop_method=method,initial_stop_reference='entry_fill',stop_limit_enabled=cap)
    f=planning_stops(pd.DataFrame([dict(symbol='A',sector='S',signal=True,rs_percentile=99,
        reference_price=10000,atr=atr,avg_turnover=1e12)]),rules)
    f['stop_limit_enabled']=cap
    return f


def test_fill_anchor_and_exact_fee_risk_budget_reduce_quantity_for_wide_atr():
    account=Account(10000000,10000000)
    limits=Limits(units=5)
    results=[allocate(candidate(atr=a),account,limits).iloc[0] for a in [200,600]]
    for row in results:
        fill=10000*(1+limits.entry_slippage)
        assert row.estimated_entry_price==fill
        assert row.stop_price==pytest.approx(fill-max(fill*.08,2*row.atr))
        risk=fill*(1+limits.cost_rate/2)-row.stop_price*(1-limits.exit_slippage)*(1-limits.cost_rate/2)
        assert row.planned_risk==pytest.approx(row.quantity*risk)
        assert row.planned_risk<=account.equity*limits.max_trade_risk
    assert 0<results[1].quantity<results[0].quantity


def test_fill_rechecks_cap_and_held_stock_cannot_be_added():
    f=candidate(atr=800,cap=True)
    on=allocate(f,Account(1000000,1000000)).iloc[0]
    assert on.quantity==0 and on.allocation_reason=='blocked_stop_limit_at_entry'
    f['stop_limit_enabled']=False
    assert allocate(f,Account(1000000,1000000)).quantity.iloc[0]>0
    held=Account(1000000,990000,[dict(symbol='A',sector='S',quantity=1,current_price=10000,stop_price=9000)])
    row=allocate(f,held).iloc[0]
    assert row.quantity==0 and row.allocation_reason=='already_held'


def simulation_inputs(method='hybrid'):
    dates=pd.bdate_range('2026-01-05',periods=4)
    p=pd.DataFrame([dict(date=d,symbol=s,open=price,high=price+100,low=price-100,close=price,volume=100000)
        for i,d in enumerate(dates) for s,price in [('A',10000 if i==0 else 10200),('B',10000)]])
    u=pd.DataFrame([dict(symbol='A',sector='S',benchmark='B')])
    rules=Rules(initial_stop_method=method,initial_stop_reference='entry_fill',stop_limit_enabled=False)
    f=planning_stops(pd.DataFrame([dict(date=d,symbol='A',sector='S',reference_price=10000,
        atr=600 if i==0 else 2000,signal=i==0,rs_percentile=99,avg_turnover=1e12,
        trailing_channel=9500 if i==1 else 7000) for i,d in enumerate(dates)]),rules)
    f['stop_limit_enabled']=False
    return p,u,f


@pytest.mark.parametrize('method',['fixed','atr','hybrid'])
def test_next_open_finalization_frozen_atr_no_widening_and_resume(method):
    p,u,f=simulation_inputs(method)
    limits=Limits(units=5)
    def run(start,end,state=None):
        return simulate(p,f,u,start,end,10000000,'fixed',limits,initial_state=state)
    full=run('2026-01-05','2026-01-08')
    pos=full[4]['positions']['A']
    expected=initial_stop(10200*(1+limits.entry_slippage),600,method=method)
    assert pos['initial_stop_price']==expected['stop_price']
    assert pos['initial_stop_method']==method and pos['signal_atr']==600
    assert pos['stop_price']>=pos['initial_stop_price']
    assert pos['stop_price']==max(9500,pos['initial_stop_price'])
    assert pos['initial_risk']<=100000
    assert full[2].iloc[0].initial_stop_price==expected['stop_price']
    before=run('2026-01-05','2026-01-05')
    # JSON checkpoint must carry the signal-day ATR and policy, not entry-day ATR.
    restored=json.loads(json.dumps(before[4]))
    resumed=run('2026-01-06','2026-01-08',restored)
    assert resumed[4]==full[4]
    pd.testing.assert_frame_equal(resumed[2],full[2])
    changed=f.copy();changed.loc[changed.date>pd.Timestamp('2026-01-05'),'atr']=9000
    altered=simulate(p,changed,u,'2026-01-05','2026-01-08',10000000,'fixed',limits)
    assert altered[4]==full[4]


def test_three_methods_share_indicators_and_comparison_inputs(tmp_path):
    generate(tmp_path)
    p=pd.read_csv(tmp_path/'prices.csv',dtype={'symbol':str})
    u=pd.read_csv(tmp_path/'universe.csv',dtype={'symbol':str,'benchmark':str})
    scans=[]
    for method in ['fixed','atr','hybrid']:
        rules=Rules(initial_stop_method=method,initial_stop_reference='entry_fill')
        scan=screen(p,u,'2026-11-06',rules).set_index('symbol')
        f,_=features(p,u,rules)
        latest=f[f.date=='2026-11-06'].set_index('symbol')
        for col in ['stop_price','signal']:
            pd.testing.assert_series_equal(scan[col].sort_index(),latest[col].sort_index())
        scans.append(scan)
    for f in scans[1:]:
        for col in ['atr','rs_percentile','mansfield_rs']:
            pd.testing.assert_series_equal(scans[0][col],f[col])
    summary=compare_initial_stops(tmp_path/'prices.csv',tmp_path/'universe.csv',tmp_path/'account.json',
        '2026-11-06',None,tmp_path/'out')
    settings=list(summary['rules'].values())
    assert all({k:v for k,v in r.items() if k!='initial_stop_method'}==
        {k:v for k,v in settings[0].items() if k!='initial_stop_method'} for r in settings)
    assert (tmp_path/'out'/'comparison.csv').is_file()
    with pytest.raises(FileExistsError):
        compare_initial_stops(tmp_path/'prices.csv',tmp_path/'universe.csv',tmp_path/'account.json',
            '2026-11-06',None,tmp_path/'out')


def test_legacy_fingerprint_and_explicit_new_policy():
    base=decision_rule_settings(Rules())
    assert not any(k.startswith('initial_stop_') for k in base)
    assert 'fixed_stop_fraction' not in base
    assert base==decision_rule_settings(Rules(fixed_stop_fraction=.10))
    assert base!=decision_rule_settings(Rules(initial_stop_reference='entry_fill'))
    with pytest.raises(ValueError):Rules(initial_stop_method='hybrid').validate()


def test_backtest_override_is_explicit_and_does_not_modify_config(tmp_path):
    from trendfollow.workflow import run_comparison
    generate(tmp_path)
    config=tmp_path/'config.json';config.write_text('{}')
    result=run_comparison(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-09-01','2026-10-01',
        '2026-11-06',tmp_path/'backtest',config_path=config,initial_stop_method='hybrid')
    assert result['rules']['initial_stop_method']=='hybrid'
    assert result['rules']['initial_stop_reference']=='entry_fill'
    assert config.read_text()=='{}'
