import copy
from dataclasses import replace
import json

import pandas as pd
import pytest

from trendfollow.backtest import simulate
from trendfollow.initial_stop import planning_stops
from trendfollow.pyramiding import review_events
from trendfollow.pyramiding_execution import stop_risk
from trendfollow.relative_strength import decision_rule_settings
from trendfollow.risk import Account,Limits,allocate
from trendfollow.strategy import Rules

FREE=Limits(cost_rate=0,entry_slippage=0,exit_slippage=0,units=2)


def held():
    return dict(symbol='A',sector='S',quantity=100,current_price=13000,stop_price=12000,
        entry_price=10000,entry_total=1000000,initial_risk=80000,target_3r=12400,
        success=True,success_3r_date='2026-01-07',entry_date='2026-01-06',
        addition_count=0,initial_quantity=100,average_entry_price=10000,
        total_committed_risk=80000,lots=[dict(number=0,quantity=100)],pyramiding_filled_setups=[])


def candidate(symbol='A',adding=True,atr=250):
    rules=Rules(entry_method='setup',initial_stop_method='atr',initial_stop_reference='entry_fill',
        stop_limit_enabled=False,pyramiding_review_enabled=True,pyramiding_enabled=True)
    f=planning_stops(pd.DataFrame([dict(symbol=symbol,sector='S',signal=True,reference_price=13000,
        atr=atr,avg_turnover=1e12,rs_percentile=99,order_intent='add' if adding else 'entry',
        date='2026-01-15',setup_wave_start='2026-01-06',setup_peak_date='2026-01-08',
        setup_confirmed_at='2026-01-09',pyramiding_enabled=True,pyramiding_max_additions=3,
        stop_limit_enabled=False)]),rules)
    return f


def test_combined_risk_uses_current_value_not_purchase_profit_and_keeps_one_percent_default():
    p=held();account=Account(10000000,8700000,[p])
    result=allocate(candidate(),account,FREE).iloc[0]
    assert result.quantity==80 and result.stop_price==12500
    assert result.existing_stop_risk_after==50000
    assert result.planned_risk==40000 and result.combined_position_risk==90000
    assert result.position_risk_limit==100000
    assert p['stop_price']==12000 and p['quantity']==100
    assert stop_risk(13000,12500,FREE)==500


def test_smaller_equity_automatically_shrinks_combined_risk_budget():
    limits=replace(FREE,max_position=.9)
    original=allocate(candidate(),Account(10000000,8700000,[held()]),limits).iloc[0]
    reduced=allocate(candidate(),Account(8000000,6700000,[held()]),limits).iloc[0]
    assert reduced.quantity==60<original.quantity
    assert reduced.combined_position_risk<=80000


def test_two_percent_is_mandatory_for_initial_entries_too():
    with pytest.raises(ValueError,match='2%'):Limits(max_trade_risk=.02001).validate()
    limits=replace(FREE,max_trade_risk=.02,units=5,max_position=.9,max_sector=.9)
    row=allocate(candidate('B',False),Account(1000000,1000000),limits).iloc[0]
    assert row.planned_risk<=20000
    # Legacy allocator uses the same hard limit validation.
    f=candidate('B',False).drop(columns='pyramiding_enabled')
    with pytest.raises(ValueError,match='2%'):
        allocate(f,Account(1000000,1000000),replace(limits,max_trade_risk=.03))


@pytest.mark.parametrize('limits',[
    replace(FREE,cash_reserve=.8),replace(FREE,max_sector=.15),
    replace(FREE,max_position=.15),replace(FREE,volume_participation=.000001),
    replace(FREE,total_risk=.005),replace(FREE,units=1)])
def test_all_existing_caps_apply_to_additions(limits):
    f=candidate();f['avg_turnover']=1e8
    account=Account(10000000,8700000,[held()])
    row=allocate(f,account,limits).iloc[0]
    assert row.planned_risk<=row.per_trade_budget+1e-8
    if row.quantity:
        assert row.combined_position_risk<=row.position_risk_limit+1e-8
        assert row.portfolio_risk_after<=row.portfolio_risk_limit+1e-8
    assert row.cash_required<=max(0,account.cash-account.equity*limits.cash_reserve)+1e-8


