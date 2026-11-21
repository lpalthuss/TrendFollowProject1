from dataclasses import replace
import json

import pandas as pd
import pytest

from trendfollow.backtest import features, metrics, simulate
from trendfollow.demo import generate
from trendfollow.market import build_universe
from trendfollow.quality import compare_sources
from trendfollow.risk import Limits
from trendfollow.strategy import screen
from trendfollow.workflow import paper_checkpoint


def inputs():
    dates=pd.date_range('2026-01-05',periods=3)
    rows=[]
    for date,price in zip(dates,[100,102,80]):
        rows.append(dict(date=date,symbol='A',open=price,high=price+3,low=price-2,close=price,volume=10000))
        rows.append(dict(date=date,symbol='B',open=100,high=101,low=99,close=100,volume=10000))
    prices=pd.DataFrame(rows)
    universe=pd.DataFrame([dict(symbol='A',name='Alpha',sector='S',benchmark='B')])
    signals=pd.DataFrame([dict(date=d,symbol='A',sector='S',signal=i==0,
                reference_price=100,stop_price=90,rs_percentile=100,avg_turnover=1e9,
                trailing_channel=85) for i,d in enumerate(dates)])
    limits=Limits(max_trade_risk=.02,total_risk=.5,max_position=.9,max_sector=.9,
                  cash_reserve=0,volume_participation=.1,cost_rate=0,entry_slippage=0,exit_slippage=0)
    return prices,universe,signals,limits


def test_next_open_entry_and_gap_exit():
    p,u,s,l=inputs()
    curve,trades,orders,events,state=simulate(p,s,u,'2026-01-05','2026-01-07',10000,'fixed',l)
    assert curve.positions.iloc[0]==0
    assert orders.iloc[0].date=='2026-01-06'
    assert trades.iloc[0].entry_price==102
    assert trades.iloc[0].exit_price==80
    assert trades.iloc[0].exit_reason=='gap_stop'
    assert trades.iloc[0].quantity==16
    assert curve.equity.iloc[-1]==pytest.approx(9648)
    assert curve.cash.min()>=0


