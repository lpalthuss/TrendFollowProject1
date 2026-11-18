from datetime import datetime
import sys
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest

from trendfollow import candidate_universe as candidates
from trendfollow import krx_open_api
from trendfollow import pykrx_universe
from trendfollow.operations import Operations
from trendfollow.config import Rules
from dataclasses import asdict


def test_snapshot_publish_retries_transient_windows_lock(tmp_path, monkeypatch):
    stage = tmp_path / 'staged'
    target = tmp_path / 'published'
    stage.mkdir()
    original = Path.rename
    attempts = []

    def once_locked(path, destination):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError('temporary file lock')
        return original(path, destination)

    monkeypatch.setattr(Path, 'rename', once_locked)
    monkeypatch.setattr(candidates.clock_time, 'sleep', lambda _seconds: None)
    candidates._publish_directory(stage, target)
    assert target.is_dir()
    assert len(attempts) == 2


def test_daily_candidate_snapshots_are_immutable_and_record_removed_reasons(tmp_path, monkeypatch):
    selections = iter([
        (
            pd.DataFrame([dict(symbol='000001', name='A', sector='S'), dict(symbol='000002', name='B', sector='S')]),
            pd.DataFrame([dict(symbol='000003', reasons='recent_turnover_proxy_below_threshold')]),
        ),
        (
            pd.DataFrame([dict(symbol='000002', name='B', sector='S'), dict(symbol='000003', name='C', sector='S')]),
            pd.DataFrame([dict(symbol='000001', reasons='halted')]),
        ),
    ])
    monkeypatch.setattr(candidates, 'download_master', lambda _directory: ['master'])
    monkeypatch.setattr(candidates, 'build_universe', lambda _frames, _threshold: next(selections))
    first = datetime.fromisoformat('2026-11-13T18:00:00+09:00')
    second = datetime.fromisoformat('2026-11-16T18:00:00+09:00')
    with pytest.raises(ValueError, match='직전 완료 거래일'):
        candidates.capture_selection(tmp_path, '2026-11-12', first)
    with pytest.raises(ValueError, match='소급할 수 없습니다'):
        candidates.capture_selection(tmp_path, '2026-11-13', datetime.fromisoformat('2026-11-14T10:00:00+09:00'))
    one = candidates.capture_selection(tmp_path, '2026-11-13', first)
    assert one['count'] == 2 and not one['comparison_available']
    two = candidates.capture_selection(tmp_path, '2026-11-16', second)
    assert two['added'] == two['removed'] == 1
    detail = candidates.read_selection(tmp_path, '2026-11-16')
    assert isinstance(detail['members'][0]['symbol'], str)
    assert detail['added'][0]['symbol'] == '000003'
    assert detail['removed'][0]['symbol'] == '000001'
    assert detail['removed'][0]['reason'] == 'halted'
    assert detail['removed'][0]['reason_display'] == '거래정지'
    assert '최근 거래대금 추정액' in detail['added'][0]['reason_display']
    assert '→' not in detail['added'][0]['reason_display']
    assert '2026-11' not in detail['added'][0]['reason_display']
    assert '선정 기준 충족' not in detail['added'][0]['reason_display']
    assert candidates.capture_selection(tmp_path, '2026-11-13', first) == one
    assert candidates.available_selections(tmp_path) == ['2026-11-16', '2026-11-13']


def test_candidate_reason_display_keeps_threshold_separate_from_approximate_amount():
    assert candidates._reason_label('daily_turnover_below_threshold') == '거래대금 기준 미만'
    assert candidates._reason_label('no_regular_trading,daily_turnover_below_threshold') == '해당 날짜 정규 거래 없음'
    assert candidates._approx_amount(2_432_000_000) == '24억원가량'


