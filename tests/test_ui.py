import copy
from dataclasses import asdict
import http.client
import json
from pathlib import Path
import shutil
import threading
import time
from datetime import datetime

import pandas as pd
import pytest

from trendfollow.demo import generate
from trendfollow.risk import Limits
from trendfollow.strategy import Rules
from trendfollow.ui_service import Operations
from trendfollow.ui_server import make_server


def test_guidance_distinguishes_missing_industry_processed_day_and_date_gates(ops,project,monkeypatch):
    from trendfollow import ui_service
    class AfterClose(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,11,9,18,0,tzinfo=tz)
    monkeypatch.setattr(ops,'clock',lambda:AfterClose.now(ui_service.KST))
    w=create(ops)
    g=ops.guidance(w['id'])
    assert g['as_of']=='2026-11-09' and g['industry_ready'] and not g['processed']
    assert ops.guidance(w['id'],'2026-11-10')['blocked']
    assert ops.guidance(w['id'],'2026-11-08')['blocked']
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert job['status']=='completed'
    assert ops.guidance(w['id'],'2026-11-06')['processed']
    assert ops.guidance(w['id'],'2026-11-05')['blocked']
    directory=project/'data/industries';directory.mkdir()
    u=pd.read_csv(project/'data/market/universe.csv')
    peer=directory/'peer-returns.csv'
    u['industry_peer_history']=str(peer);u.to_csv(directory/'universe.csv',index=False)
    setup=create(ops,rules=asdict(Rules(entry_method='setup')))
    assert not ops.guidance(setup['id'])['industry_ready']
    pd.DataFrame({'date':['2026-11-06']}).to_csv(peer,index=False)
    assert not ops.guidance(setup['id'])['industry_ready']
    pd.DataFrame({'date':['2026-11-09']}).to_csv(peer,index=False)
    assert ops.guidance(setup['id'])['industry_ready']


def test_before_close_guidance_defaults_to_previous_completed_weekday(ops,monkeypatch):
    from trendfollow import ui_service
    class BeforeClose(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,11,9,15,40,tzinfo=tz)
    monkeypatch.setattr(ops,'clock',lambda:BeforeClose.now(ui_service.KST))
    w=create(ops)
    assert ops.guidance(w['id'])['as_of']=='2026-11-06'
    assert ops.guidance(w['id'],'2026-11-09')['blocked']


@pytest.fixture
def project(tmp_path):
    demo=tmp_path/'demo';generate(demo)
    (tmp_path/'examples').mkdir()
    (tmp_path/'examples/config.json').write_text(json.dumps(dict(rules={},limits={})))
    prices=tmp_path/'data/market/history';prices.mkdir(parents=True)
    shutil.copyfile(demo/'prices.csv',prices/'prices.csv')
    shutil.copyfile(demo/'universe.csv',tmp_path/'data/market/universe.csv')
    return tmp_path


@pytest.fixture
def ops(project):
    operations=Operations(project)
    yield operations
    operations.close()


def create(ops,**extra):
    return ops.create_workspace({**dict(name='독립 모의 계좌',rules=asdict(Rules()),limits=asdict(Limits())),**extra})


def wait_job(ops,job):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        result=copy.deepcopy(ops.jobs[job['id']])
        if result['status'] not in {'queued','running'}:return result
        time.sleep(.02)
    pytest.fail('UI job timed out')


