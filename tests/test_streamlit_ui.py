"""Exercise reruns, empty state, authorization and unknown-value filtering."""
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from trendfollow.streamlit_ui import (auth_key, candidate_stage, candidate_table_rows, filter_candidates,
                                     high52_distance, issue_auth_token, missing_candidate_sessions,
                                     observed_time_label, valid_auth_token)
from trendfollow.symbol_lookup import matching_symbols, named_rows, symbol_names

ROOT = Path(__file__).resolve().parents[1]


def unselected_board(**kwargs):
    # AppTest mocks the v2 JavaScript component registry.
    from types import SimpleNamespace
    return SimpleNamespace(symbol=None)


def test_partial_market_result_queues_once_without_clicking():
    from types import SimpleNamespace
    from threading import RLock
    from unittest.mock import Mock

    job = dict(id='repair', action='market_analyze', status='queued', workspace='a', as_of='2026-11-17')
    ops = SimpleNamespace(root=ROOT, lock=RLock(), jobs={}, refresh_jobs=Mock(), submit=Mock(return_value=job))
    app = AppTest.from_string("""
import streamlit as st
from trendfollow.streamlit_ui import complete_candidate_analysis
complete_candidate_analysis(st.session_state.ops, 'a', '2026-11-17', ('config', 'selection'))
""")
    app.session_state['ops'] = ops
    with patch('trendfollow.market_candidate_analysis.saved_analysis_current', return_value=False):
        app.run()
        ops.jobs[job['id']] = job
        app.run()
    assert not app.exception
    ops.submit.assert_called_once_with(dict(workspace='a', action='market_analyze', as_of='2026-11-17', selected_only=True))


def test_saved_master_resolves_name_and_preserves_duplicate_choices(tmp_path):
    master = tmp_path / 'master.csv'
    master.write_text('symbol,name\n005930,삼성전자\n005935,삼성전자우\n123456,동명이인\n654321,동명이인\n',
                      encoding='utf-8')
    names = symbol_names(master)
    assert matching_symbols(names, '삼성전자')[0] == '005930'
    assert matching_symbols(names, '동명이인') == ['123456', '654321']
    assert named_rows([{'symbol': '005930', 'price': 100}], names)[0]['name'] == '삼성전자'


def test_industry_observation_time_is_labelled_in_kst_without_inventing_missing_time():
    assert observed_time_label('2026-11-18T16:26:03+09:00') == '2026-11-18 16:26 KST'
    assert observed_time_label('2026-11-10') == '2026-11-10 · 시각 기록 없음'


def test_candidate_capture_needs_an_unsaved_session_in_selected_range():
    end = date(2026, 11, 18)
    saved = {'2026-11-16', '2026-11-17', '2026-11-18'}
    assert missing_candidate_sessions(date(2026, 11, 16), end, saved) == []
    assert missing_candidate_sessions(date(2026, 11, 13), end, saved) == ['2026-11-13']


def test_auth_cookie_is_signed_expires_and_changes_with_password():
    key = auth_key('test-password')
    token = issue_auth_token(key, now=1000)
    assert valid_auth_token(token, key, now=1001)
    assert not valid_auth_token(token, key, now=1001 + 12 * 60 * 60)
    assert not valid_auth_token(token, auth_key('changed-password'), now=1001)
    assert not valid_auth_token(token + 'bad', key, now=1001)


def test_running_operations_source_version_matches_files_and_detects_edits(tmp_path):
    from trendfollow.runtime_guard import source_fingerprint
    from trendfollow.streamlit_ui import CODE_ROOT
    from trendfollow.operations import Operations

    assert Operations.source_fingerprint == source_fingerprint(CODE_ROOT)
    package = tmp_path / 'trendfollow'
    package.mkdir()
    source = package / 'sample.py'
    source.write_text('VALUE = 1\n', encoding='utf-8')
    before = source_fingerprint(tmp_path)
    source.write_text('VALUE = 2\n', encoding='utf-8')
    assert source_fingerprint(tmp_path) != before


def test_streamlit_stops_before_using_stale_operations():
    class StaleOperations:
        jobs = {}
        def refresh_jobs(self):
            pass

    with patch('trendfollow.streamlit_ui.setting', return_value=''), \
         patch('trendfollow.streamlit_ui.operations', return_value=StaleOperations()):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
    assert any('코드가 변경' in error.value for error in app.error)
    assert not app.radio


def test_stale_server_shows_running_job_instead_of_claiming_it_stopped():
    class StaleOperations:
        jobs = {'job': {'id': 'job', 'workspace': 'account', 'status': 'running'}}
        def refresh_jobs(self):
            pass

    with patch('trendfollow.streamlit_ui.setting', return_value=''), \
         patch('trendfollow.streamlit_ui.operations', return_value=StaleOperations()), \
         patch('trendfollow.streamlit_ui.job_status') as status:
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
    assert any('기존 코드로 계속 실행' in warning.value for warning in app.warning)
    status.assert_called_once()


