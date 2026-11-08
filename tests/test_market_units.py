from dataclasses import replace
import json

import pandas as pd
import pytest

from trendfollow.backtest import simulate, features
from trendfollow.market_units import MARKET_UNIT_SETTINGS, market_unit_features
from trendfollow.relative_strength import decision_rule_settings
from trendfollow.risk import Account, Limits, allocate
from trendfollow.strategy import Rules, screen
from trendfollow.workflow import paper_input_digest


def inputs():
    dates = pd.date_range('2026-01-05',periods=3)
    rows = []
    for date, index in zip(dates,[100,102,80]):
        for symbol, close in [('A',100),('B',index),('KOSPI',index)]:
            rows.append(dict(date=date,symbol=symbol,open=close,high=close+2,
                             low=close-2,close=close,volume=10000))
    return pd.DataFrame(rows),pd.DataFrame([dict(symbol='A',name='Alpha',sector='S',benchmark='B',market='KOSPI')])


def test_regimes_are_causal_and_missing_index_is_not_forward_filled():
    p,u=inputs()
    rules=Rules(market_units_enabled=True,market_units_benchmark='market_index',market_units_ma_days=2)
    f=market_unit_features(p,u,rules)
    assert f.market_unit_regime.tolist()==['unavailable','strong','weak']
    assert f.market_unit_cap.tolist()==[0,5,1]
    modified=p.copy()
    modified.loc[modified.date==p.date.max(),'close']=10000
    pd.testing.assert_frame_equal(f.iloc[:2],market_unit_features(modified,u,rules).iloc[:2])
    missing=p[~((p.symbol=='KOSPI')&(p.date==p.date.max()))]
    last=market_unit_features(missing,u,rules).iloc[-1]
    assert last.market_unit_regime=='unavailable' and last.market_unit_cap==0
    with pytest.raises(ValueError,match='시장 유닛 비교'):
        market_unit_features(p[p.symbol!='KOSPI'],u,rules)


def test_effective_units_cap_size_without_overwriting_base_units():
    row=dict(symbol='A',sector='S',signal=True,reference_price=100,stop_price=90,
             avg_turnover=1e9,allocation_score=100)
    limits=Limits(units=5,cost_rate=0,entry_slippage=0,exit_slippage=0)
    strong=allocate(pd.DataFrame([dict(row,market_unit_cap=5)]),Account(100000,100000),limits)
    weak=allocate(pd.DataFrame([dict(row,market_unit_cap=1)]),Account(100000,100000),limits)
    assert strong.quantity.iloc[0]==5*weak.quantity.iloc[0]>0
    assert weak.effective_units.iloc[0]==1 and limits.units==5
    blocked=allocate(pd.DataFrame([dict(row,market_unit_cap=float('nan'))]),Account(100000,100000),limits)
    assert blocked.quantity.iloc[0]==0 and blocked.allocation_reason.iloc[0]=='blocked_market_units'
    # Recovery does not award trade-success units: a one-unit account remains one unit.
    recovery=allocate(pd.DataFrame([dict(row,market_unit_cap=5)]),Account(100000,100000),replace(limits,units=1))
    assert recovery.effective_units.iloc[0]==1


def test_next_open_uses_signal_day_cap_and_resume_preserves_trade_units():
    p,u=inputs()
    dates=sorted(p.date.unique())
    signals=pd.DataFrame([dict(date=d,symbol='A',sector='S',signal=i==0,
        reference_price=100,stop_price=90,rs_percentile=100,avg_turnover=1e9,
        trailing_channel=85,market_unit_cap=1 if i==0 else 5,
        market_unit_regime='weak' if i==0 else 'strong') for i,d in enumerate(dates)])
    limits=Limits(cost_rate=0,entry_slippage=0,exit_slippage=0)
    result=simulate(p,signals,u,'2026-01-05','2026-01-07',100000,'fixed',limits)
    orders=result[2]
    assert orders.iloc[0].date=='2026-01-06'
    assert orders.iloc[0].effective_units==1
    assert orders.iloc[0].quantity==20
    first=simulate(p,signals,u,'2026-01-05','2026-01-05',100000,'fixed',limits)
    resumed=simulate(p,signals,u,'2026-01-06','2026-01-07',100000,'fixed',limits,initial_state=first[4])
    assert resumed[4]==result[4]
    assert result[4]['units']==5


def test_disabled_feature_keeps_existing_configuration_fingerprints():
    settings=decision_rule_settings(Rules())
    assert not set(MARKET_UNIT_SETTINGS)&set(settings)
    assert settings==decision_rule_settings(Rules(market_units_ma_days=100,market_units_weak_cap=0))
    active=decision_rule_settings(Rules(market_units_enabled=True))
    assert active['market_units_enabled'] is True
    p,u=inputs()
    stocks=p[p.symbol!='KOSPI']
    assert paper_input_digest(stocks,u,Rules())==paper_input_digest(p,u,Rules())
    assert paper_input_digest(stocks,u,Rules())!=paper_input_digest(p,u,Rules(
        market_units_enabled=True,market_units_benchmark='market_index'))


@pytest.mark.parametrize('kwargs',[
    {'market_units_enabled':1},{'market_units_ma_days':0},
    {'market_units_strong_cap':-1},{'market_units_weak_cap':6},
    {'market_units_ma_days':True},{'market_units_benchmark':'unknown'}])
def test_invalid_market_settings(kwargs):
    with pytest.raises(ValueError):
        Rules(**kwargs).validate()


def test_screen_and_backtest_use_same_market_cap():
    from trendfollow.demo import generate
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as directory:
        from pathlib import Path
        root=Path(directory);generate(root)
        p=pd.read_csv(root/'prices.csv',dtype={'symbol':str})
        u=pd.read_csv(root/'universe.csv',dtype={'symbol':str,'benchmark':str})
        rules=Rules(market_units_enabled=True)
        scanned=screen(p,u,'2026-11-06',rules).set_index('symbol')
        prepared,_=features(p,u,rules)
        latest=prepared[prepared.date=='2026-11-06'].set_index('symbol')
        pd.testing.assert_series_equal(scanned.market_unit_cap.sort_index(),latest.market_unit_cap.sort_index())
        pd.testing.assert_series_equal(scanned.market_unit_regime.sort_index(),latest.market_unit_regime.sort_index())


def test_daily_collects_indices_when_only_market_units_need_them(tmp_path,monkeypatch):
    import trendfollow.market as market
    from trendfollow.workflow import daily_run
    p,u=inputs()
    path=tmp_path/'universe.csv';u.to_csv(path,index=False)
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'rules':{'market_units_enabled':True,
        'market_units_benchmark':'market_index'}}),encoding='utf-8')
    probes=[]
    def probe(self,symbol,start,end,refresh=False):
        probes.append(symbol)
        return p[p.symbol==symbol]
    def acquire(*args,**kwargs):
        assert kwargs['include_market_indices'] is True
        raise RuntimeError('collection reached')
    monkeypatch.setattr(market.NaverResearchSource,'daily',probe)
    monkeypatch.setattr(market,'acquire_research',acquire)
    with pytest.raises(RuntimeError,match='collection reached'):
        daily_run(path,tmp_path/'data',tmp_path/'state.json',as_of='2026-11-06',config_path=config)
    assert set(probes)=={'B','KOSPI'}