def test_watch_job_collects_missing_close_and_saves_plan_without_account_processing(ops, monkeypatch):
    import trendfollow.market as market

    monkeypatch.setattr(ops, 'clock', lambda: datetime.fromisoformat('2026-11-09T13:00:00+09:00'))
    w = create(ops, rules=asdict(Rules(entry_method='setup', rs_method='mansfield')))
    original = ops.path(w['data_dir']) / 'prices.csv'
    daily = pd.read_csv(original, dtype={'symbol': str})
    original.write_bytes(daily[daily.date.lt('2026-11-06')].to_csv(index=False).encode())
    universe = ops.path(w['universe'])
    universe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ops.root/'data/market/universe.csv', universe)
    symbol = pd.read_csv(universe, dtype=str).symbol.iloc[0]

    def collect(universe, out, start, end, **kwargs):
        members = pd.read_csv(universe, dtype=str)
        assert members.symbol.tolist() == [symbol]
        assert end == kwargs['required_session'] == '2026-11-06'
        out.mkdir()
        daily[daily.symbol.isin({symbol, *members.benchmark})].to_csv(out/'prices.csv', index=False)
        return {'published': True}

    monkeypatch.setattr(market, 'acquire_research', collect)
    payload = dict(workspace=w['id'], action='prepare_intraday', as_of='2026-11-06',
                   session='2026-11-09', symbols=[symbol])
    blocked = wait_job(ops, ops.submit(dict(payload, watch_mode='industry')))
    assert blocked['status'] == 'failed' and '산업 구성 자료' in blocked['error']
    result = wait_job(ops, ops.submit(dict(payload, watch_mode='symbols')))
    assert result['status'] == 'completed', result
    plan, = ops.plans()
    assert plan['workspace'] == w['id'] and plan['as_of'] == '2026-11-06'
    assert plan['targets'] == [symbol] and plan['subscriptions'] == 1
    assert not ops.path(w['state']).exists()


def test_create_new_configuration_never_overwrites_templates_or_initializes_account(ops,project):
    template=(project/'examples/config.json').read_bytes()
    w=create(ops)
    assert (project/'examples/config.json').read_bytes()==template
    assert not ops.path(w['state']).exists()
    snap=ops.snapshot(w['id'])
    assert snap['status']=='not_started' and snap['rows']==[]
    assert snap['cash']==10000000 and snap['files_ready']


def test_account_archive_preserves_history_and_can_be_restored(ops):
    w=create(ops)
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert job['status']=='completed'
    before=ops.path(w['state']).read_bytes()
    summary=next(row for row in ops.workspace_summaries() if row['id']==w['id'])
    assert summary['account_as_of']=='2026-11-06'
    assert not summary.get('archived_at')
    archived=ops.set_archived(w['id'],True)
    assert archived['archived_at']
    assert ops.path(w['state']).read_bytes()==before
    assert ops.workspace(w['id'])['archived_at']==archived['archived_at']
    assert ops.set_archived(w['id'],True)['archived_at']==archived['archived_at']
    assert 'archived_at' not in ops.set_archived(w['id'],False)
    assert ops.path(w['state']).read_bytes()==before
    with pytest.raises(ValueError,match='기본 계좌'):ops.set_archived('baseline',True)
    with pytest.raises(ValueError,match='보관 상태'):ops.set_archived(w['id'],'true')


def test_account_rename_changes_only_registry_name(ops):
    w=create(ops)
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert job['status']=='completed'
    state_before=ops.path(w['state']).read_bytes()
    config_before=ops.path(w['config']).read_bytes()
    renamed=ops.rename_workspace(w['id'],'  새 이름  ')
    assert renamed['name']=='새 이름'
    assert renamed['id']==w['id']
    assert ops.workspace(w['id'])['name']=='새 이름'
    assert ops.path(w['state']).read_bytes()==state_before
    assert ops.path(w['config']).read_bytes()==config_before
    assert ops.snapshot(w['id'])['status']=='ready'
    with pytest.raises(ValueError,match='기본 계좌'):ops.rename_workspace('baseline','새 이름')
    with pytest.raises(ValueError,match='1~60자'):ops.rename_workspace(w['id'],'  ')
    with pytest.raises(ValueError,match='1~60자'):ops.rename_workspace(w['id'],'가'*61)
    with pytest.raises(ValueError,match='찾을 수 없습니다'):ops.rename_workspace('missing','새 이름')