def test_shared_cash_and_sector_are_not_spent_twice_between_initial_and_addition():
    f=pd.concat([candidate(),candidate('B',False)],ignore_index=True)
    f.loc[f.symbol=='B','rs_percentile']=90
    limits=replace(FREE,cash_reserve=.8)
    result=allocate(f,Account(10000000,8700000,[held()]),limits)
    assert result.cash_required.sum()<=700000 and result.quantity.iloc[1]==0
    assert result.quantity.iloc[0]>0


@pytest.mark.parametrize('change,reason',[
    ({'addition_count':3},'max_additions_reached'),
    ({'exit_pending':True},'addition_exit_pending'),
    ({'rs_exit_pending':{'reason':'weak'}},'addition_exit_pending'),
    ({'target_3r':14000},'below_three_r_at_open'),
    ({'stop_price':12600},'common_stop_not_raised'),
    ({'price_stale':True},'stale_position_price'),
    ({'stop_price':13001},'existing_stop_breached_review_exit_first')])
def test_rechecks_block_invalid_additions_without_mutating_the_position(change,reason):
    p=held();p.update(change);before=copy.deepcopy(p)
    row=allocate(candidate(),Account(10000000,8700000,[p]),FREE).iloc[0]
    assert row.quantity==0 and row.allocation_reason==reason and p==before


def test_market_unit_cap_and_duplicate_setup_are_checked():
    p=held();f=candidate();f['market_unit_cap']=0
    assert allocate(f,Account(10000000,8700000,[p]),FREE).allocation_reason.iloc[0]=='blocked_market_units'
    f['market_unit_cap']=5;p['pyramiding_filled_setups']=['2026-01-06|2026-01-08|2026-01-09']
    assert allocate(f,Account(10000000,8700000,[p]),FREE).allocation_reason.iloc[0]=='setup_already_filled'


def test_execution_fees_reduce_equity_and_never_push_combined_risk_over_two_percent():
    limits=Limits(max_trade_risk=.02,units=5,max_position=.9,max_sector=.9)
    account=Account(1000000,1000000)
    row=allocate(candidate('B',False),account,limits).iloc[0]
    actual_equity=account.equity-row.cash_required+row.quantity*13000
    assert row.equity_at_allocation==pytest.approx(actual_equity)
    assert row.combined_position_risk<=actual_equity*.02


def inputs():
    dates=pd.bdate_range('2026-01-05',periods=16)
    values=[100,100,125,128,125,130,132,134,140,142,145,150,152,154,160,162]
    p=pd.DataFrame([dict(date=d,symbol=s,open=v,high=v+1,low=v-.5,close=v,volume=100000)
        for d,price in zip(dates,values) for s,v in [('A',price),('B',100)]])
    u=pd.DataFrame([dict(symbol='A',name='Alpha',sector='S',benchmark='B')])
    rules=Rules(entry_method='setup',initial_stop_method='atr',initial_stop_reference='entry_fill',
        stop_limit_enabled=False,pyramiding_review_enabled=True,pyramiding_enabled=True)
    rows=[]
    for i,(date,price) in enumerate(zip(dates,values)):
        peak=3 if i<8 else 6 if i<11 else 9 if i<14 else 12
        rows.append(dict(date=date,symbol='A',sector='S',entry_method='setup',signal=i in [0,5,8,11,14],
            reference_price=price,close=price,atr=4 if i<5 else 3 if i<8 else 2 if i<11 else 1,
            avg_turnover=1e12,rs_percentile=99,trailing_channel=90,
            setup_wave_start='2025-12-01' if i==0 else str(dates[1].date()),
            setup_peak_date='2025-12-23' if i==0 else str(dates[peak].date()),
            setup_confirmed_at='2025-12-24' if i==0 else str(dates[peak+1].date()),
            setup_ready=True,setup_trigger=True,setup_entry=True,setup_invalidated=False,
            pyramiding_review_enabled=True,pyramiding_enabled=True,pyramiding_max_additions=3,stop_limit_enabled=False))
    f=planning_stops(pd.DataFrame(rows),rules)
    limits=replace(FREE,max_position=.9,max_sector=.9,cash_reserve=0,volume_participation=.1)
    return p,u,f,limits