def test_old_observation_failure_stays_in_job_history(tmp_path):
    from threading import RLock

    class Jobs:
        home = tmp_path
        lock = RLock()
        jobs = {'old': dict(id='old', workspace='account', action='intraday_close',
                            as_of='2026-11-18', status='failed',
                            error='대조 기준일을 관측 거래일과 맞추세요.')}

        def refresh_jobs(self):
            pass

    ops = Jobs()
    def page():
        import streamlit as st
        from trendfollow.streamlit_ui import job_status
        job_status(st.session_state['fake-ops'], 'account', show_next=False)

    app = AppTest.from_function(page)
    app.session_state['fake-ops'] = ops
    app.run(timeout=30)
    assert not app.error
    assert any('작업 기록 보기' in item.label for item in app.expander)
    app.session_state['job-submitted-account'] = 'old'
    app.run(timeout=30)
    assert any('대조 기준일' in item.value for item in app.error)


def test_candidate_table_uses_display_names_and_hides_internal_fields():
    rows = candidate_table_rows([dict(symbol='009270', name='종목', market='KOSPI',
                                      sector='UNCLASSIFIED:009270', benchmark='069500',
                                      recent_turnover_proxy=1_165_242_606,
                                      reason_display='거래대금 기준 미만')],
                                'KRX Open API', include_reason=True)
    assert rows[0]['산업'] == '산업 분류 없음'
    assert rows[0]['당일 거래대금'] == '12억원가량'
    assert rows[0]['제외 사유'] == '거래대금 기준 미만'
    assert 'sector' not in rows[0] and 'benchmark' not in rows[0]


def test_auth_cookie_survives_module_secret_reload(tmp_path, monkeypatch):
    import trendfollow.streamlit_ui as ui
    monkeypatch.setattr(ui, 'ROOT', tmp_path)
    ui.auth_server_secret.cache_clear()
    key = ui.auth_key('test-password')
    token = ui.issue_auth_token(key, now=1000)
    ui.auth_server_secret.cache_clear()
    assert ui.valid_auth_token(token, ui.auth_key('test-password'), now=1001)
    assert not ui.valid_auth_token(token, ui.auth_key('changed-password'), now=1001)


def test_new_streamlit_session_recovers_login_from_cookie(monkeypatch):
    monkeypatch.setenv('UI_PASSWORD', 'test-password')
    token = issue_auth_token(auth_key('test-password'))
    with patch('trendfollow.streamlit_ui.read_auth_cookie', return_value=token), patch('trendfollow.streamlit_ui.job_status'):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        assert not app.exception and not app.error
        assert app.radio(key='page-picker')
        assert not any(field.label == '운용 비밀번호' for field in app.text_input)


def test_local_env_password_and_environment_override(tmp_path, monkeypatch):
    import trendfollow.streamlit_ui as ui
    (tmp_path / '.env').write_text('UI_PASSWORD=local-password\n', encoding='utf-8')
    monkeypatch.setattr(ui, 'ROOT', tmp_path)
    monkeypatch.delenv('UI_PASSWORD', raising=False)
    assert ui.setting('UI_PASSWORD') == 'local-password'
    monkeypatch.setenv('UI_PASSWORD', 'process-password')
    assert ui.setting('UI_PASSWORD') == 'process-password'


def test_filters_keep_unknown_separate_from_false():
    rows = [dict(symbol='000001',signal=True,rs_pass=True,volume_ratio=2),
            dict(symbol='000002',signal=False,rs_pass=False,volume_ratio=.5),
            dict(symbol='000003',signal=None,rs_pass=None,volume_ratio=None)]
    assert [r['symbol'] for r in filter_candidates(rows, signal='미통과')] == ['000002']
    assert [r['symbol'] for r in filter_candidates(rows, signal='미확인·미적용')] == ['000003']
    assert [r['symbol'] for r in filter_candidates(rows, rs='통과',volume='통과')] == ['000001']
    assert filter_candidates(rows,search='없는 종목') == []


def test_52week_and_setup_filters_use_saved_analysis_values():
    rows = [dict(symbol='000001',high52_breakout=True,setup_ready=True,setup_trigger=False),
            dict(symbol='000002',high52_breakout=False,setup_ready=False,setup_trigger=False),
            dict(symbol='000003',high52_breakout=None,setup_ready=None,setup_trigger=None)]
    assert [r['symbol'] for r in filter_candidates(rows,high52='돌파')] == ['000001']
    assert [r['symbol'] for r in filter_candidates(rows,high52='자료 부족')] == ['000003']
    assert [r['symbol'] for r in filter_candidates(rows,setup_condition='준비 미충족')] == ['000002']