def test_invalid_risk_and_pyramiding_configuration_is_rejected_before_saving(ops):
    with pytest.raises(ValueError,match='2%'):create(ops,limits=asdict(Limits(max_trade_risk=.03)))
    with pytest.raises(ValueError):create(ops,rules=asdict(Rules(pyramiding_enabled=True)))
    assert not ops.registry.exists()


@pytest.mark.parametrize('value',['.env','../secret.json','data/../../secret.csv','.'])
def test_paths_cannot_expose_credentials_or_escape_project(ops,value):
    with pytest.raises(ValueError):ops.path(value)


def test_analyze_does_not_change_account_and_paper_is_explicit_idempotent_action(ops):
    w=create(ops)
    analysis=wait_job(ops,ops.submit(dict(workspace=w['id'],action='analyze',as_of='2026-11-06')))
    assert analysis['status']=='completed' and not ops.path(w['state']).exists()
    snap=ops.snapshot(w['id'])
    assert snap['rows'] and snap['analysis']['mode']=='planning_only'
    assert all(row['sector_action'] is None for row in snap['rows'])
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert job['status']=='completed'
    before=ops.path(w['state']).read_bytes()
    again=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert again['result']['status']=='already_processed'
    assert ops.path(w['state']).read_bytes()==before
    assert ops.snapshot(w['id'])['status']=='ready'


def test_configuration_drift_hides_old_signals_and_blocks_execution(ops):
    w=create(ops)
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert job['status']=='completed'
    before=ops.path(w['state']).read_bytes()
    config=json.loads(ops.path(w['config']).read_text());config['rules']['min_rs']=90
    ops.path(w['config']).write_text(json.dumps(config))
    snap=ops.snapshot(w['id'])
    assert snap['status']=='configuration_mismatch' and snap['rows']==[]
    with pytest.raises(ValueError,match='설정'):ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06'))
    assert ops.path(w['state']).read_bytes()==before


def test_missing_mansfield_is_unknown_not_failed_and_signal_columns_are_allowlisted(ops):
    w=create(ops,rules=asdict(Rules(rs_method='mansfield')))
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='analyze',as_of='2026-11-06')))
    assert job['status']=='completed'
    file=ops.latest_signals(w);f=pd.read_csv(file)
    f['mansfield_rs']=float('nan')
    f['APP_SECRET']='must-not-be-returned';f.to_csv(file,index=False)
    rows=ops.snapshot(w['id'])['rows']
    assert rows and all(row['rs_pass'] is None for row in rows)
    assert all('APP_SECRET' not in row for row in rows)


def test_old_signals_show_52_week_status_from_saved_prices_without_reprocessing(ops):
    w=create(ops)
    day='2026-11-06'
    dates=pd.bdate_range(end=day,periods=253)
    prices=pd.DataFrame([
        dict(date=str(date.date()),symbol=symbol,high=100+i)
        for symbol,count in [('DEMO01',253),('DEMO02',240)]
        for i,date in enumerate(dates[-count:])
    ])
    prices.to_csv(ops.path(w['data_dir'])/'prices.csv',index=False)
    signal=ops.home/'analyses'/w['id']/day/'signals.csv'
    signal.parent.mkdir(parents=True)
    pd.DataFrame([
        dict(date=day,symbol='DEMO01',reference_price=400),
        dict(date=day,symbol='DEMO02',reference_price=400),
    ]).to_csv(signal,index=False)
    before=signal.read_bytes()
    rows={row['symbol']:row for row in ops.snapshot(w['id'])['rows']}
    assert rows['DEMO01']['prior_52w_high']==351
    assert rows['DEMO01']['high52_breakout'] is True
    assert rows['DEMO02'].get('prior_52w_high') is None
    assert signal.read_bytes()==before
    earlier=pd.DataFrame([
        dict(date=str(date.date()),symbol='DEMO02',high=120)
        for date in dates[:13]
    ])
    pd.concat([prices,earlier]).to_csv(ops.path(w['data_dir'])/'prices.csv',index=False)
    updated={row['symbol']:row for row in ops.snapshot(w['id'])['rows']}
    assert updated['DEMO02']['prior_52w_high']==338
    assert updated['DEMO02']['high52_breakout'] is True
    assert signal.read_bytes()==before