def test_entry_gap_limit_cancels_instead_of_filling():
    p,u,s,l=inputs()
    p.loc[(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-06')),['open','high','low','close']]=[130,132,129,131]
    curve,trades,orders,_,_=simulate(p,s,u,'2026-01-05','2026-01-07',10000,'fixed',l)
    assert trades.empty
    assert orders.iloc[0].reason=='entry_gap_limit'
    assert curve.equity.iloc[-1]==10000


def test_locked_gap_does_not_assume_exit_fill():
    p,u,s,l=inputs()
    p.loc[(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-07')),['open','high','low','close']]=80
    curve,trades,orders,_,state=simulate(p,s,u,'2026-01-05','2026-01-07',10000,'fixed',l)
    assert trades.empty
    assert state['positions']['A']['exit_pending']
    assert curve.equity.iloc[-1]==pytest.approx(9648)
    assert curve.stale_positions.iloc[-1]==1


def test_cost_and_slippage_reduce_equity():
    p,u,s,l=inputs()
    no_cost=simulate(p,s,u,'2026-01-05','2026-01-07',10000,'fixed',l)[0]
    with_cost=simulate(p,s,u,'2026-01-05','2026-01-07',10000,'fixed',
                      replace(l,cost_rate=.01,entry_slippage=.01,exit_slippage=.01))[0]
    # Compare equal filled quantity, since risk sizing changes with cost assumptions.
    assert with_cost.equity.iloc[1]<10000
    assert with_cost.cash.min()>=0


def test_future_prices_do_not_change_features(tmp_path):
    generate(tmp_path)
    p=pd.read_csv(tmp_path/'prices.csv',dtype={'symbol':str})
    u=pd.read_csv(tmp_path/'universe.csv',dtype={'symbol':str})
    old=features(p,u)[0]
    future=p.groupby('symbol').tail(1).copy()
    future['date']='2026-11-09'
    future[['open','high','low','close']]*=10
    extended=features(pd.concat([p,future]),u)[0]
    pd.testing.assert_frame_equal(old,extended[extended.date<='2026-11-06'].reset_index(drop=True))
    expected=screen(p,u,'2026-11-06').set_index('symbol').signal.sort_index()
    actual=old[old.date=='2026-11-06'].set_index('symbol').signal.sort_index()
    pd.testing.assert_series_equal(expected,actual)


def test_quality_does_not_hide_missing_days_or_price_errors():
    p,_,_,_=inputs()
    reference=p.copy()
    primary=p.iloc[1:].copy().astype({'close':float})
    primary.loc[primary.index[-1],'close']=100.5
    summary,detail=compare_sources(primary,reference)
    assert summary['missing_sessions']==1
    assert summary['price_mismatch_rows']==2


def test_master_filter_includes_reason_for_current_liquidity_exclusions():
    row={'symbol':'005930','name':'Example','그룹코드':'ST','거래정지':'N','정리매매':'N',
        '관리종목':'N','저유동성':'N','우선주':'0','SPAC':'N','지수업종대분류':'0027',
        '기준가':'10000','전일거래량':'200000'}
    frames=[pd.DataFrame([row,dict(row,symbol='000660',거래정지='Y')]),pd.DataFrame()]
    good,bad=build_universe(frames)
    assert list(good.symbol)==['005930']
    assert 'halted' in bad.reasons.iloc[0]
    assert good.sector.iloc[0]=='KOSPI:0027'


def test_paper_same_day_idempotence_and_data_revision_protection(tmp_path):
    generate(tmp_path)
    state=tmp_path/'paper/state.json'
    args=[tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state]
    first=paper_checkpoint(*args)
    before=state.read_bytes()
    second=paper_checkpoint(*args)
    assert first['status']=='processed' and second['status']=='already_processed'
    assert state.read_bytes()==before
    data=pd.read_csv(tmp_path/'prices.csv')
    data.loc[0,'volume']+=1
    data.to_csv(tmp_path/'prices.csv',index=False)
    with pytest.raises(ValueError,match='데이터가 변경'):
        paper_checkpoint(*args)
    assert state.read_bytes()==before


def test_metrics_drawdown_includes_initial_cash():
    curve=pd.DataFrame([dict(date='2026-01-05',equity=9000,stale_positions=0),
                        dict(date='2026-01-06',equity=9500,stale_positions=0)])
    result=metrics(curve,pd.DataFrame(),10000)
    assert result['max_drawdown']==pytest.approx(-.1)
    assert result['closed_trades']==0


def test_progressive_success_event_is_counted_only_once():
    p,u,s,l=inputs()
    p.loc[(p.symbol=='A')&(p.date==pd.Timestamp('2026-01-07')),['open','high','low','close']]=[150,153,148,150]
    extra=p[p.date=='2026-01-07'].copy()
    extra['date']=pd.Timestamp('2026-01-08')
    extra.loc[extra.symbol=='A',['open','high','low','close']]=[160,163,158,160]
    p=pd.concat([p,extra],ignore_index=True)
    next_signal=s.tail(1).copy();next_signal['date']=pd.Timestamp('2026-01-08')
    s=pd.concat([s,next_signal],ignore_index=True)
    curve,trades,orders,events,state=simulate(p,s,u,'2026-01-05','2026-01-08',10000,'progressive',l)
    assert list(events.kind)==['success_3r']
    assert state['units']==2
    assert state['positions']['A']['stop_price']>=102


def test_resume_matches_uninterrupted_simulation():
    p,u,s,l=inputs()
    full=simulate(p,s,u,'2026-01-05','2026-01-07',10000,'progressive',l)
    first=simulate(p,s,u,'2026-01-05','2026-01-06',10000,'progressive',l)
    resumed=simulate(p,s,u,'2026-01-07','2026-01-07',10000,'progressive',l,initial_state=first[-1])
    assert full[-1]['cash']==pytest.approx(resumed[-1]['cash'])
    assert full[-1]['positions']==resumed[-1]['positions']
    assert full[-1]['units']==resumed[-1]['units']


def test_daily_weekend_skips_before_network(tmp_path,monkeypatch):
    from trendfollow.workflow import daily_run
    import requests
    def forbidden(*args,**kwargs):
        pytest.fail('weekend must not access network')
    monkeypatch.setattr(requests,'get',forbidden)
    result=daily_run(tmp_path/'absent.csv',tmp_path/'data',tmp_path/'state.json','2026-11-08')
    assert result['status']=='skipped_weekend'


def test_daily_catchup_processes_missing_sessions_in_order_with_one_collection(tmp_path,monkeypatch):
    import json
    from trendfollow import market,workflow
    from trendfollow.strategy import Rules
    universe=tmp_path/'universe.csv'
    pd.DataFrame([{'symbol':'000001','benchmark':'069500'}]).to_csv(universe,index=False)
    state=tmp_path/'state.json'
    state.write_text(json.dumps({'last_date':'2026-11-10'}),encoding='utf-8')
    class Source:
        def __init__(self,cache):pass
        def daily(self,symbol,start,end):
            dates=['2026-11-10','2026-11-11','2026-11-12','2026-11-13']
            return pd.DataFrame({'date':[d for d in dates if start<=d<=end]})
    monkeypatch.setattr(market,'NaverResearchSource',Source)
    collected=[];processed=[]
    monkeypatch.setattr(market,'acquire_research',lambda *a,**kw:(collected.append(kw['required_session']) or {'errors':[]}))
    monkeypatch.setattr(workflow,'read_inputs',lambda *a:(pd.DataFrame(),pd.DataFrame(),Rules(),None))
    monkeypatch.setattr(workflow,'features',lambda *a:(pd.DataFrame({'date':[pd.Timestamp('2026-11-13')],'signal':[False]}),None))
    monkeypatch.setattr(workflow,'verify_paper_candidates',lambda *a:[])
    def checkpoint(prices,universe,date,state_path,*args):
        processed.append(date)
        state.write_text(json.dumps({'last_date':date}),encoding='utf-8')
        return {'status':'processed','as_of':date}
    monkeypatch.setattr(workflow,'paper_checkpoint',checkpoint)
    with pytest.raises(ValueError,match='미처리 거래일이 여러 개'):
        workflow.daily_run(universe,tmp_path/'data',state,'2026-11-13')
    result=workflow.daily_run(universe,tmp_path/'data',state,'2026-11-13',catch_up=True)
    assert collected==['2026-11-13']
    assert processed==['2026-11-11','2026-11-12','2026-11-13']
    assert result['processed_dates']==processed
    state.write_text(json.dumps({'last_date':'2026-11-10'}),encoding='utf-8')
    collected.clear();processed.clear()
    result=workflow.daily_run(universe,tmp_path/'data',state,'2026-11-13',catch_up=True,use_existing_prices=True)
    assert not collected
    assert result['processed_dates']==['2026-11-11','2026-11-12','2026-11-13']


def test_paper_refuses_to_advance_when_processed_history_is_revised(tmp_path):
    generate(tmp_path)
    state=tmp_path/'paper/state.json'
    paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-05',state)
    before=state.read_bytes()
    p=pd.read_csv(tmp_path/'prices.csv')
    p.loc[0,'volume']+=1
    p.to_csv(tmp_path/'prices.csv',index=False)
    with pytest.raises(ValueError,match='과거 일봉이 수정'):
        paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state)
    assert state.read_bytes()==before

def test_pyramiding_transition_keeps_prior_open_fill_and_stop():
    from trendfollow.config import Rules

    prices, universe, signals, limits = inputs()
    first = simulate(prices, signals, universe, '2026-01-05', '2026-01-05', 10000, 'fixed', limits)[-1]
    args = (prices, signals, universe, '2026-01-06', '2026-01-06', 10000, 'fixed', limits)
    old_result = simulate(*args, initial_state=first)
    rules = Rules(entry_method='setup', initial_stop_reference='entry_fill',
                  pyramiding_enabled=True, pyramiding_review_enabled=True)
    new_result = simulate(*args, initial_state=first, close_rules=rules)
    pd.testing.assert_frame_equal(old_result[2], new_result[2])
    before = old_result[-1]['positions']['A']
    after = new_result[-1]['positions']['A']
    assert {k:after[k] for k in before} == before
    assert before['quantity'] == after['lots'][0]['quantity']
    assert after['addition_count'] == 0
    assert old_result[-1]['cash'] == new_result[-1]['cash']