def test_market_stage_and_52week_distance_do_not_depend_on_buy_route():
    breakout = dict(close=4950,high=4950,prior_52w_high=4270,high52_breakout=True)
    faded = dict(close=106100,high=109900,prior_52w_high=108000,high52_breakout=False)
    assert candidate_stage(breakout) == '종가 돌파'
    assert high52_distance(breakout) == '15.9% 돌파'
    assert candidate_stage(faded) == '고가 접촉·종가 하회'
    assert high52_distance(faded) == '1.8% 남음'


def navigate(app, page):
    if page == '날짜별 시장 후보':
        app.radio(key='page-picker').set_value('시장 후보').run(timeout=30)
        return
    section = '운용 기록' if page in ('종목 탐색','날짜별 시장 후보','운용 기록') else '오늘의 운용'
    back = next((button for button in app.button if button.label.endswith('으로 돌아가기')), None)
    if back:
        back.click().run(timeout=30)
    app.radio(key='page-picker').set_value(section).run(timeout=30)
    if page != section:
        app.button(key=f'nav-{page}').click().run(timeout=30)


def test_readonly_navigation_never_submits_jobs(monkeypatch):
    monkeypatch.delenv('UI_PASSWORD',raising=False)
    with patch('trendfollow.streamlit_ui.candidate_board_component', return_value=unselected_board), \
         patch('trendfollow.streamlit_ui.setting', return_value=''), patch('trendfollow.streamlit_ui.job_status'), patch('trendfollow.ui_service.Operations.submit') as submit:
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        assert not app.exception
        for page in ['오늘의 운용','날짜별 시장 후보','운용 기록','장중 관측','정정·검증','전략·설정','종목 탐색']:
            navigate(app, page)
            assert not app.exception
            assert not app.error
        submit.assert_not_called()


def test_account_change_returns_to_primary_workflow(monkeypatch):
    monkeypatch.delenv('UI_PASSWORD', raising=False)
    with patch('trendfollow.streamlit_ui.setting', return_value=''), \
         patch('trendfollow.streamlit_ui.job_status'):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        navigate(app, '종목 탐색')
        current = app.selectbox(key='account-picker').value
        other = 'setup-rs' if current != 'setup-rs' else 'baseline'
        app.selectbox(key='account-picker').set_value(other).run(timeout=30)
        assert not app.exception
        assert app.radio(key='page-picker').value == '오늘의 운용'
        assert any('오늘의 운용 · 다음 단계' in item.value for item in app.subheader)


def test_account_selection_restored_in_new_session():
    import streamlit as st
    from trendfollow.streamlit_ui import ACCOUNT_COOKIE

    cookies = {}
    def save_cookie(name, value, max_age):
        cookies[name] = value

    def open_app():
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        assert not app.exception
        return app

    with patch.object(type(st.context), 'cookies', property(lambda _: cookies)), \
         patch('trendfollow.streamlit_ui.write_cookie', side_effect=save_cookie), \
         patch('trendfollow.streamlit_ui.setting', return_value=''):
        app = open_app()
        initial = app.selectbox(key='account-picker').value
        other = 'setup-rs' if initial != 'setup-rs' else 'baseline'
        app.selectbox(key='account-picker').set_value(other).run(timeout=30)
        assert cookies[ACCOUNT_COOKIE] == other
        assert open_app().selectbox(key='account-picker').value == other
        cookies[ACCOUNT_COOKIE] = 'no-longer-listed'
        assert open_app().selectbox(key='account-picker').value == initial


def test_observation_selects_account_stock_by_name(monkeypatch):
    monkeypatch.delenv('UI_PASSWORD', raising=False)
    with patch('trendfollow.streamlit_ui.setting', return_value=''), \
         patch('trendfollow.streamlit_ui.job_status'), \
         patch('trendfollow.operations.accounts.WorkspaceStore.favorites', return_value=['005930']):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        navigate(app, '장중 관측')
        chooser = next(widget for widget in app.multiselect if widget.label == '감시 종목명·코드 검색')
        assert any('000080' in label and '·' in label for label in chooser.options)
        chooser.set_value(['000080']).run(timeout=30)
        assert not app.exception and not app.error
        key = app.selectbox(key='account-picker').value
        assert app.session_state[f'watch-{key}'] == ['000080']
        next(b for b in app.button if b.label == '관심 종목 불러오기').click().run(timeout=30)
        assert app.session_state[f'watch-{key}'] == ['000080', '005930']