def test_candidate_dates_include_saved_account_days_and_select_without_changing_account(ops):
    w=create(ops)
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert job['status']=='completed', job.get('error')
    before=ops.path(w['state']).read_bytes()
    dates=ops.candidate_dates(w['id'])
    assert '2026-11-06' in dates['signal_dates']
    assert '2026-11-06' in dates['account_analysis_dates']
    selected=ops.snapshot(w['id'],'2026-11-06')
    assert selected['as_of']=='2026-11-06' and selected['rows']
    assert ops.path(w['state']).read_bytes()==before


def test_enabling_daily_candidates_keeps_one_account_and_prior_state(ops):
    from trendfollow.candidate_universe import enable_daily_account

    w=create(ops)
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert job['status']=='completed', job.get('error')
    before=ops.path(w['state']).read_bytes()
    policy=enable_daily_account(ops,w)
    updated=ops.workspace(w['id'])
    assert policy['enabled_after']=='2026-11-06'
    assert updated['id']==w['id'] and updated['data_dir']!=w['data_dir']
    assert updated['universe']!=w['universe']
    assert ops.snapshot(w['id'])['status']=='ready'
    assert ops.path(w['state']).read_bytes()==before
    assert len([row for row in ops.workspaces() if row['id']==w['id']])==1


def test_daily_job_passes_the_dated_selection_into_the_same_account(ops, monkeypatch):
    from trendfollow.candidate_universe import enable_daily_account, selection_home
    from trendfollow.operations import actions

    w=create(ops)
    first=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert first['status']=='completed', first.get('error')
    enable_daily_account(ops,w)
    folder=selection_home(ops.root)/'2026-11-09'
    folder.mkdir(parents=True)
    base=pd.read_csv(ops.path(ops.workspace(w['id'])['universe']),dtype={'symbol':str})
    base.iloc[:2].to_csv(folder/'universe.csv',index=False)
    pd.DataFrame(columns=['symbol','reason']).to_csv(folder/'added.csv',index=False)
    pd.DataFrame(columns=['symbol','reason']).to_csv(folder/'removed.csv',index=False)
    (folder/'summary.json').write_text(json.dumps({'as_of':'2026-11-09','count':2}),encoding='utf-8')
    calls=[]
    monkeypatch.setattr(actions,'daily_run',lambda *args,**kwargs: calls.append((args,kwargs)) or {'status':'processed'})
    monkeypatch.setattr('trendfollow.candidate_universe.backfill_added_history',lambda *args,**kwargs: [])
    monkeypatch.setattr(ops,'clock',lambda:datetime.fromisoformat('2026-11-09T18:00:00+09:00'))
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='daily',as_of='2026-11-09')))
    assert job['status']=='completed', job.get('error')
    assert len(calls)==1
    args,kwargs=calls[0]
    assert args[2]==ops.path(w['state']) and args[3]=='2026-11-09'
    assert '2026-11-09' in kwargs['universe_by_session']
    assert ops.path(w['state']).exists()


def test_old_closed_intraday_signals_are_never_current_and_accounts_are_isolated(ops):
    w=create(ops)
    run=ops.run_path('a'*16)
    run.mkdir(parents=True)
    (run/'plan.json').write_text(json.dumps(dict(ui_workspace_id=w['id'])))
    (run/'summary.json').write_text(json.dumps(dict(connection_state='closed')))
    (run/'events.jsonl').write_text(json.dumps(dict(timestamp='2026-11-06T10:00:00+09:00',entry_confirmed=True))+'\n')
    result=ops.intraday(w['id'])
    assert result['runs'][0]['events'][0]['ui_current'] is False
    assert ops.intraday('different-account')['runs']==[]