def test_display_selection_uses_only_industry_membership_saved_by_that_date(tmp_path):
    folder = candidates.selection_home(tmp_path) / '2026-11-11'
    folder.mkdir(parents=True)
    pd.DataFrame([dict(symbol='000001', name='A', sector='UNCLASSIFIED:000001', benchmark='069500'),
                  dict(symbol='000002', name='B', sector='UNCLASSIFIED:000002', benchmark='069500')]).to_csv(
                      folder / 'universe.csv', index=False)
    pd.DataFrame([dict(symbol='000001', name='A', sector='UNCLASSIFIED:000001',
                       benchmark='069500', recent_turnover_proxy=2_000_000_000,
                       reason='신규 편입')]).to_csv(folder / 'added.csv', index=False)
    pd.DataFrame(columns=['symbol', 'reason']).to_csv(folder / 'removed.csv', index=False)
    pd.DataFrame([dict(symbol='000002', name='B', sector='UNCLASSIFIED:000002',
                       benchmark='069500', recent_turnover_proxy=500_000_000,
                       reasons='daily_turnover_below_threshold')]).to_csv(folder / 'excluded.csv', index=False)
    (folder / 'summary.json').write_text('{"as_of":"2026-11-11","count":2}', encoding='utf-8')
    for day, name in [('2026-11-10', '이전 산업'), ('2026-11-12', '미래 산업')]:
        target = tmp_path / 'data' / 'industries' / 'snapshots' / day
        target.mkdir(parents=True)
        pd.DataFrame([dict(symbol='000001', sector='KOSPI:1', industry_name=name)]).to_csv(
            target / 'memberships.csv', index=False)
    displayed = candidates.display_selection(tmp_path, '2026-11-11', include_excluded=True)
    assert displayed['summary']['industry_classification_as_of'] == '2026-11-10'
    assert displayed['summary']['industry_classified'] == 1
    assert displayed['members'][0]['industry_name'] == '이전 산업'
    assert displayed['members'][1]['sector'] == 'UNCLASSIFIED:000002'
    assert displayed['added'][0]['industry_name'] == '이전 산업'
    assert displayed['excluded'][0]['reason_display'] == '거래대금 기준 미만'
    assert candidates.read_selection(tmp_path, '2026-11-11')['members'][0]['sector'] == 'UNCLASSIFIED:000001'


@pytest.mark.parametrize('mode', ['normal', 'recalculation'])
@pytest.mark.parametrize('source', ['kis', 'krx_open_api', 'pykrx'])
def test_candidate_job_uses_latest_completed_session_instead_of_form_date(tmp_path, monkeypatch, mode, source):
    market=tmp_path/'data'/'market'
    market.mkdir(parents=True)
    (market/'universe.csv').write_text('symbol,benchmark\n000001,069500\n')
    now=datetime.fromisoformat('2026-11-16T10:00:00+09:00')
    captured=[]
    monkeypatch.setattr(candidates, 'capture_selection', lambda _root, as_of, _now, _progress: captured.append(as_of) or {'as_of':as_of})
    monkeypatch.setattr(candidates, 'capture_krx_missing', lambda _root, _now, _progress, from_as_of=None, source=None: captured.append(source) or {'source':source})
    ops=Operations(tmp_path,clock=lambda:now)
    try:
        w=ops.create_workspace({'name':'일별 후보 확인','rules':{},'limits':{}})
        if mode == 'recalculation':
            ops.update_workspace(dict(w, mode='recalculation'))
        with pytest.raises(ValueError, match='자료 원천이 전달되지 않았습니다'):
            ops.submit({'workspace':w['id'],'action':'candidate_universe','as_of':'2026-11-10'})
        job=ops.submit({'workspace':w['id'],'action':'candidate_universe','as_of':'2026-11-10',
                        'candidate_source':source})
        assert job['as_of']=='2026-11-13'
        assert job['candidate_source']==source
        ops.pool.shutdown(wait=True)
        assert captured==([source] if source!='kis' else ['2026-11-13'])
    finally:
        ops.close()


def test_weekend_date_list_marks_historical_capture_unavailable(tmp_path):
    market = tmp_path / 'data' / 'market'
    market.mkdir(parents=True)
    (market / 'universe.csv').write_text('symbol,benchmark\n000001,069500\n')
    ops = Operations(tmp_path, clock=lambda: datetime.fromisoformat('2026-11-14T10:00:00+09:00'))
    try:
        w = ops.create_workspace({'name': '후보 기준일', 'rules': {}, 'limits': {}})
        dates = ops.candidate_dates(w['id'])
        assert dates['capture_as_of'] == '2026-11-13'
        assert dates['capture_available'] is False
        assert dates['candidate_sources']==['kis','krx_open_api','pykrx']
        assert dates['reference_dates'][:4] == ['2026-11-13', '2026-11-12', '2026-11-11', '2026-11-10']
        assert dates['missing_selection_count'] == len(dates['reference_dates'])
        assert dates['suggested_capture_start'] == dates['reference_dates'][-1]
        saved = candidates.selection_home(tmp_path) / '2026-11-11'
        saved.mkdir(parents=True)
        (saved / 'summary.json').write_text('{}', encoding='utf-8')
        updated = ops.candidate_dates(w['id'])
        assert '2026-11-11' not in updated['missing_selection_dates']
        assert updated['missing_selection_count'] == dates['missing_selection_count'] - 1
    finally:
        ops.close()