def test_favorites_persist_per_account_and_removal_is_idempotent(tmp_path):
    from trendfollow.operations import Operations
    ops = Operations(tmp_path)
    try:
        ops.accounts.set_favorite('baseline', '005930', True)
        ops.accounts.set_favorite('baseline', '005930', True)
        assert ops.accounts.favorites('setup-rs') == []
    finally:
        ops.close()
    ops = Operations(tmp_path)
    try:
        assert ops.accounts.favorites('baseline') == ['005930']
        ops.accounts.set_favorite('baseline', '005930', False)
        assert ops.accounts.favorites('baseline') == []
    finally:
        ops.close()


def test_observation_shows_selected_stock_run_instead_of_previous_stock(monkeypatch):
    from trendfollow.ui_service import Operations
    from trendfollow.operations.intraday import IntradayService

    def intraday(self, workspace=None):
        def run(run_id, symbol, error=None, failure_reason=None):
            return dict(id=run_id, session='2026-11-19', targets=[symbol], events=[], watch_mode='industry' if error else 'symbols',
                        summary=dict(connection_state='failed', subscriptions_requested=91 if error else 1,
                                     subscription_acks=41 if error else 1, quote_ticks=23,
                                     kis_error_code=error, failure_reason=failure_reason,
                                     rejected_symbol='098460' if error else None,
                                     latest_target_quotes={symbol: dict(price=100)}))
        return dict(today='2026-11-19', default_session='2026-11-19', regular_session=True,
                    control=dict(status='off_confirmed', enabled=False, active_or_unconfirmed=[]),
                    plans=[], runs=[run('old', '005930'), run('new', '009150', 'OPSP0008'),
                                    run('connection', '000080', failure_reason='connection_error')])

    monkeypatch.delenv('UI_PASSWORD', raising=False)
    with patch('trendfollow.streamlit_ui.setting', return_value=''), \
         patch('trendfollow.streamlit_ui.job_status'), \
         patch.object(Operations, 'intraday', intraday), \
         patch.object(IntradayService, 'plan_basis', return_value=dict(as_of='2026-11-18', industry_ready=True)), \
         patch.object(IntradayService, 'subscription_preview', return_value=dict(count=1, industries=[])):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        navigate(app, '장중 관측')
        next(widget for widget in app.multiselect if widget.label == '감시 종목명·코드 검색').set_value(['009150']).run(timeout=30)
        assert not app.exception
        assert not any('OPSP0008' in item.value for item in app.error)
        next(widget for widget in app.selectbox if widget.label=='감시 범위').set_value('industry').run()
        assert any('OPSP0008' in item.value for item in app.error)
        assert any('삼성전기' in item.value and '오늘 최근 관측' in item.value for item in app.markdown)
        next(widget for widget in app.selectbox if widget.label=='감시 범위').set_value('symbols').run()
        assert not any(field.label == '관측 거래일' for field in app.date_input)
        next(widget for widget in app.multiselect if widget.label == '감시 종목명·코드 검색').set_value(['000080']).run(timeout=30)
        assert any('KIS WebSocket 연결 오류' in item.value for item in app.error)


def test_revision_name_search_passes_exact_stock_code(monkeypatch):
    from trendfollow.ui_service import Operations

    seen = []
    report = dict(id='report', account_as_of='2026-11-13', row_changes=1,
                  applied=True, stale=False, explanation='')

    def revision_view(self, key, report_id=None, **filters):
        if report_id:
            seen.append(filters.get('symbols'))
        return dict(policy='manual', report=report,
                    rows=[dict(symbol='005930', field='close')] if not report_id else [], total=1)

    monkeypatch.delenv('UI_PASSWORD', raising=False)
    with patch('trendfollow.streamlit_ui.setting', return_value=''), \
         patch('trendfollow.streamlit_ui.job_status'), \
         patch.object(Operations, 'revision_view', revision_view):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        navigate(app, '정정·검증')
        next(field for field in app.text_input if field.label == '변경 종목명·코드 검색').set_value('삼성전자').run(timeout=30)
        assert not app.exception and not app.error
        assert seen[-1] == ['005930']


def test_workflow_starts_with_next_action_and_prepares_missing_market_dates():
    from trendfollow.ui_service import Operations
    original = Operations.guidance
    def guidance(self, key, as_of=None):
        result = original(self, key, as_of)
        if as_of:
            result.update(blocked=None, processed=False, pending_sessions=[as_of],
                          candidate_missing_sessions=[as_of])
        return result
    with patch('trendfollow.streamlit_ui.setting', return_value=''), \
         patch('trendfollow.streamlit_ui.job_status'), \
         patch('trendfollow.candidate_universe.historical_candidate_source', return_value='krx_open_api'), \
         patch.object(Operations, 'guidance', guidance):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        assert app.radio(key='page-picker').value == '오늘의 운용'
        assert any('자동 준비할 시장 종목 목록' in item.value for item in app.caption)
        assert any(button.label.endswith('까지 필요한 자료 준비·종가 처리') for button in app.button)