@pytest.mark.parametrize(('when', 'open_session'), [
    ('2026-11-13T08:59:00+09:00', False),
    ('2026-11-13T09:00:00+09:00', True),
    ('2026-11-13T15:30:00+09:00', True),
    ('2026-11-13T15:31:00+09:00', False),
    ('2026-11-13T15:40:00+09:00', True),
    ('2026-11-13T19:59:00+09:00', True),
    ('2026-11-13T20:00:00+09:00', False),
    ('2026-11-14T11:00:00+09:00', False),
])
def test_intraday_status_exposes_server_session_clock(ops, monkeypatch, when, open_session):
    monkeypatch.setattr(ops, 'clock', lambda: datetime.fromisoformat(when))
    status = ops.intraday()
    assert status['today'] == when[:10]
    assert status['regular_session'] is open_session


def test_historical_analysis_chart_does_not_show_future_prices(ops):
    w=create(ops)
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='analyze',as_of='2026-11-05')))
    assert job['status']=='completed'
    symbol=ops.snapshot(w['id'])['rows'][0]['symbol']
    rows=ops.prices(w['id'],symbol)
    assert rows and all(row['date']<='2026-11-05' for row in rows)


def test_candidate_detail_exposes_saved_peer_excess_return(ops):
    w=create(ops)
    folder=ops.home/'analyses'/w['id']/'saved'
    folder.mkdir(parents=True)
    pd.DataFrame([dict(symbol='005930',date='2026-11-13',sector_peer_count=162,
                       sector_up_fraction=0.747,sector_excess_return=0.0329)]).to_csv(folder/'signals.csv',index=False)
    row=ops.snapshot(w['id'],'2026-11-13')['rows'][0]
    assert row['sector_peer_count']==162
    assert row['sector_excess_return']==pytest.approx(0.0329)


def test_price_chart_returns_only_fifteen_months_through_selected_day(ops):
    w=create(ops)
    path=ops.path(w['data_dir'])/'prices.csv'
    prices=pd.read_csv(path,dtype={'symbol':str})
    symbol=prices.symbol.iloc[0]
    sample=prices.loc[prices.symbol.eq(symbol)].iloc[[0]].copy()
    additions=[]
    for day in ('2025-08-11','2025-08-12','2026-11-13'):
        row=sample.copy()
        row['date']=day
        additions.append(row)
    pd.concat([prices,*additions],ignore_index=True).to_csv(path,index=False)
    rows=ops.prices(w['id'],symbol,as_of='2026-11-12')
    dates={row['date'] for row in rows}
    assert '2025-08-11' not in dates
    assert '2025-08-12' in dates
    assert '2026-11-13' not in dates


def test_comparison_chart_uses_frozen_account_prices(ops,project):
    original=create(ops)
    comparison=create(ops)
    original_path=ops.path(original['data_dir'])/'prices.csv'
    prices=pd.read_csv(original_path,dtype={'symbol':str})
    symbol=prices.symbol.iloc[0]
    fresh=prices.loc[prices.symbol.eq(symbol)].iloc[[0]].copy()
    fresh['date']='2026-11-13'
    fresh['volume']=123456
    pd.concat([prices,fresh],ignore_index=True).to_csv(original_path,index=False)
    stale=ops.path(comparison['state']).parent/'recalculation-inputs'
    stale.mkdir()
    prices.to_csv(stale/'prices.csv',index=False)
    comparison.update(mode='recalculation',source_workspace=original['id'],
                      data_dir=str(stale.relative_to(project)))
    ops.accounts.update_workspace(comparison)
    rows=ops.prices(comparison['id'],symbol,as_of='2026-11-13')
    expected=prices.loc[prices.symbol.eq(symbol)].sort_values('date').iloc[-1]
    assert rows[-1]['date']==expected['date']
    assert rows[-1]['volume']==expected['volume']
    assert all(row['date']!='2026-11-13' for row in rows)


