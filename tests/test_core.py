import json

import pandas as pd
import pytest

from trendfollow.demo import generate
from trendfollow.risk import Account, Limits, allocate
from trendfollow.strategy import Rules, screen, validate_prices, wilder_atr
from trendfollow.units import replay


@pytest.fixture
def dataset(tmp_path):
    generate(tmp_path)
    p = pd.read_csv(tmp_path/'prices.csv', dtype={'symbol': str})
    u = pd.read_csv(tmp_path/'universe.csv', dtype={'symbol': str})
    return p, u


def test_future_rows_cannot_change_historical_result(dataset):
    p, u = dataset
    before = screen(p, u, '2026-11-06')
    future = p.groupby('symbol', sort=False).tail(1).copy()
    future['date'] = '2026-11-09'
    future[['open','high','low','close']] *= 100
    pd.testing.assert_frame_equal(before, screen(pd.concat([p, future]), u, '2026-11-06'))


def test_breakout_uses_previous_high_and_synthetic_has_two_signals(dataset):
    p, u = dataset
    result = screen(p, u, '2026-11-06').set_index('symbol')
    assert result.signal.sum() == 2
    assert result.loc['DEMO01', 'prior_breakout_high'] < result.loc['DEMO01', 'reference_price']
    assert not result.loc['DEMO03', 'signal']


def test_stale_or_missing_sessions_are_not_signals(dataset):
    p, u = dataset
    p = p[~((p.symbol == 'DEMO01') & (p.date == '2026-11-06'))]
    p = p[~((p.symbol == 'DEMO02') & (p.date == '2026-10-19'))]
    result = screen(p, u, '2026-11-06').set_index('symbol')
    assert result.loc['DEMO01', 'reasons'] == 'stale_prices_or_benchmark'
    assert result.loc['DEMO02', 'reasons'] == 'missing_or_misaligned_sessions'


def test_duplicate_and_bad_ohlc_are_rejected(dataset):
    p, _ = dataset
    with pytest.raises(ValueError, match='중복'):
        validate_prices(pd.concat([p, p.iloc[:1]]))
    p.loc[0, 'low'] = p.loc[0, 'high']*2
    with pytest.raises(ValueError, match='OHLC'):
        validate_prices(p)


def test_wilder_seed_and_update():
    f = pd.DataFrame({'high':[11,14,16], 'low':[9,10,12], 'close':[10,13,15]})
    assert wilder_atr(f, 2) == pytest.approx(3.5)


def candidates():
    return pd.DataFrame([
        dict(symbol='A', sector='ONE', signal=True, reference_price=10000., stop_price=9200.,
             rs_percentile=100., avg_turnover=1e10),
        dict(symbol='B', sector='ONE', signal=True, reference_price=10000., stop_price=9200.,
             rs_percentile=90., avg_turnover=1e10)])


def test_shared_cash_and_risk_are_never_double_spent():
    account = Account(1000000, 1000000)
    limits = Limits(max_trade_risk=.02, units=5, total_risk=.025, max_position=.9,
                    max_sector=.9, cash_reserve=.1)
    result = allocate(candidates(), account, limits)
    assert result.planned_risk.sum() <= 25000
    assert result.cash_required.sum() <= 900000
    assert result.quantity.iloc[1] < result.quantity.iloc[0]


def test_units_scale_risk_not_stop_distance():
    limits = Limits(cost_rate=0, entry_slippage=0, exit_slippage=0, max_trade_risk=.02)
    result = allocate(candidates().head(1), Account(10000000,10000000), limits)
    assert result.quantity.iloc[0] == 50
    assert result.planned_risk.iloc[0] == 40000


def test_existing_position_sector_exposure_and_pending_orders():
    account = Account(1000000, 600000, positions=[dict(symbol='C', sector='ONE',
        quantity=40, current_price=10000., stop_price=9900.)])
    assert allocate(candidates(), account).quantity.sum() == 0
    pending = Account(1000000,1000000,pending_cash=100)
    result = allocate(candidates(), pending)
    assert result.quantity.sum() == 0
    assert set(result.allocation_reason) == {'pending_orders_require_reconciliation'}


def test_invalid_account_and_nan_limits():
    with pytest.raises(ValueError):
        allocate(candidates(), Account(100, 101))
    with pytest.raises(ValueError):
        allocate(candidates(), Account(100, 100), Limits(cost_rate=float('nan')))


def test_duplicate_success_is_once_and_post_success_loss_policy():
    def event(eid, trade, kind, time):
        return dict(event_id=eid,trade_id=trade,kind=kind,timestamp=time)
    a = event('1','A','success_3r','2026-11-01')
    events = [a, a, event('2','A','success_3r','2026-11-02'),
              event('3','A','loss_closed','2026-11-03')]
    assert replay(events) == 2
    with pytest.raises(ValueError):
        replay(events+[event('4','A','success_3r','2026-11-04')])


def test_csv_symbols_keep_leading_zero(tmp_path):
    from trendfollow.cli import read_universe
    p = tmp_path/'universe.csv'
    p.write_text('symbol,name,sector,benchmark\n005930,Test,Sector,069500\n',encoding='utf-8')
    u = read_universe(p)
    assert u.symbol.iloc[0] == '005930'
    assert u.benchmark.iloc[0] == '069500'


def test_end_to_end_reports(tmp_path):
    from trendfollow.cli import main
    main(['demo', '--out', str(tmp_path)])
    report = json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    assert report['synthetic'] is True
    assert report['candidate_count'] == 2
    assert report['mode'] == 'research_only'
    assert (tmp_path/'candidates.csv').exists()


def test_future_events_do_not_increase_historical_size(tmp_path):
    from trendfollow.cli import run_scan
    generate(tmp_path)
    path = tmp_path/'events.json'
    path.write_text(json.dumps([dict(event_id='future',trade_id='T',kind='success_3r',
                                    timestamp='2026-11-09T15:30:00+09:00')]),encoding='utf-8')
    run_scan(tmp_path/'prices.csv', tmp_path/'universe.csv', tmp_path/'account.json',
             '2026-11-06', tmp_path/'result', events=path)
    report = json.loads((tmp_path/'result'/'report.json').read_text(encoding='utf-8'))
    assert report['units'] == 1


def test_incomplete_week_does_not_change_weekly_trend(dataset):
    p, u = dataset
    before = screen(p, u, '2026-11-05').set_index('symbol').weekly_trend
    # This week's prices change dramatically, but its Friday has not completed.
    changed = p.copy()
    changed.loc[changed.date >= '2026-11-02', ['open','high','low','close']] *= .01
    after = screen(changed, u, '2026-11-05').set_index('symbol').weekly_trend
    pd.testing.assert_series_equal(before.sort_index(), after.sort_index())


def test_breached_existing_stop_blocks_new_allocation():
    account = Account(1000000,900000,positions=[dict(symbol='C',sector='OTHER',quantity=10,
                      current_price=10000.,stop_price=10500.)])
    result = allocate(candidates(), account)
    assert result.quantity.sum() == 0
    assert set(result.allocation_reason) == {'existing_stop_breached_review_exit_first'}


def test_missing_date_is_not_silently_dropped(dataset):
    p, _ = dataset
    p.loc[0,'date'] = None
    with pytest.raises(ValueError,match='거래일'):
        validate_prices(p)