def test_password_gate_and_account_save(tmp_path, monkeypatch):
    import shutil
    import trendfollow.streamlit_ui as ui
    shutil.copytree(ROOT/'examples',tmp_path/'examples')
    monkeypatch.setattr(ui,'ROOT',tmp_path)
    monkeypatch.setenv('UI_PASSWORD','test-only-password')
    app = AppTest.from_file(str(ROOT/'streamlit_app.py'), default_timeout=30).run(timeout=30)
    assert not app.radio
    app.text_input[0].set_value('wrong')
    app.button[0].click().run()
    assert app.error
    app.text_input[0].set_value('test-only-password')
    app.button[0].click().run()
    assert not app.exception
    assert not any(field.label == '운용 비밀번호' for field in app.text_input)
    assert not any(button.label == '로그인' for button in app.button)
    navigate(app, '전략·설정')
    for preset_name in ('setup', 'setup-rs-exit'):
        selector = next(widget for widget in app.selectbox if widget.label == '전략 예제')
        selector.select_index(selector.options.index(preset_name)).run(timeout=30)
    assert not app.exception
    assert any('RS 순위 50 미만 2회 연속이면 청산' in item.value for item in app.markdown)
    assert any(frame.value.to_dict('records') == [
        {'항목':'RS 약화 청산', '이전 값':'해제', '선택한 값':'사용'}
    ] for frame in app.dataframe)
    next(b for b in app.button if b.label=='별도 계좌 저장').click().run()
    assert not app.exception
    assert not app.error
    saved=list((tmp_path/'outputs/ui/accounts').glob('*/config.json'))
    assert len(saved)==1
    app.run()
    assert len(list((tmp_path/'outputs/ui/accounts').glob('*/config.json')))==1
    assert not list((tmp_path/'outputs/ui/accounts').glob('*/state.json'))


def test_candidate_master_names_cache_and_recorded_fallback(tmp_path):
    from trendfollow.streamlit_ui import board_rows, _master_names

    master = tmp_path/'data/market/master'
    master.mkdir(parents=True)
    path = master/'kospi.csv'
    records = [dict(symbol='000080', name='기록 이름'), dict(symbol='000001', name='기존 이름')]
    assert board_rows(tmp_path, records)[0]['name'] == '기록 이름'
    path.write_text('symbol,name\n000080,KIS 이름\n', encoding='utf-8')
    first = board_rows(tmp_path, records)
    hits = _master_names.cache_info().hits
    assert [r['name'] for r in first] == ['KIS 이름', '기존 이름']
    assert board_rows(tmp_path, records) == first and _master_names.cache_info().hits == hits + 1
    path.write_text('symbol,name\n000080,새로운 KIS 이름\n', encoding='utf-8')
    assert board_rows(tmp_path, records)[0]['name'] == '새로운 KIS 이름'
    assert records[0]['name'] == '기록 이름'


def test_candidate_detail_and_filter_are_connected(monkeypatch):
    import streamlit as st
    from trendfollow.ui_service import Operations
    original = Operations.snapshot
    def snapshot(self,key, *, include_multi_rs=True):
        result = original(self,key, include_multi_rs=include_multi_rs)
        assert include_multi_rs == (st.session_state.get('detail-page') == '종목 탐색')
        result['rows'] = [dict(symbol='000080',name='테스트 상승',sector='산업A',
            reference_price=10000,rs_percentile=95,signal=True,rs_pass=True,
            setup_phase='breakout',volume_ratio=2),
            dict(symbol='000210',name='테스트 대기',sector='산업B',
            reference_price=9000,rs_percentile=70,signal=False,rs_pass=False,setup_ready=True,
            setup_phase='base',volume_ratio=None)]
        return result
    monkeypatch.delenv('UI_PASSWORD',raising=False)
    from unittest.mock import Mock
    table = Mock()
    with patch('trendfollow.streamlit_ui._master_names', return_value={'000080': 'KIS 상승', '000210': 'KIS 둘째'}), \
         patch('trendfollow.streamlit_ui.candidate_board_component', return_value=table), \
         patch('trendfollow.streamlit_ui.setting', return_value=''), patch('trendfollow.streamlit_ui.job_status'), patch.object(Operations,'snapshot',snapshot), patch.object(Operations,'prices',return_value=[
            dict(date='2026-11-13',close=9900),dict(date='2026-11-16',close=10000)]):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        assert not app.exception and not app.error
        navigate(app, '종목 탐색')
        assert any(s.value=='KIS 상승' for s in app.subheader)
        shown = table.call_args.kwargs['data']
        assert shown['explorer'] and shown['selected'] == '000080'
        assert [r['rs_pass'] for r in shown['rows']] == [True, False]
        assert shown['rows'][1]['setup_phase'] == 'base'
        next(t for t in app.text_input if t.label=='종목명·코드·산업 검색').set_value('대기').run()
        assert not app.exception and not app.error
        assert any(s.value=='KIS 둘째' for s in app.subheader)
        next(b for b in app.button if b.label=='장중 관측에서 열기').click().run(timeout=30)
        key=app.selectbox(key='account-picker').value
        assert app.session_state[f'watch-{key}']==['000210']
        assert app.multiselect(key=f'watch-{key}').value == ['000210']