def test_price_chart_daily_atr_uses_account_wilder_period_and_full_history(ops):
    from trendfollow.indicators import wilder_atr_series
    w=create(ops,rules=asdict(Rules(atr_days=5)))
    prices=pd.read_csv(ops.path(w['data_dir'])/'prices.csv',dtype={'symbol':str})
    symbol=prices.symbol.iloc[0]
    history=prices.loc[prices.symbol.eq(symbol)].sort_values('date')
    history=history.loc[history.date.le('2026-11-12')]
    expected=wilder_atr_series(history,5).iloc[-1]
    rows=ops.prices(w['id'],symbol,as_of='2026-11-12')
    assert rows[-1]['atr']==pytest.approx(expected)
    previous_close=history.close.iloc[-2]
    assert rows[-1]['atr_up_multiple']==pytest.approx(max(0,history.high.iloc[-1]-previous_close)/expected)
    assert rows[-1]['atr_down_multiple']==pytest.approx(max(0,previous_close-history.low.iloc[-1])/expected)
    assert all(row['atr'] is None or row['atr']>0 for row in rows)


def test_serial_job_queue_rejects_overlapping_mutations(ops,monkeypatch):
    w=create(ops);started=threading.Event();release=threading.Event()
    def execute(key,*args):
        ops.jobs[key]['status']='running';started.set();release.wait(3);ops.jobs[key]['status']='completed'
    monkeypatch.setattr(ops,'execute',execute)
    job=ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06'))
    assert started.wait(2)
    try:
        with pytest.raises(ValueError,match='진행 중'):ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06'))
    finally:release.set()
    assert wait_job(ops,job)['status']=='completed'


def test_restart_marks_unfinished_jobs_interrupted_without_running_them(project):
    file=project/'outputs/ui/jobs/job.json';file.parent.mkdir(parents=True)
    file.write_text(json.dumps(dict(id='job',status='running',action='daily',owner_id='terminated-owner')))
    operations=Operations(project)
    try:assert operations.jobs['job']['status']=='interrupted'
    finally:operations.close()


def test_future_date_and_unknown_action_do_not_start_jobs(ops):
    w=create(ops)
    with pytest.raises(ValueError):ops.submit(dict(workspace=w['id'],action='paper',as_of='2099-01-01'))
    with pytest.raises(ValueError):ops.submit(dict(workspace=w['id'],action='real_order',as_of='2026-11-06'))
    assert not ops.jobs


def test_live_watch_cannot_start_without_valid_plan_or_outside_market(ops):
    with pytest.raises(ValueError):ops.start_watch(dict(plan_id='unknown',seconds=300))
    assert not ops.processes