def test_setup_account_universe_keeps_removed_holding_for_exit_management(tmp_path):
    day='2026-11-13'
    industry=tmp_path/'data'/'industries'
    (industry/'snapshots'/day).mkdir(parents=True)
    original=pd.DataFrame([
        dict(symbol='000001',name='held',sector='OLD',benchmark='069500',industry_peer_history=str(industry/'peer-returns.csv')),
        dict(symbol='000002',name='kept',sector='OLD',benchmark='069500',industry_peer_history=str(industry/'peer-returns.csv')),
    ])
    original.to_csv(industry/'universe.csv',index=False)
    (industry/'peer-returns.csv').write_text('date,symbol,sector,day_return,benchmark_day_return,quote_eligible\n')
    pd.DataFrame([dict(symbol='000002',sector='NEW',industry_name='new industry')]).to_csv(
        industry/'snapshots'/day/'memberships.csv',index=False)
    folder=candidates.selection_home(tmp_path)/day
    folder.mkdir(parents=True)
    pd.DataFrame([dict(symbol='000002',name='kept',market='KOSPI',sector='KOSPI:1',benchmark='069500',recent_turnover_proxy=2e9)]).to_csv(folder/'universe.csv',index=False)
    pd.DataFrame(columns=['symbol','reason']).to_csv(folder/'added.csv',index=False)
    pd.DataFrame(columns=['symbol','reason']).to_csv(folder/'removed.csv',index=False)
    (folder/'summary.json').write_text('{"as_of":"2026-11-13","count":1}',encoding='utf-8')
    ops=Operations(tmp_path)
    try:
        w=ops.create_workspace({'name':'일별 셋업','rules':asdict(Rules(entry_method='setup')),'limits':{}})
        state={'positions':{'000001':{'quantity':1}}}
        path=candidates.account_universe(ops,w,day,state)
        selected=pd.read_csv(path,dtype={'symbol':str}).set_index('symbol')
        assert set(selected.index)=={'000001','000002'}
        assert selected.loc['000001','candidate_active']==False
        assert selected.loc['000002','candidate_active']==True
        assert selected.loc['000002','sector']=='NEW'
    finally:
        ops.close()


def test_setup_account_universe_uses_prior_observed_membership_for_missing_day(tmp_path):
    industry=tmp_path/'data'/'industries'
    prior=industry/'snapshots'/'2026-11-13'
    future=industry/'snapshots'/'2026-11-17'
    prior.mkdir(parents=True)
    future.mkdir(parents=True)
    pd.DataFrame([dict(symbol='000002',sector='PRIOR',industry_name='prior')]).to_csv(
        prior/'memberships.csv',index=False)
    pd.DataFrame([dict(symbol='000002',sector='FUTURE',industry_name='future')]).to_csv(
        future/'memberships.csv',index=False)
    (industry/'peer-returns.csv').write_text(
        'date,symbol,sector,day_return,benchmark_day_return,quote_eligible\n',encoding='utf-8')
    folder=candidates.selection_home(tmp_path)/'2026-11-16'
    folder.mkdir(parents=True)
    pd.DataFrame([dict(symbol='000002',name='selected',market='KOSPI',sector='KOSPI:1',
                       benchmark='069500',recent_turnover_proxy=2e9)]).to_csv(
        folder/'universe.csv',index=False)
    pd.DataFrame(columns=['symbol','reason']).to_csv(folder/'added.csv',index=False)
    pd.DataFrame(columns=['symbol','reason']).to_csv(folder/'removed.csv',index=False)
    (folder/'summary.json').write_text('{"as_of":"2026-11-16","count":1}',encoding='utf-8')
    ops=Operations(tmp_path)
    try:
        w=ops.create_workspace({'name':'과거 산업 구성','rules':asdict(Rules(entry_method='setup')),'limits':{}})
        path=candidates.account_universe(ops,w,'2026-11-16',None)
        row=pd.read_csv(path,dtype={'symbol':str}).iloc[0]
        assert row.sector=='PRIOR'
        assert row.industry_membership_observed_on=='2026-11-13'
        assert 'day_return' not in row.index
        assert not (industry/'snapshots'/'2026-11-16').exists()
    finally:
        ops.close()