def test_daily_action_accepts_missing_industry_and_selected_price_source(monkeypatch):
    from trendfollow.ui_service import Operations
    original = Operations.guidance
    def guidance(self,key,as_of=None):
        result = original(self,key,as_of)
        if as_of:
            result.update(blocked=None,processed=False,requires_industry=True,industry_ready=False,
                          pending_sessions=[as_of],candidate_missing_sessions=[])
        return result
    monkeypatch.setenv('UI_PASSWORD','test-only-password')
    with patch.object(Operations,'guidance',guidance), patch.object(Operations,'submit',return_value={'id':'test-job'}) as submit:
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        app.text_input[0].set_value('test-only-password')
        app.button[0].click().run(timeout=30)
        app.radio(key='page-picker').set_value('오늘의 운용').run(timeout=30)
        app.selectbox(key='price-source').set_value('pykrx').run(timeout=30)
        next(button for button in app.button if button.label.endswith('까지 필요한 자료 준비·종가 처리')).click().run(timeout=30)
        assert submit.call_args.args[0]['action'] == 'daily'
        assert submit.call_args.args[0]['price_source'] == 'pykrx'


def test_daily_missing_bars_shows_rows_and_retries_failed_request(monkeypatch):
    from unittest.mock import PropertyMock
    from trendfollow.ui_service import Operations
    original = Operations.guidance
    jobs = {}
    def guidance(self, key, as_of=None):
        result = original(self, key, as_of)
        if as_of:
            result.update(blocked=None, processed=False, pending_sessions=[as_of], candidate_missing_sessions=[])
            jobs['failed'] = dict(id='failed', workspace=key, action='daily', as_of=as_of, status='failed',
                                  price_source='pykrx', candidate_source='krx_open_api',
                                  missing_bars=[dict(symbol='089590', date='2026-11-19')])
        return result
    monkeypatch.setenv('UI_PASSWORD', 'test-only-password')
    with patch.object(Operations, 'guidance', guidance), \
         patch.object(Operations, 'jobs', new_callable=PropertyMock, return_value=jobs), \
         patch.object(Operations, 'refresh_jobs'), patch('trendfollow.streamlit_ui.job_status'), \
         patch('trendfollow.streamlit_ui.stored_symbol_names', return_value={'089590':'제주항공'}), \
         patch.object(Operations, 'submit', return_value={'id':'retry'}) as submit_job:
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        app.text_input[0].set_value('test-only-password')
        app.button[0].click().run(timeout=30)
        rows = next(df.value for df in app.dataframe if '누락 거래일' in df.value)
        assert rows.to_dict('records') == [{'종목':'제주항공 · 089590', '누락 거래일':'2026-11-19'}]
        submit_job.assert_not_called()
        next(button for button in app.button if button.label == '종가 처리 다시 시도').click().run(timeout=30)
        assert submit_job.call_args.args[0] == dict(action='daily', workspace=jobs['failed']['workspace'],
                                                    as_of=jobs['failed']['as_of'], price_source='pykrx',
                                                    candidate_source='krx_open_api')


def test_market_candidate_page_handles_stale_cached_date_query():
    from trendfollow.ui_service import Operations
    original = Operations.candidate_dates
    def old_dates(self, key):
        dates = original(self, key)
        for field in ('suggested_capture_start', 'capture_window_start',
                      'missing_selection_count', 'missing_selection_dates'):
            dates.pop(field, None)
        return dates
    with patch('trendfollow.streamlit_ui.candidate_board_component', return_value=unselected_board), \
         patch('trendfollow.streamlit_ui.setting', return_value=''), patch('trendfollow.streamlit_ui.job_status'), patch.object(Operations, 'candidate_dates', old_dates):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        navigate(app, '날짜별 시장 후보')
        next(widget for widget in app.selectbox if widget.label == '후보군 자료 원천').set_value('krx_open_api').run(timeout=30)
        assert not app.exception and not app.error
        assert any('서버를 다시 시작' in notice.value for notice in app.warning)