def test_server_cookie_origin_csrf_and_no_secret_file_routes(project):
    server=make_server(project,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    try:
        connection.request('GET','/api/bootstrap');response=connection.getresponse();assert response.status==403;response.read()
        connection.request('GET','/');response=connection.getresponse();assert response.status==200
        cookie=response.getheader('Set-Cookie').split(';')[0];assert b'ui' in response.read()
        connection.request('GET','/app.js')
        response=connection.getresponse()
        assert response.status==200 and response.getheader('Content-Type').startswith('text/javascript')
        assert b'filterRows' in response.read()
        connection.request('GET','/api/bootstrap',headers={'Cookie':cookie})
        response=connection.getresponse();bootstrap=json.loads(response.read());assert response.status==200
        payload=json.dumps(dict(name='UI 계좌',rules={},limits={}))
        connection.request('POST','/api/workspaces',payload,{'Cookie':cookie,'Content-Type':'application/json'})
        response=connection.getresponse();assert response.status==403;response.read()
        headers={'Cookie':cookie,'X-UI-Token':bootstrap['csrf'],'Content-Type':'application/json','Origin':'https://external.example'}
        connection.request('POST','/api/workspaces',payload,headers)
        response=connection.getresponse();assert response.status==403;response.read()
        headers['Origin']=f'http://127.0.0.1:{server.server_port}'
        connection.request('POST','/api/workspaces',payload,headers)
        response=connection.getresponse();assert response.status==200;row=json.loads(response.read())
        assert not (project/row['state']).exists()
        connection.request('GET','/.env',headers={'Cookie':cookie})
        response=connection.getresponse();assert response.status==404;response.read()
        connection.request('GET','/api/bootstrap',headers={'Host':'rebind.example','Cookie':cookie})
        response=connection.getresponse();assert response.status==403;response.read()
    finally:
        connection.close();server.shutdown();server.server_close();server.operations.close();thread.join(timeout=3)

def test_revision_replay_is_a_separate_frozen_account_and_continue_preserves_source_history(ops,project):
    w=create(ops,rules=asdict(Rules(min_turnover=1e20)))
    first=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-05')))
    assert first['status']=='completed'
    original=ops.path(w['state']).read_bytes()
    old_signals=(ops.path(w['state']).parent/'2026-11-05/signals.csv').read_bytes()
    file=project/'data/market/history/prices.csv';p=pd.read_csv(file,dtype={'symbol':str})
    p.loc[p.index[0],'volume']+=1;p.to_csv(file,index=False)
    failed=wait_job(ops,ops.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06')))
    assert failed['status']=='failed' and failed['revision_id']
    data=ops.revision_view(w['id']);report=data['report']
    assert data['rows'] and report['row_changes']>=1
    assert ops.path(w['state']).read_bytes()==original
    assert len(ops.revision_view(w['id'],source='prices')['rows'])==len(data['rows'])
    replay=wait_job(ops,ops.submit(dict(workspace=w['id'],action='revision_replay',as_of='2026-11-06',report_id=report['id'])))
    assert replay['status']=='completed',replay
    new=ops.workspace(replay['result']['workspace_id'])
    assert new['mode']=='recalculation' and ops.state(new)['last_date']=='2026-11-06'
    assert ops.path(w['state']).read_bytes()==original
    same_day_analysis=wait_job(ops,ops.submit(dict(workspace=new['id'],action='analyze',as_of='2026-11-06')))
    assert same_day_analysis['status']=='completed',same_day_analysis
    later_analysis=wait_job(ops,ops.submit(dict(workspace=new['id'],action='analyze',as_of='2026-11-09')))
    assert later_analysis['status']=='failed'
    assert '2026-11-06까지만 저장' in later_analysis['error']
    with pytest.raises(ValueError,match='고정 자료'):ops.submit(dict(workspace=new['id'],action='daily',as_of='2026-11-06'))
    with pytest.raises(ValueError,match='확인'):ops.submit(dict(workspace=w['id'],action='revision_continue',as_of='2026-11-06',report_id=report['id']))
    continued=wait_job(ops,ops.submit(dict(workspace=w['id'],action='revision_continue',as_of='2026-11-06',report_id=report['id'],reviewed=True,price_reviewed=True)))
    assert continued['status']=='completed',continued
    assert ops.state(w)['last_date']=='2026-11-06'
    assert (ops.path(w['state']).parent/'2026-11-05/signals.csv').read_bytes()==old_signals
    assert ops.revision_view(w['id'])['report']['applied']


def test_reviewed_revision_continues_to_selected_date_without_recollecting(project,monkeypatch):
    from types import SimpleNamespace
    from trendfollow import revisions,workflow
    from trendfollow.operations import actions
    from trendfollow.strategy import Rules

    calls=[]
    context=SimpleNamespace(
        root=project,
        path=lambda value:project/value,
        configuration=lambda workspace:(Rules(),None),
        job_manager=SimpleNamespace(report_progress=lambda key,message,current=None,total=None:calls.append(('progress',message))),
    )
    workspace=dict(data_dir='data/market/history',universe='data/market/universe.csv',
                   config='examples/config.json',state='outputs/ui/accounts/test/state.json',capital=10000000)
    monkeypatch.setattr(revisions,'approve_revision',lambda *a,**kw:calls.append(('approve',a[2])))
    monkeypatch.setattr(workflow,'continue_revised_checkpoint',lambda *a,**kw:{'status':'processed','as_of':'2026-11-11'})
    def catchup(*args,**kwargs):
        calls.append(('catchup',args[3],kwargs['use_existing_prices'],kwargs['catch_up']))
        return {'status':'processed','as_of':'2026-11-13','processed_dates':['2026-11-12','2026-11-13']}
    monkeypatch.setattr(actions,'daily_run',catchup)
    result=actions.ActionRunner(context).run('job',workspace,
        {'action':'revision_continue','report_id':'r','price_reviewed':True,'catch_up_to':'2026-11-13'},
        '2026-11-11')
    assert ('catchup','2026-11-13',True,True) in calls
    assert result['revision_id']=='r' and result['as_of']=='2026-11-13'

@pytest.mark.parametrize('phase',['headers','body'])
@pytest.mark.parametrize('error',[ConnectionAbortedError,ConnectionResetError,BrokenPipeError,TimeoutError])
def test_client_disconnect_does_not_send_another_error_response(project,phase,error):
    server=make_server(project,0)
    handler=object.__new__(server.RequestHandlerClass)
    handler.server=server;handler.path='/api/bootstrap'
    handler.headers={'Host':f'127.0.0.1:{server.server_port}'}
    handler.authenticated=lambda:True
    responses=[];handler.send_response=responses.append;handler.send_header=lambda *args:None
    def closed(*args):raise error('client disconnected')
    handler.end_headers=closed if phase=='headers' else lambda:None
    class Writer:
        write=closed
    handler.wfile=Writer()
    try:
        handler.do_GET()
        assert responses==[200] and handler.close_connection is True
    finally:server.server_close();server.operations.close()


def test_second_ui_keeps_live_job_and_refreshes_completion(ops,project,monkeypatch):
    from trendfollow.ui_jobs import save_job
    w=create(ops);started=threading.Event();release=threading.Event();second=None
    def execute(key,*args):
        ops.jobs[key]['status']='running';save_job(ops.home/'jobs'/f'{key}.json',ops.jobs[key]);started.set()
        release.wait(5);ops.jobs[key]['status']='completed';save_job(ops.home/'jobs'/f'{key}.json',ops.jobs[key])
    monkeypatch.setattr(ops,'execute',execute)
    job=ops.submit(dict(workspace=w['id'],action='analyze',as_of='2026-11-06'))
    assert started.wait(2)
    try:
        second=Operations(project)
        assert second.jobs[job['id']]['status']=='running'
        persisted=json.loads((ops.home/'jobs'/f'{job["id"]}.json').read_text(encoding='utf-8'))
        assert persisted['status']=='running'
        with pytest.raises(ValueError,match='진행 중'):second.submit(dict(workspace=w['id'],action='paper',as_of='2026-11-06'))
        release.set();assert wait_job(ops,job)['status']=='completed'
        ops.pool.submit(lambda:None).result(timeout=3)
        assert next(j for j in second.pulse(w['id'])['jobs'] if j['id']==job['id'])['status']=='completed'
    finally:
        release.set()
        if second:second.close()


def test_legacy_running_job_is_not_asserted_interrupted_or_overwritten(project):
    file=project/'outputs/ui/jobs/legacy.json';file.parent.mkdir(parents=True)
    running=dict(id='legacy',action='daily',status='running')
    file.write_text(json.dumps(running))
    ops=Operations(project)
    try:
        assert ops.jobs['legacy']['status']=='unconfirmed'
        assert json.loads(file.read_text())==running
        file.write_text(json.dumps(dict(running,status='completed',result={'as_of':'2026-11-06'})))
        ops.refresh_jobs();assert ops.jobs['legacy']['status']=='completed'
    finally:ops.close()