def run(p,u,f,l,start='2026-01-05',end='2026-01-26',state=None):
    return simulate(p,f,u,start,end,100000,'progressive',l,initial_state=state)


def test_three_filled_additions_raise_common_stop_and_keep_original_r_and_success_once():
    p,u,f,l=inputs();result=run(p,u,f,l)
    curve,trades,orders,events,state=result
    adds=orders[orders.order_intent=='add']
    assert len(adds)==3 and adds.addition_number.tolist()==[1,2,3]
    assert (adds.common_stop_after>adds.common_stop_before).all()
    assert (adds.combined_position_risk<=adds.position_risk_limit).all()
    assert (adds.portfolio_risk_after<=adds.portfolio_risk_limit).all()
    pos=state['positions']['A']
    assert pos['initial_quantity']==25 and pos['quantity']>25 and len(pos['lots'])==4
    assert pos['entry_price']==100 and pos['initial_risk']==200 and pos['target_3r']==124
    assert pos['addition_count']==3 and state['units']==2 and trades.empty
    assert (events.kind=='success_3r').sum()==1
    assert 'max_additions_reached' in review_events(events).iloc[-2].review_reasons
    assert curve.cash.min()>=0


def test_resume_preserves_lots_and_does_not_mutate_the_input_checkpoint():
    p,u,f,l=inputs();full=run(p,u,f,l)
    first=run(p,u,f,l,end='2026-01-12')
    old=json.loads(json.dumps(first[4]));before=copy.deepcopy(old)
    resumed=run(p,u,f,l,start='2026-01-13',state=old)
    assert old==before and resumed[4]==full[4]
    pd.testing.assert_frame_equal(resumed[2].reindex(columns=full[2].columns).reset_index(drop=True),
        full[2].iloc[1:].reset_index(drop=True),check_dtype=False)


@pytest.mark.parametrize('kind,reason',[
    ('gap_up','entry_gap_limit'),('missing','missing_or_untradable_open'),
    ('locked','locked_single_price_bar'),('below_three_r','below_three_r_at_open')])
