from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from trendfollow.backtest import features
from trendfollow.relative_strength import decision_rule_settings
from trendfollow.setup import sector_features, setup_features
from trendfollow.strategy import Rules, screen


def pattern(base_bars=4,base_low=139):
    rows=[(101,102,100,101,800),(106,112,105,110,1000),(116,125,115,123,1200),
          (136,150,135,149,1500),(145,148,base_low,144,1000),(144,146,139,143,900)]
    for i in range(base_bars-2):
        width=3*.7**i
        rows.append((144,145+width/2,145-width/2,145,300*.7**i))
    rows.append((148,154,147,153,4000))
    frame=pd.DataFrame(rows,columns=['open','high','low','close','volume'])
    frame.index=pd.bdate_range('2026-08-03',periods=len(frame))
    return frame


@pytest.mark.parametrize('base_bars',[4,12,25])
def test_variable_duration_base_has_same_process(base_bars):
    f=pattern(base_bars)
    result=setup_features(f,Rules(entry_method='setup'),atr_values=np.ones(len(f)))
    latest=result.iloc[-1]
    assert latest.setup_entry
    assert latest.setup_base_bars==base_bars
    assert latest.setup_resistance==150
    assert latest.setup_wave_low==100
    assert latest.setup_retracement==pytest.approx(.22)


def test_half_wave_retracement_is_not_half_of_peak_price():
    f=pattern(base_low=125)
    good=setup_features(f,Rules(),atr_values=np.ones(len(f)))
    assert good.iloc[-1].setup_retracement==pytest.approx(.5)
    assert good.iloc[-1].setup_entry
    deep=pattern(base_low=124)
    assert not setup_features(deep,Rules(),atr_values=np.ones(len(deep))).iloc[-1].setup_entry


def test_trigger_day_volume_does_not_enter_base_contraction_statistics():
    f=pattern()
    a=setup_features(f,Rules(),atr_values=np.ones(len(f)))
    f.loc[f.index[-1],'volume']=1e9
    b=setup_features(f,Rules(),atr_values=np.ones(len(f)))
    assert a.iloc[-1].setup_volume_ratio==b.iloc[-1].setup_volume_ratio


def test_future_bars_cannot_relabel_old_pivots_or_signals():
    f=pattern()
    before=setup_features(f,Rules(),atr_values=np.ones(len(f)))
    extended=pd.concat([f,pd.DataFrame([dict(open=300,high=400,low=250,close=350,volume=99999)],
                        index=[f.index[-1]+pd.offsets.BDay()])])
    after=setup_features(extended,Rules(),atr_values=np.ones(len(extended)))
    pd.testing.assert_frame_equal(before,after.iloc[:-1],check_freq=False)
    assert pd.Timestamp(before.iloc[-1].setup_confirmed_at)>pd.Timestamp(before.iloc[-1].setup_peak_date)


def test_no_contraction_or_unhealthy_trigger_bar_rejects_entry():
    f=pattern()
    f.loc[f.index[-3:-1],'volume']=3000
    assert not setup_features(f,Rules(),atr_values=np.ones(len(f))).iloc[-1].setup_entry
    f=pattern();f.loc[f.index[-1],'low']=124
    row=setup_features(f,Rules(),atr_values=np.ones(len(f))).iloc[-1]
    assert row.setup_trigger and row.setup_invalidated and not row.setup_entry
    assert row.setup_retracement_today > Rules().setup_max_retracement


def test_same_day_setup_invalidation_and_52_week_breakout_can_coexist():
    recent = pattern()
    recent.loc[recent.index[-1],'low'] = 124
    warm = pd.DataFrame([dict(open=100,high=101,low=99,close=100,volume=1000)]*252,
        index=pd.bdate_range(end=recent.index[0]-pd.offsets.BDay(),periods=252))
    prices = pd.concat([warm,recent])
    setup = setup_features(prices,Rules(entry_method='setup'),atr_values=np.ones(len(prices))).iloc[-1]
    prior_high = prices.high.shift().rolling(252).max().iloc[-1]
    assert setup.setup_invalidated and setup.setup_trigger and not setup.setup_entry
    assert prices.close.iloc[-1] > prior_high


def sector_input(returns):
    rows=[]
    for symbol,ret in [('INDEX',.001),*returns.items()]:
        for day,price in [('2026-08-03',100.),('2026-08-04',100*(1+ret))]:
            rows.append(dict(date=day,symbol=symbol,open=price,high=price+1,low=price-1,close=price,volume=100))
    u=pd.DataFrame([dict(symbol=s,sector='S',benchmark='INDEX') for s in returns])
    return pd.DataFrame(rows),u


def test_sector_breadth_excludes_the_candidate_itself():
    p,u=sector_input({'A':.5,'B':-.01,'C':-.01,'D':-.01})
    result=sector_features(p,u,Rules()).set_index(['date','symbol'])
    own=result.loc[(pd.Timestamp('2026-08-04'),'A')]
    assert own.sector_peer_count==3 and own.sector_up_fraction==0
    assert not own.sector_action
    p,u=sector_input({'A':.5,'B':.01,'C':.01,'D':.01})
    own=sector_features(p,u,Rules()).query("symbol=='A'").iloc[-1]
    assert own.sector_up_fraction==1 and own.sector_action
    assert own.sector_excess_return==pytest.approx(.009)


