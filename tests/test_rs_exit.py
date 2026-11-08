from dataclasses import replace
import copy

import numpy as np
import pandas as pd
import pytest

from trendfollow.backtest import features,simulate
from trendfollow.relative_strength import decision_rule_settings
from trendfollow.risk import Limits
from trendfollow.rs_exit import exit_columns
from trendfollow.strategy import Rules,screen


def inputs(values=(49,48,47,60),dates=None,rules=None):
    dates=pd.DatetimeIndex(dates) if dates is not None else pd.bdate_range('2026-01-05',periods=len(values))
    p=[]
    for i,date in enumerate(dates):
        for symbol,price in [('A',101+i*2),('B',100)]:
            p.append(dict(date=date,symbol=symbol,open=price,high=price+2,low=price-1,close=price+1,volume=10000))
    u=pd.DataFrame([dict(symbol='A',name='Alpha',sector='S',benchmark='B')])
    f=pd.DataFrame([dict(date=d,symbol='A',sector='S',signal=False,reference_price=100,
        stop_price=80,avg_turnover=1e9,trailing_channel=70,rs_percentile=value,
        mansfield_rs=value,mansfield_observation_date=d) for d,value in zip(dates,values)])
    rules=rules or Rules(rs_exit_enabled=True)
    f=exit_columns(f,rules)
    state={'cash':99000,'positions':{'A':dict(sector='S',quantity=10,entry_date='2026-01-01',
        entry_price=100,entry_total=1000,initial_risk=200,stop_price=80,target_3r=200,
        last_price=100,success=False,exit_pending=False)},'units':3,'pending':[],
        'initial_cash':100000,'last_date':'2026-01-02','mode':'progressive'}
    return pd.DataFrame(p),u,f,state


LIMITS=Limits(cost_rate=0,entry_slippage=0,exit_slippage=0)


def run(p,u,f,state,start=None,end=None):
    return simulate(p,f,u,start or str(p.date.min().date()),end or str(p.date.max().date()),
                    100000,'progressive',LIMITS,initial_state=copy.deepcopy(state))


def test_two_weak_closes_sell_at_next_open_not_trigger_close():
    p,u,f,state=inputs()
    curve,trades,orders,events,final=run(p,u,f,state)
    t=trades.iloc[0]
    assert t.exit_date=='2026-01-07' and t.exit_price==105
    assert t.exit_reason=='rs_weakness_percentile' and t.rs_exit_signal_date=='2026-01-06'
    assert t.rs_exit_indicator==48 and t.rs_exit_threshold==50
    assert curve.positions.tolist()==[1,1,0,0]
    assert (events.kind=='rs_exit_scheduled').sum()==1 and final['positions']=={}


@pytest.mark.parametrize('middle',[np.nan,50,80])
def test_missing_or_not_weak_observation_breaks_consecutive_streak(middle):
    p,u,f,state=inputs((49,middle,48,47,70))
    result=run(p,u,f,state)
    assert result[1].iloc[0].rs_exit_signal_date=='2026-01-08'
    assert result[1].iloc[0].exit_date=='2026-01-09'


def test_weekly_mansfield_counts_distinct_completed_weeks_not_daily_carries():
    dates=pd.bdate_range('2026-01-05','2026-01-20')
    rules=Rules(rs_method='mansfield',rs_exit_enabled=True)
    p,u,f,state=inputs([-1]*len(dates),dates,rules)
    f['mansfield_observation_date']=pd.Series(pd.date_range('2026-01-02','2026-01-23',freq='W-FRI'),
        index=pd.date_range('2026-01-02','2026-01-23',freq='W-FRI')).reindex(dates,method='ffill').to_numpy()
    f=exit_columns(f,rules)
    state['positions']['A']['entry_date']='2026-01-05'
    state['positions']['A']['target_3r']=1000
    result=run(p,u,f,state)
    assert result[1].iloc[0].rs_exit_signal_date=='2026-01-16'
    assert result[1].iloc[0].exit_date=='2026-01-19'
    assert result[1].iloc[0].exit_reason=='rs_weakness_mansfield'


def test_resume_preserves_counts_and_already_confirmed_exit():
    p,u,f,state=inputs()
    full=run(p,u,f,state)
    first=run(p,u,f,state,end='2026-01-05')
    assert first[4]['positions']['A']['rs_weak_count']==1
    resumed=run(p,u,f,first[4],start='2026-01-06')
    assert resumed[4]==full[4]
    scheduled=run(p,u,f,state,end='2026-01-06')
    assert scheduled[1].empty and scheduled[4]['positions']['A']['rs_exit_pending']['signal_date']=='2026-01-06'
    filled=run(p,u,f,scheduled[4],start='2026-01-07')
    pd.testing.assert_frame_equal(filled[1],full[1])


