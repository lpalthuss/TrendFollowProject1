import copy
from dataclasses import replace
import json

import pandas as pd
import pytest

from trendfollow.backtest import features,simulate
from trendfollow.demo import generate
from trendfollow.initial_stop import planning_stops
from trendfollow.pyramiding import evaluate_addition,review_events,setup_identity
from trendfollow.relative_strength import decision_rule_settings
from trendfollow.risk import Limits
from trendfollow.strategy import Rules,screen

LIMITS=Limits(cost_rate=0,entry_slippage=0,exit_slippage=0)


def position():
    return dict(quantity=10,entry_price=100,initial_risk=80,target_3r=124,
        entry_date='2026-01-06',success=True,success_3r_date='2026-01-07',
        stop_price=100,exit_pending=False,entry_setup_id='old',pyramiding_review_seen=[])


def trigger():
    return dict(symbol='A',date='2026-01-15',entry_method='setup',signal=True,
        setup_wave_start='2026-01-06',setup_peak_date='2026-01-08',setup_confirmed_at='2026-01-09',
        setup_ready=True,setup_trigger=True,setup_entry=True,setup_invalidated=False,
        close=130,reference_price=130,stop_price=120)


def test_review_requires_new_post_success_setup_and_never_sizes_or_orders():
    pos=position();row=trigger();before=copy.deepcopy(pos)
    result=evaluate_addition(pos,row,row['date'],LIMITS)
    assert result['eligible_for_risk_review'] and result['review_status']=='eligible_for_risk_review'
    assert result['risk_aggregation_status']=='pending'
    assert result['additional_quantity']==result['orders_created']==0
    assert result['initial_risk_per_share']==8 and result['target_3r']==124
    assert pos['quantity']==before['quantity'] and pos['stop_price']==before['stop_price']
    assert pos['pyramiding_review_seen']==[setup_identity(row)]


@pytest.mark.parametrize('which,key,value,reason',[
    ('pos','success',False,'three_r_not_reached'),
    ('pos','success',float('nan'),'three_r_not_reached'),
    ('pos','success_3r_date',None,'missing_or_invalid_three_r_date'),
    ('pos','success_3r_date','2026-01-16','missing_or_invalid_three_r_date'),
    ('pos','initial_risk',0,'missing_initial_risk'),
    ('pos','stop_price',99,'holding_stop_below_entry'),
    ('pos','stop_price',130,'holding_stop_breached'),
    ('pos','exit_pending',True,'exit_pending'),
    ('pos','rs_exit_pending',{'reason':'weak'},'exit_pending'),
    ('row','close',123,'below_three_r_now'),
    ('row','setup_peak_date','2026-01-07','no_new_post_three_r_setup'),
    ('row','setup_confirmed_at','2026-01-16','no_new_post_three_r_setup'),
    ('row','setup_peak_date',None,'no_new_post_three_r_setup'),
    ('row','setup_peak_date','invalid','no_new_post_three_r_setup'),
    ('row','setup_trigger',False,'no_valid_setup_trigger'),
    ('row','setup_trigger',pd.NA,'no_valid_setup_trigger'),
    ('row','setup_entry',False,'no_valid_setup_trigger'),
    ('row','setup_invalidated',True,'unhealthy_pullback'),
    ('row','setup_invalidated',pd.NA,'unhealthy_pullback'),
    ('row','entry_method','breakout','not_setup_strategy'),
    ('row','signal',False,'entry_filters_not_passed'),
    ('row','signal',pd.NA,'entry_filters_not_passed'),
    ('row','date','2026-01-14','stale_or_missing_signal'),
    ('row','stop_price',0,'invalid_proposed_stop')])
def test_failed_review_records_reason_and_never_consumes_setup(which,key,value,reason):
    pos=position();row=trigger()
    (pos if which=='pos' else row)[key]=value
    result=evaluate_addition(pos,row,'2026-01-15',LIMITS)
    assert not result['eligible_for_risk_review'] and reason in result['review_reasons']
    assert pos['pyramiding_review_seen']==[]


def test_same_setup_cannot_be_reviewed_twice_across_json_resume():
    pos=position();row=trigger()
    assert evaluate_addition(pos,row,row['date'],LIMITS)['eligible_for_risk_review']
    restored=json.loads(json.dumps(pos))
    again=evaluate_addition(restored,row,row['date'],LIMITS)
    assert not again['eligible_for_risk_review'] and 'setup_already_reviewed' in again['review_reasons']
    row.update(date='2026-02-05',setup_wave_start='2026-01-16',
        setup_peak_date='2026-01-22',setup_confirmed_at='2026-01-23')
    assert evaluate_addition(restored,row,row['date'],LIMITS)['eligible_for_risk_review']


def test_initial_setup_is_not_a_new_setup():
    row=trigger();pos=position();pos['entry_setup_id']=setup_identity(row)
    assert 'not_a_new_setup' in evaluate_addition(pos,row,row['date'],LIMITS)['review_reasons']