def test_krx_historical_universe_uses_dated_basic_info_and_daily_turnover(tmp_path, monkeypatch):
    monkeypatch.setenv('KRX_OPEN_API_KEY', 'test-key')
    calls = []
    def fetch(api, day, key):
        calls.append((api, day, key))
        common = '100001' if api.startswith('ksq') else '000001'
        preferred = '100002' if api.startswith('ksq') else '000002'
        trade_common = 'KR7100001000' if api.startswith('ksq') else common
        if api.endswith('base_info'):
            return [
                dict(ISU_CD=trade_common, ISU_SRT_CD=common, ISU_NM='보통주', SECUGRP_NM='주권', KIND_STKCERT_TP_NM='보통주'),
                dict(ISU_SRT_CD=preferred, ISU_NM='우선주', SECUGRP_NM='주권', KIND_STKCERT_TP_NM='우선주'),
            ]
        return [
            dict(ISU_CD=trade_common, ACC_TRDVAL='2000000000', ACC_TRDVOL='100000', TDD_OPNPRC='20000'),
            dict(ISU_CD=preferred, ACC_TRDVAL='3000000000', ACC_TRDVOL='100000', TDD_OPNPRC='30000'),
        ]
    monkeypatch.setattr(krx_open_api, '_request', fetch)
    members, excluded = krx_open_api.fetch_historical_universe(tmp_path, '2026-11-13', 1e9)
    assert set(members.symbol) == {'000001', '100001'}
    assert members.iloc[0].sector == 'UNCLASSIFIED:000001'
    assert members.iloc[0].recent_turnover_proxy == 2e9
    assert 'not_common_stock' in excluded.loc[excluded.symbol.eq('000002'), 'reasons'].iloc[0]
    assert len(calls) == 4 and {day for _, day, _ in calls} == {'2026-11-13'}


def test_krx_missing_dates_are_saved_in_order_without_replacing_existing_snapshots(tmp_path, monkeypatch):
    captured = []
    def fetch(_root, day, _threshold, _progress):
        captured.append(day)
        members = pd.DataFrame([dict(symbol='000001', name='A', market='KOSPI',
                                     sector='UNCLASSIFIED:000001', benchmark='069500',
                                     recent_turnover_proxy=2e9)])
        return members, pd.DataFrame(columns=['symbol', 'reasons'])
    monkeypatch.setattr(krx_open_api, 'fetch_historical_universe', fetch)
    now = datetime.fromisoformat('2026-11-14T10:00:00+09:00')
    result = candidates.capture_krx_missing(tmp_path, now, from_as_of='2026-11-10')
    assert captured == ['2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13']
    assert result['saved_dates'] == captured
    summary = candidates.read_selection(tmp_path, '2026-11-13')['summary']
    assert summary['source'] == 'KRX Open API'
    assert summary['previous_as_of'] == '2026-11-12'
    assert candidates.capture_krx_missing(tmp_path, now, from_as_of='2026-11-10')['saved_dates'] == []
    assert len(captured) == 4


def test_krx_open_api_rejection_is_clear_and_does_not_expose_key(monkeypatch):
    class Rejected:
        status_code = 401
    monkeypatch.setattr(krx_open_api.requests, 'get', lambda *args, **kwargs: Rejected())
    with pytest.raises(ValueError, match='유가증권 종목기본정보.*이용 신청·승인 상태') as error:
        krx_open_api._request('stk_isu_base_info', '2026-11-13', 'secret-test-key')
    assert 'secret-test-key' not in str(error.value)


def test_pykrx_historical_source_uses_dated_tickers_and_turnover(tmp_path, monkeypatch):
    monkeypatch.setattr(pykrx_universe, 'configure_krx_credentials', lambda _path: None)
    kospi = [f'{ticker:06d}' for ticker in range(100, 200)]
    kosdaq = [f'{ticker:06d}' for ticker in range(1100, 1200)]
    def tickers(_day, market):
        return kospi if market == 'KOSPI' else kosdaq
    def bars(_day, market):
        symbols = tickers(_day, market)
        return pd.DataFrame({'시가': [1000] * 100, '거래량': [2] * 100,
                             '거래대금': [2e9] * 100}, index=symbols)
    def name(symbol):
        return '테스트우' if symbol == kospi[0] else '테스트스팩' if symbol == kosdaq[0] else f'종목{symbol}'
    monkeypatch.setitem(sys.modules, 'pykrx', SimpleNamespace(
        stock=SimpleNamespace(get_market_ticker_list=tickers,
                              get_market_ohlcv_by_ticker=bars,
                              get_market_ticker_name=name)))
    now = datetime.fromisoformat('2026-11-14T10:00:00+09:00')
    result = candidates.capture_krx_missing(tmp_path, now, from_as_of='2026-11-13', source='pykrx')
    assert result['saved_dates'] == ['2026-11-13']
    selected = candidates.read_selection(tmp_path, '2026-11-13')
    assert selected['summary']['source'] == 'pykrx'
    assert selected['summary']['count'] == 198
    assert {r['symbol']: r['reason'] for r in selected['removed']} == {}
    assert candidates.capture_krx_missing(tmp_path, now, from_as_of='2026-11-13', source='krx_open_api')['saved_dates'] == []