@pytest.mark.parametrize('kind',['locked','missing','zero_volume'])
def test_unfillable_exit_is_latched_until_available_even_after_strength_recovers(kind):
    p,u,f,state=inputs((49,48,90,90))
    mask=(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-07'))
    if kind=='locked':
        for col in ['open','high','low','close']:p.loc[mask,col]=105
    elif kind=='missing':p=p[~mask]
    else:p.loc[mask,'volume']=0
    result=run(p,u,f,state)
    t=result[1].iloc[0]
    assert t.exit_date=='2026-01-08' and t.exit_reason=='rs_weakness_percentile'
    assert t.rs_exit_signal_date=='2026-01-06'
    assert (result[2].status=='deferred').sum()==1


def test_price_gap_stop_has_priority_and_keeps_rs_instruction_evidence():
    p,u,f,state=inputs()
    mask=(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-07'))
    for col,value in [('open',75),('high',78),('low',74),('close',76)]:p.loc[mask,col]=value
    result=run(p,u,f,state)
    assert result[1].iloc[0].exit_reason=='gap_stop' and result[1].iloc[0].exit_price==75
    assert result[1].iloc[0].rs_exit_signal_date=='2026-01-06'


def test_exit_cannot_reenter_same_stock_on_same_open():
    p,u,f,state=inputs()
    f.loc[f.date==pd.Timestamp('2026-01-06'),'signal']=True
    result=run(p,u,f,state)
    assert not result[4]['positions']
    assert 'rs_exit_same_session_reentry_blocked' in result[2].reason.tolist()


def test_disabled_preserves_existing_behavior_and_configuration_fingerprint():
    rules=Rules()
    p,u,f,state=inputs(rules=rules)
    result=run(p,u,f,state)
    assert result[1].empty and 'rs_weak_count' not in result[4]['positions']['A']
    base=decision_rule_settings(rules)
    assert not any(k.startswith('rs_exit_') for k in base)
    assert base==decision_rule_settings(replace(rules,rs_exit_percentile=60,rs_exit_confirmations=3))
    assert base!=decision_rule_settings(replace(rules,rs_exit_enabled=True))


def test_observations_before_position_entry_are_not_counted():
    p,u,f,state=inputs((49,48,47,46,45))
    state['cash']=100000;state['positions']={}
    f.loc[f.date==pd.Timestamp('2026-01-05'),'signal']=True
    result=run(p,u,f,state)
    assert result[1].iloc[0].entry_date=='2026-01-06'
    assert result[1].iloc[0].rs_exit_signal_date=='2026-01-07'
    assert result[1].iloc[0].exit_date=='2026-01-08'


@pytest.mark.parametrize('kwargs',[{'rs_exit_enabled':1},{'rs_exit_percentile':101},
    {'rs_exit_mansfield':float('nan')},{'rs_exit_confirmations':0},{'rs_exit_confirmations':True}])
def test_invalid_rules(kwargs):
    with pytest.raises(ValueError):Rules(**kwargs).validate()


def test_scan_and_history_have_same_exit_observation_without_future_leakage(tmp_path):
    from trendfollow.demo import generate
    generate(tmp_path)
    p=pd.read_csv(tmp_path/'prices.csv',dtype={'symbol':str})
    u=pd.read_csv(tmp_path/'universe.csv',dtype={'symbol':str,'benchmark':str})
    rules=Rules(rs_exit_enabled=True,rs_method='mansfield',mansfield_weeks=8)
    prepared,_=features(p,u,rules)
    end=p.date.max()
    current=screen(p,u,end,rules).set_index('symbol')
    latest=prepared[prepared.date==end].set_index('symbol')
    for field in ['rs_exit_value','rs_exit_observation_date','rs_exit_weak']:
        pd.testing.assert_series_equal(current[field].sort_index(),latest[field].sort_index())
    cutoff=sorted(p.date.unique())[-8]
    before=features(p[p.date<=cutoff],u,rules)[0]
    pd.testing.assert_frame_equal(prepared[prepared.date<=cutoff].reset_index(drop=True),before.reset_index(drop=True))