def test_close_plan_shows_account_risk_at_close(monkeypatch):
    from dataclasses import asdict
    from unittest.mock import Mock
    import trendfollow.streamlit_ui as ui
    from trendfollow.risk import Limits

    ops = Mock()
    ops.state.return_value = dict(last_date='2026-11-18', cash=1_000_000, positions={}, units=1)
    snapshot = dict(account_as_of='2026-11-18', status='ready', limits=asdict(Limits()),
                    pending=[dict(symbol='005930', sector='반도체', reference_price=1000,
                                  stop_price=900, signal=True, avg_turnover=1e9, rs_percentile=99)])
    view = Mock()
    monkeypatch.setattr(ui, 'st', view)
    monkeypatch.setattr(ui, 'stored_symbol_names', lambda *_: {'005930': '삼성전자'})
    ui.close_plan_summary(ops, 'account', snapshot)
    row = view.dataframe.call_args.args[0].iloc[0]
    assert '삼성전자' in row['종목']
    assert 0 < row['예상 수량(주)'] <= 20
    assert 0 < row['계획 위험(원)'] <= 2000


def test_candidate_plan_uses_selected_day_and_all_competing_candidates(tmp_path, monkeypatch):
    import json
    from unittest.mock import Mock
    import pandas as pd
    import trendfollow.streamlit_ui as ui

    day = '2026-11-17'
    checkpoint = tmp_path / day / 'state.json'
    checkpoint.parent.mkdir()
    pending = [{'symbol':'000001'}, {'symbol':'000002'}]
    checkpoint.write_text(json.dumps({'cash':1234, 'pending':pending}), encoding='utf-8')
    ops, view = Mock(), Mock()
    ops.path.return_value = tmp_path / 'state.json'
    allocate = Mock(return_value=pd.DataFrame([dict(symbol='000002', quantity=2, stop_price=1000, planned_risk=400)]))
    monkeypatch.setattr(ui, 'st', view)
    monkeypatch.setattr(ui, 'pending_allocations', allocate)
    snapshot = dict(workspace={'state':'state.json'}, limits={})
    ui.candidate_plan(ops, snapshot, day, '000002')
    assert allocate.call_args.args[0]['cash'] == 1234
    assert allocate.call_args.args[2] == pending
    assert '400원' in view.write.call_args.args[0]
    ui.candidate_plan(ops, snapshot, '2026-11-18', '000002')
    assert '처리 기록이 없어' in view.caption.call_args.args[0]
    assert allocate.call_count == 1


def test_history_uses_selected_checkpoint_and_next_day_orders(tmp_path):
    import json

    for day, symbol in [('2026-11-16','005930'), ('2026-11-17','000080')]:
        folder = tmp_path / day
        folder.mkdir()
        (folder / 'state.json').write_text(json.dumps(dict(cash=1000, positions={},
            pending=[dict(symbol=symbol, reference_price=200)])), encoding='utf8')
    (folder / 'orders.csv').write_text('date,symbol,status\n2026-11-17,005930,filled\n2026-11-17,000080,rejected\n',
                                       encoding='utf8')

    with patch('trendfollow.streamlit_ui.stored_symbol_names', return_value={}):
        app = AppTest.from_string(
            'from trendfollow.streamlit_ui import account_history\n'
            'from types import SimpleNamespace\nfrom pathlib import Path\n'
            f'ops=SimpleNamespace(path=lambda _:Path({str(tmp_path / "state.json")!r}))\n'
            'account_history(ops,"demo",dict(workspace={"state":"state.json"},rules={},account_as_of="2026-11-17"))'
        ).run()
        prefix = 'history-record-demo-2026-11-17-'
        (folder / 'pyramiding-review.csv').write_text('symbol,date\n', encoding='utf8')
        (folder / 'events.csv').write_text('kind,symbol\nrs_exit_scheduled,005930\nsuccess_3r,000080\n', encoding='utf8')
        app.run()
        assert '005930 · RS 약화 청산 예정' in app.button(key=prefix+'운용 이벤트').label
        assert app.button(key=prefix+'보유 종목').disabled
        assert '0종목' in app.button(key=prefix+'보유 종목').label
        assert '전략에서 사용 안 함' in app.button(key=prefix+'위험 검토').label
        assert '0건' in app.button(key=prefix+'추가매수 검토').label
        assert '2건 중 1건 체결' in app.button(key=prefix+'모의 주문·체결').label
        app.button(key=prefix+'모의 주문·체결').click().run()
        assert list(app.dataframe[-1].value['status']) == ['filled', 'rejected']
        app.selectbox(key='history-day-demo').set_value('2026-11-16').run()
        assert not app.exception
        assert list(app.dataframe[0].value['종목코드']) == ['005930']
        assert list(app.dataframe[1].value['종목코드']) == ['005930']
        assert list(app.dataframe[1].value['status']) == ['filled']