def test_proposed_stop_uses_same_initial_stop_policy_and_cap():
    row=trigger();row.update(initial_stop_reference='entry_fill',initial_stop_method='hybrid',
        fixed_stop_fraction=.08,stop_atr_multiple=2,atr=12,max_stop_fraction=.15,stop_limit_enabled=True)
    result=evaluate_addition(position(),row,row['date'],LIMITS)
    assert result['proposed_stop_price']==106
    assert 'proposed_stop_limit_exceeded' in result['review_reasons']
    row['stop_limit_enabled']=False
    assert evaluate_addition(position(),row,row['date'],LIMITS)['eligible_for_risk_review']


def simulation_inputs(enabled=True):
    dates=pd.bdate_range('2026-01-05',periods=10)
    closes=[100,100,124,128,123,124,125,126,130,132]
    p=pd.DataFrame([dict(date=d,symbol=s,open=v,high=v+2,low=v-1,close=v,volume=100000)
        for d,price in zip(dates,closes) for s,v in [('A',price),('B',100)]])
    # Touching 3R intraday must not latch success until a completed close confirms it.
    p.loc[(p.symbol=='A')&(p.date==dates[1]),'high']=126
    u=pd.DataFrame([dict(symbol='A',name='Alpha',sector='S',benchmark='B')])
    rules=Rules(entry_method='setup',initial_stop_method='fixed',initial_stop_reference='entry_fill',
        pyramiding_review_enabled=enabled,stop_limit_enabled=False)
    f=pd.DataFrame([dict(date=d,symbol='A',sector='S',entry_method='setup',signal=i in [0,8,9],
        reference_price=price,close=price,atr=4,avg_turnover=1e9,rs_percentile=99,trailing_channel=90,
        setup_wave_start='2026-01-06' if i>=8 else '2025-12-01',
        setup_peak_date='2026-01-08' if i>=8 else '2025-12-23',
        setup_confirmed_at='2026-01-09' if i>=8 else '2025-12-24',
        setup_ready=i in [0,8,9],setup_trigger=i in [0,8,9],setup_entry=i in [0,8,9],
        setup_invalidated=False,stop_limit_enabled=False) for i,(d,price) in enumerate(zip(dates,closes))])
    f=planning_stops(f,rules)
    if enabled:f['pyramiding_review_enabled']=True
    return p,u,f


def test_three_r_is_not_take_profit_units_increment_once_and_review_does_not_add_shares():
    p,u,f=simulation_inputs()
    def run(end):return simulate(p,f,u,'2026-01-05',end,100000,'progressive',LIMITS)
    before=run('2026-01-06')
    assert not before[4]['positions']['A']['success']
    at_three=run('2026-01-07')
    pos=at_three[4]['positions']['A']
    assert pos['success'] and pos['success_3r_date']=='2026-01-07' and pos['stop_price']==100
    assert pos['target_3r']==124 and at_three[4]['units']==2
    full=run('2026-01-16');final=full[4]['positions']['A']
    assert full[1].empty and len(full[2])==1
    assert final['quantity']==pos['quantity'] and final['initial_risk']==pos['initial_risk']
    assert full[4]['units']==2
    reviews=review_events(full[3])
    assert reviews.loc[reviews.eligible_for_risk_review,'date'].tolist()==['2026-01-15']
    assert 'setup_already_reviewed' in reviews.iloc[-1].review_reasons
    assert (reviews.additional_quantity==0).all() and not full[4]['pending']
    assert (full[3].kind=='success_3r').sum()==1


def test_resume_matches_continuous_and_future_signals_do_not_change_earlier_reviews():
    p,u,f=simulation_inputs()
    full=simulate(p,f,u,'2026-01-05','2026-01-16',100000,'progressive',LIMITS)
    first=simulate(p,f,u,'2026-01-05','2026-01-15',100000,'progressive',LIMITS)
    resumed=simulate(p,f,u,'2026-01-16','2026-01-16',100000,'progressive',LIMITS,
        initial_state=json.loads(json.dumps(first[4])))
    assert resumed[4]==full[4]
    combined=pd.concat([review_events(first[3]),review_events(resumed[3])],ignore_index=True)
    pd.testing.assert_frame_equal(combined,review_events(full[3]).reset_index(drop=True))
    changed=f.copy();changed.loc[changed.date>pd.Timestamp('2026-01-15'),'signal']=False
    prior=simulate(p,changed,u,'2026-01-05','2026-01-15',100000,'progressive',LIMITS)
    pd.testing.assert_frame_equal(review_events(prior[3]),review_events(first[3]))


