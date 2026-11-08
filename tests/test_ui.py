import copy
from dataclasses import asdict
import http.client
import json
from pathlib import Path
import shutil
import threading
import time

import pandas as pd
import pytest

from trendfollow.demo import generate
from trendfollow.risk import Limits
from trendfollow.strategy import Rules
from trendfollow.ui_service import Operations
from trendfollow.ui_server import make_server


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


def test_create_new_configuration_never_overwrites_templates_or_initializes_account(ops,project):
    template=(project/'examples/config.json').read_bytes()
    w=create(ops)
    assert (project/'examples/config.json').read_bytes()==template
    assert not ops.path(w['state']).exists()
    snap=ops.snapshot(w['id'])
    assert snap['status']=='not_started' and snap['rows']==[]
    assert snap['cash']==10000000 and snap['files_ready']


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


def test_historical_analysis_chart_does_not_show_future_prices(ops):
    w=create(ops)
    job=wait_job(ops,ops.submit(dict(workspace=w['id'],action='analyze',as_of='2026-11-05')))
    assert job['status']=='completed'
    symbol=ops.snapshot(w['id'])['rows'][0]['symbol']
    rows=ops.prices(w['id'],symbol)
    assert rows and all(row['date']<='2026-11-05' for row in rows)


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
    file.write_text(json.dumps(dict(id='job',status='running',action='daily')))
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