def test_core_and_backtest_use_same_setup_and_sector_rules():
    f=pattern()
    warm=pd.DataFrame([dict(open=100,high=101,low=99,close=100,volume=1000)]*35,
                     index=pd.bdate_range(end=f.index[0]-pd.offsets.BDay(),periods=35))
    stock=pd.concat([warm,f]).rename_axis('date').reset_index();stock['symbol']='A'
    benchmark=stock.copy();benchmark['symbol']='INDEX'
    benchmark[['open','high','low','close']]=100
    prices=pd.concat([stock,benchmark],ignore_index=True)
    prices['date']=prices.date.dt.strftime('%Y-%m-%d')
    u=pd.DataFrame([dict(symbol='A',name='A',sector='S',benchmark='INDEX')])
    rules=Rules(entry_method='setup',rs_days=5,weekly_ma=2,weekly_slope=1,atr_days=2,
                setup_reversal_atr=1,setup_min_rise_atr=2,sector_require_action=False,
                min_turnover=1,volume_multiple=1)
    asof=str(f.index[-1].date())
    current=screen(prices,u,asof,rules).iloc[0]
    historical=features(prices,u,rules)[0].iloc[-1]
    assert current.setup_entry and current.signal
    for column in ['setup_resistance','setup_retracement','setup_volume_ratio','setup_range_ratio',
                   'setup_trigger','setup_entry','signal_rs','signal_mansfield','signal']:
        a,b=current[column],historical[column]
        assert (pd.isna(a) and pd.isna(b)) or a==pytest.approx(b)


def test_52_week_breakout_is_an_independent_opt_in_setup_entry_path():
    dates = pd.bdate_range('2025-11-17', periods=255)
    close = np.array([100 + i * .4 for i in range(254)] + [210.])
    stock = pd.DataFrame(dict(date=dates, symbol='A', open=close, high=close+1,
                              low=close-1, close=close, volume=[1000]*254+[2000]))
    benchmark = stock.copy()
    benchmark['symbol'] = 'INDEX'
    benchmark[['open','high','low','close']] = 100.
    prices = pd.concat([stock,benchmark],ignore_index=True)
    universe = pd.DataFrame([dict(symbol='A',name='A',sector='S',benchmark='INDEX')])
    options = dict(entry_method='setup', rs_days=5, weekly_ma=2, weekly_slope=1,
                   atr_days=2, min_rs=0, min_turnover=1, volume_multiple=1,
                   sector_require_action=False)
    as_of = str(dates[-1].date())
    old = screen(prices,universe,as_of,Rules(**options)).iloc[0]
    enabled = Rules(**options,setup_52w_enabled=True)
    current = screen(prices,universe,as_of,enabled).iloc[0]
    historical = features(prices,universe,enabled)[0].iloc[-1]
    assert not old.signal and old.high52_breakout and not old.setup_entry
    assert current.high52_breakout and not current.setup_entry and current.signal
    assert current.signal_entry_path == '52주 신고가'
    assert current.prior_52w_high == pytest.approx(stock.high.iloc[1:-1].max())
    assert historical.high52_breakout and historical.signal


def test_52_week_option_requires_boolean():
    with pytest.raises(ValueError, match='setup_52w_enabled'):
        Rules(entry_method='setup',setup_52w_enabled='true').validate()


def test_existing_breakout_account_settings_ignore_new_setup_options():
    baseline=decision_rule_settings(Rules())
    assert decision_rule_settings(Rules(setup_max_retracement=.4))==baseline
    assert decision_rule_settings(Rules(entry_method='setup'))!=baseline


def test_missing_session_resets_the_wave_instead_of_bridging_unknown_prices():
    full=pattern(12)
    missing=full.drop(full.index[7])
    result=setup_features(missing,Rules(),atr_values=np.ones(len(missing)),calendar=full.index)
    assert not result.iloc[-1].setup_entry


def test_same_setup_does_not_trigger_again_on_next_rising_bar():
    f=pattern()
    extra=pd.DataFrame([dict(open=154,high=157,low=153,close=156,volume=5000)],
                       index=[f.index[-1]+pd.offsets.BDay()])
    f=pd.concat([f,extra])
    result=setup_features(f,Rules(),atr_values=np.ones(len(f)))
    assert result.setup_entry.sum()==1
    assert result.iloc[-2].setup_entry and not result.iloc[-1].setup_trigger


@pytest.mark.parametrize('rules',[Rules(entry_method='x'),Rules(setup_max_retracement=2),
    Rules(setup_reversal_atr=0),Rules(sector_min_peers=0),Rules(sector_min_breadth=float('nan'))])
def test_invalid_setup_options_are_rejected(rules):
    with pytest.raises(ValueError):
        rules.validate()