def test_cancelled_addition_does_not_raise_stop_or_increment_count(kind,reason):
    p,u,f,l=inputs();mask=(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-13'))
    if kind=='gap_up':p.loc[mask,['open','high','low','close']]=[150,151,149,150]
    elif kind=='missing':p=p[~mask]
    elif kind=='locked':p.loc[mask,['open','high','low','close']]=132
    else:
        p.loc[mask,['open','high','low','close']]=[123,124,122,123]
        f.loc[f.date==pd.Timestamp('2026-01-12'),['atr','stop_price']]=[4,122]
    result=run(p,u,f,l,end='2026-01-13');pos=result[4]['positions']['A']
    assert pos['addition_count']==0 and pos['quantity']==25 and pos['stop_price']==100
    assert reason in result[2].reason.dropna().tolist()


def test_common_stop_exits_every_lot_with_total_cost_and_frozen_initial_r():
    p,u,f,l=inputs()
    before=run(p,u,f,l,end='2026-01-21');pos=before[4]['positions']['A']
    mask=(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-22'))
    p.loc[mask,['open','high','low','close']]=[140,141,139,140]
    result=run(p,u,f,l);trade=result[1].iloc[0]
    assert trade.quantity==pos['quantity'] and trade.addition_count==3
    assert trade.pnl==pytest.approx(trade.quantity*140-pos['entry_total'])
    assert trade.r_multiple==pytest.approx(trade.pnl/200)
    assert trade.r_multiple_total_committed_risk==pytest.approx(trade.pnl/pos['total_committed_risk'])
    assert trade.exit_reason=='gap_stop'


def test_execution_off_preserves_review_only_fingerprint_and_configuration_validation():
    old=Rules(entry_method='setup',pyramiding_review_enabled=True)
    assert 'pyramiding_enabled' not in decision_rule_settings(old)
    assert decision_rule_settings(old)==decision_rule_settings(replace(old,pyramiding_max_additions=1))
    for kwargs in [dict(pyramiding_enabled=1),dict(pyramiding_max_additions=4),
        dict(pyramiding_max_additions=True),dict(pyramiding_enabled=True)]:
        with pytest.raises(ValueError):Rules(**kwargs).validate()


def test_new_execution_costs_cannot_push_another_position_above_its_two_percent_limit():
    p=held();p['stop_price']=12500
    limits=Limits(max_trade_risk=.02,units=5,max_position=.9,max_sector=.9,exit_slippage=0,cost_rate=.003)
    # Include exit costs when locating the existing position exactly at its risk cap.
    risk=p['quantity']*stop_risk(13000,12500,limits)
    equity=risk/.02
    result=allocate(candidate('B',False),Account(equity,equity-1300000,[p]),limits).iloc[0]
    assert result.quantity==0 and result.allocation_reason=='blocked_other_position_risk'


def test_fourth_signal_is_blocked_and_still_keeps_current_holding_risk_audited():
    from trendfollow.pyramiding_execution import execution_events,RISK_COLUMNS
    p,u,f,l=inputs();result=run(p,u,f,l)
    audit=execution_events(result[3],'campaign_risk',RISK_COLUMNS)
    assert len(audit)==15 and not audit.stale_price.any()
    assert (audit.planned_stop_risk>=0).all()
    assert audit.iloc[-1].quantity==result[4]['positions']['A']['quantity']
    assert len(result[4]['positions']['A']['pyramiding_filled_setups'])==3


def test_future_atr_cannot_reprice_already_queued_addition():
    p,u,f,l=inputs()
    original=run(p,u,f,l,end='2026-01-13')
    changed=f.copy();changed.loc[changed.date==pd.Timestamp('2026-01-13'),'atr']=1000
    changed.loc[changed.date==pd.Timestamp('2026-01-13'),'signal']=False
    altered=run(p,u,changed,l,end='2026-01-13')
    pd.testing.assert_frame_equal(original[2],altered[2])
    assert original[4]['positions']['A']['lots']==altered[4]['positions']['A']['lots']


def test_pending_addition_is_cancelled_if_existing_position_exits_at_open():
    p,u,f,l=inputs();mask=(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-13'))
    p.loc[mask,['open','high','low','close']]=[95,96,94,95]
    result=run(p,u,f,l,end='2026-01-13')
    assert result[1].iloc[0].exit_reason=='gap_stop'
    assert result[2].iloc[-1].reason=='position_closed_before_addition'
    assert not result[4]['positions']


def test_explicit_on_off_comparison_and_paper_outputs(tmp_path):
    from trendfollow.demo import generate
    from trendfollow.workflow import run_comparison,paper_checkpoint
    generate(tmp_path)
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'rules':dict(entry_method='setup',pyramiding_review_enabled=True,
        pyramiding_enabled=True,pyramiding_max_additions=3,initial_stop_reference='entry_fill')}))
    summaries={}
    for enabled in [True,False]:
        out=tmp_path/str(enabled)
        summaries[enabled]=run_comparison(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-09-01',
            '2026-10-01','2026-11-06',out,config_path=config,pyramiding_enabled=enabled)
        if enabled:
            for key in summaries[enabled]['results']:
                assert (out/key/'campaign-lots.csv').is_file() and (out/key/'campaign-risk.csv').is_file()
    assert summaries[True]['rules']['pyramiding_enabled'] is True
    assert summaries[False]['rules']['pyramiding_enabled'] is False
    state=tmp_path/'paper/state.json'
    report=paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state,config_path=config)
    assert report['pyramiding_execution']=='local_simulation' and report['pending_additions']==0
    assert (state.parent/'2026-11-06/campaign-lots.csv').is_file()
    before=state.read_bytes()
    assert paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state,config_path=config)['status']=='already_processed'
    assert state.read_bytes()==before


def test_explicit_off_uses_same_initial_sizing_until_the_first_addition():
    p,u,f,l=inputs();on=run(p,u,f,l,end='2026-01-12')
    off=f.copy();off['pyramiding_enabled']=False;off['campaign_risk_enabled']=True
    baseline=run(p,u,off,l,end='2026-01-12')
    pd.testing.assert_frame_equal(on[0],baseline[0])
    pd.testing.assert_frame_equal(on[2],baseline[2])
    assert on[4]['pending'][0]['order_intent']=='add'
    assert not baseline[4]['pending']