def test_wave_filter_switches_and_clears_linked_conditions(monkeypatch):
    from trendfollow import streamlit_ui as ui

    state = {'phase':'base', 'condition':'준비 충족', 'high52':'돌파'}
    monkeypatch.setattr(ui.st, 'session_state', state)
    for choice, expected in [('trigger', ('전체','셋업 돌파')), ('trigger', ('전체','전체')),
                             ('base', ('base','전체')), ('rising', ('rising','전체')),
                             ('all', ('전체','전체'))]:
        state['widget'] = {'choice':choice}
        ui.choose_wave('widget', 'phase', 'condition')
        assert (state['phase'], state['condition']) == expected
        assert state['high52'] == '돌파'

def test_favorite_outside_current_candidates_opens_last_analysis(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import trendfollow.streamlit_ui as ui

    today, earlier = '2026-11-19', '2026-11-17'
    ops, view, render = Mock(), Mock(), Mock()
    monkeypatch.setattr(ui, 'st', view)
    monkeypatch.setattr(ui, 'candidate_detail', render)
    monkeypatch.setattr(ui, 'favorite_watch', Mock())
    monkeypatch.setattr(ui, 'stored_symbol_names', lambda *_: {'000020': '동화약품'})
    ops.accounts.favorites.return_value = ['000020']
    ops.candidate_selection.return_value = {'members': [{'symbol': '005930'}]}
    view.radio.return_value = '000020'
    for day, symbol in [(today, '005930'), (earlier, '000020')]:
        (tmp_path / day).write_text(f'symbol\n{symbol}\n', encoding='utf-8')
    ops.latest_signals.side_effect = lambda workspace, day: tmp_path / day
    row = {'symbol': '000020'}
    ops.snapshot.return_value = {'rows': [row]}
    dates = {'capture_as_of': today, 'selection_dates': [today], 'signal_dates': [today, earlier]}
    ui.account_favorite_board(ops, 'account', {'workspace': {}}, dates, True)
    assert '후보에 없음' in view.radio.call_args.kwargs['format_func']('000020')
    assert render.call_args.args[3:5] == (row, earlier)
    assert render.call_args.kwargs['chart_day'] == today
    ops.snapshot.assert_called_once_with('account', earlier, include_multi_rs=False)

def test_favorite_watch_starts_only_selected_symbols_and_displays_matching_run(monkeypatch):
    from unittest.mock import Mock
    import trendfollow.streamlit_ui as ui

    ops, view, submit, table = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(ui, 'st', view)
    monkeypatch.setattr(ui, 'submit', submit)
    monkeypatch.setattr(ui, 'observation_table', table)
    names = {'000020': '동화약품', '005930': '삼성전자'}
    monkeypatch.setattr(ui, 'account_symbol_names', lambda *_: names)
    view.multiselect.return_value = ['005930']
    view.button.return_value = True
    run = dict(session='2026-11-20', targets=['005930'], watch_mode='symbols', events=[],
               summary={'latest_signal_states': {'005930': {'breakout_present': True}}})
    ops.intraday.return_value = dict(today=run['session'], today_as_of='2026-11-19',
                                     regular_session=True, control={'active_or_unconfirmed': []},
                                     runs=[dict(run, watch_mode='industry', summary={}), run])
    ui.favorite_watch.__wrapped__(ops, 'account', list(names), names, True,
                                  {'workspace': {}, 'rules': {'entry_method': 'setup'}})
    submit.assert_called_once_with(ops, 'account', 'watch_auto', '2026-11-19',
                                   symbols=['005930'], session=run['session'], watch_mode='symbols')
    assert table.call_args.args[0][0]['breakout_present'] is True

def test_history_cards_separate_review_candidates_and_exceeded_limits(tmp_path):
    from trendfollow.streamlit_ui import history_cards

    (tmp_path / 'pyramiding-review.csv').write_text(
        'eligible_for_risk_review\nTrue\nFalse\n', encoding='utf8')
    (tmp_path / 'campaign-risk.csv').write_text(
        'operating_risk_exceeded,hard_risk_exceeded\nTrue,True\nFalse,False\n', encoding='utf8')
    cards = history_cards(tmp_path, {}, {}, {})
    assert cards[4][1:3] == ('한도 초과 1건', '위험 검토 2건')
    assert cards[4][-1] == 'warning'
    assert cards[5][1:3] == ('위험 검토 후보 1건', '추가매수 검토 2건')