def test_rs_exit_decision_has_priority_over_same_close_addition_review():
    p,u,f=simulation_inputs()
    f['rs_exit_enabled']=True;f['rs_exit_data_ready']=True
    f['rs_exit_weak']=f.date.eq(pd.Timestamp('2026-01-15'))
    f['rs_exit_value']=40;f['rs_exit_threshold']=50;f['rs_exit_confirmations']=1
    f['rs_exit_method']='percentile';f['rs_exit_observation_date']=f.date
    result=simulate(p,f,u,'2026-01-05','2026-01-16',100000,'progressive',LIMITS)
    reviews=review_events(result[3])
    assert not reviews.eligible_for_risk_review.any()
    assert 'exit_pending' in reviews[reviews.date=='2026-01-15'].iloc[0].review_reasons
    assert result[1].iloc[0].exit_reason=='rs_weakness_percentile'


def test_price_stop_has_priority_over_same_bar_three_r_and_fresh_trigger():
    p,u,f=simulation_inputs()
    mask=(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-07'))
    p.loc[mask,'low']=91
    result=simulate(p,f,u,'2026-01-05','2026-01-07',100000,'progressive',LIMITS)
    assert result[1].iloc[0].exit_reason=='intraday_stop'
    assert not (result[3].kind=='success_3r').any()
    assert not result[4]['positions']


def test_ten_r_does_not_force_profit_taking_or_another_unit_increment():
    p,u,f=simulation_inputs()
    mask=(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-16'))
    p.loc[mask,['open','high','low','close']]=[200,201,199,200]
    f.loc[f.date==pd.Timestamp('2026-01-16'),['reference_price','close']]=200
    result=simulate(p,f,u,'2026-01-05','2026-01-16',100000,'progressive',LIMITS)
    assert result[1].empty and result[4]['positions']['A']['last_price']==200
    assert result[4]['units']==2 and len(result[2])==1


def test_missing_signal_row_is_a_blocked_review_not_a_buy():
    p,u,f=simulation_inputs()
    f=f[f.date!=pd.Timestamp('2026-01-15')]
    result=simulate(p,f,u,'2026-01-05','2026-01-15',100000,'progressive',LIMITS)
    latest=review_events(result[3]).iloc[-1]
    assert latest.date=='2026-01-15' and not latest.eligible_for_risk_review
    assert 'missing_signal_row' in latest.review_reasons and len(result[2])==1


def test_three_r_threshold_scales_with_actual_initial_risk_not_fixed_twenty_four_percent():
    row=trigger();pos=position();pos.update(initial_risk=120,target_3r=136)
    result=evaluate_addition(pos,row,row['date'],LIMITS)
    assert result['initial_risk_per_share']==12 and 'below_three_r_now' in result['review_reasons']
    row['close']=136
    assert evaluate_addition(pos,row,row['date'],LIMITS)['eligible_for_risk_review']


def test_default_is_compatible_and_enabled_requires_setup():
    assert 'pyramiding_review_enabled' not in decision_rule_settings(Rules())
    assert decision_rule_settings(Rules())==decision_rule_settings(Rules(pyramiding_review_enabled=False))
    with pytest.raises(ValueError):Rules(pyramiding_review_enabled=True).validate()
    with pytest.raises(ValueError):Rules(pyramiding_review_enabled=1).validate()
    Rules(entry_method='setup',pyramiding_review_enabled=True).validate()
    p,u,f=simulation_inputs(False)
    result=simulate(p,f,u,'2026-01-05','2026-01-16',100000,'progressive',LIMITS)
    assert review_events(result[3]).empty
    assert 'pyramiding_review_seen' not in result[4]['positions']['A']


def test_screen_features_and_paper_backtest_outputs(tmp_path):
    from trendfollow.workflow import paper_checkpoint,run_comparison
    generate(tmp_path)
    p=pd.read_csv(tmp_path/'prices.csv',dtype={'symbol':str})
    u=pd.read_csv(tmp_path/'universe.csv',dtype={'symbol':str,'benchmark':str})
    rules=Rules(entry_method='setup',pyramiding_review_enabled=True)
    history,_=features(p,u,rules)
    scan=screen(p,u,'2026-11-06',rules)
    assert scan.pyramiding_review_enabled.all() and history.pyramiding_review_enabled.all()
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'rules':{'entry_method':'setup','pyramiding_review_enabled':True}}))
    state=tmp_path/'paper/state.json'
    report=paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state,config_path=config)
    assert report['pyramiding_review_candidates']==0
    assert pd.read_csv(state.parent/'2026-11-06/pyramiding-review.csv').empty
    before=state.read_bytes()
    assert paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state,config_path=config)['status']=='already_processed'
    assert state.read_bytes()==before
    summary=run_comparison(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-09-01','2026-10-01',
        '2026-11-06',tmp_path/'backtest',config_path=config)
    assert summary['rules']['pyramiding_review_enabled']
    for key in summary['results']:assert (tmp_path/'backtest'/key/'pyramiding-review.csv').is_file()
