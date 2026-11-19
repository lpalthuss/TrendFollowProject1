"""Paper Blue workbench. Rendering delegates all mutations to Operations."""
from datetime import date, datetime, timedelta
from functools import lru_cache
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

import pandas as pd
import streamlit as st
from dotenv import dotenv_values

from .ui_service import KST, Operations
from .runtime_guard import source_fingerprint
from .price_chart import chart_html
from .symbol_lookup import matching_symbols, named_rows, symbol_names

ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = ROOT
AUTH_COOKIE = 'trendfollow_ui_auth'
AUTH_AGE_SECONDS = 12 * 60 * 60
LABELS = {'symbol':'종목코드','name':'종목명','industry_name':'산업','sector':'산업 코드',
          'reference_price':'종가','rs_percentile':'RS 순위','mansfield_rs':'Mansfield RS',
          'multi_rs':'다기간 Mansfield','prior_52w_high':'직전 52주 고가','high52_breakout':'52주 신고가 돌파',
          'rs_pass':'RS 통과','sector_action':'산업 액션','setup_phase':'셋업',
          'setup_ready':'셋업 준비','setup_trigger':'셋업 돌파','signal_entry_path':'신호 경로',
          'volume_ratio':'거래량 배수','signal':'최종 신호','quantity':'수량',
          'stop_price':'손절가','planned_risk':'계획 위험','cash_required':'필요 현금',
          'allocation_reason':'배분 사유','last_price':'최근 가격','entry_price':'진입가',
          'target_3r':'3R','addition_count':'추가매수 횟수'}
ACTIONS = {'industry':'오늘 산업 자료 갱신','daily':'일봉 수집 + 미처리 거래일 모의 처리',
           'analyze':'후보만 재계산','paper':'저장 가격으로 모의 처리',
           'prepare_intraday':'관측 계획 생성','watch_auto':'장중 관측 준비·시작',
           'intraday_close':'장중 기록과 종가 대조',
           'volume_import':'관측 거래량 누적','volume_profile':'동시간 거래량 기준 생성',
           'candidate_universe':'날짜별 시장 종목 선정','market_analyze':'선택한 거래일까지 전략 조건 계산',
           'candidate_policy':'날짜별 후보군 계좌 적용',
           'revision_check':'과거 자료 변경 대조','revision_continue':'정정 반영·계속 운용',
           'revision_replay':'정정 비교 계좌 생성'}



@st.cache_resource
def operations(root: str):
    # Shared across reruns and sessions in this process, including its job lock.
    return Operations(root)


def setting(name, default=''):
    local = dotenv_values(ROOT / '.env', interpolate=False).get(name) or default
    fallback = os.environ.get(name) or local
    try:
        return st.secrets.get(name, fallback)
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return fallback


@lru_cache(maxsize=4)
def auth_server_secret(root):
    path = Path(root) / 'outputs' / 'ui' / 'auth-secret.bin'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as handle:
            handle.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    secret = path.read_bytes()
    if len(secret) != 32:
        raise ValueError('로그인 키 저장 파일이 손상됐습니다. 관리자에게 outputs/ui/auth-secret.bin을 확인받으세요.')
    return secret


def auth_key(password):
    return hmac.new(auth_server_secret(str(ROOT)), password.encode('utf-8'), hashlib.sha256).digest()


def issue_auth_token(key, now=None):
    issued = int(time.time() if now is None else now)
    payload = f'{issued}.{secrets.token_urlsafe(16)}'
    signature = hmac.new(key, payload.encode('ascii'), hashlib.sha256).hexdigest()
    return f'{payload}.{signature}'


def valid_auth_token(token, key, now=None):
    try:
        issued, nonce, signature = token.split('.')
        issued_at = int(issued)
        current = int(time.time() if now is None else now)
        if not nonce or not 0 <= current - issued_at <= AUTH_AGE_SECONDS:
            return False
        expected = hmac.new(key, f'{issued}.{nonce}'.encode('ascii'), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return False


def read_auth_cookie():
    return st.context.cookies.get(AUTH_COOKIE, '')


def write_auth_cookie(token):
    value = f'{AUTH_COOKIE}={token}; Max-Age={AUTH_AGE_SECONDS if token else 0}; Path=/; SameSite=Strict'
    st.html(f'<script>document.cookie = {json.dumps(value)} + '
            '(location.protocol === "https:" ? "; Secure" : "");</script>',
            unsafe_allow_javascript=True)


def authorize():
    password = str(setting('UI_PASSWORD'))
    if password:
        key = auth_key(password)
        if st.session_state.get('authorized_for') != key:
            if valid_auth_token(read_auth_cookie(), key):
                st.session_state['authorized_for'] = key
        if st.session_state.get('authorized_for') != key:
            login_area = st.empty()
            with login_area.container():
                entered = st.text_input('운용 비밀번호', type='password')
                login_clicked = st.button('로그인')
            if login_clicked:
                if hmac.compare_digest(entered, password):
                    st.session_state['authorized_for'] = key
                    write_auth_cookie(issue_auth_token(key))
                    login_area.empty()
                else:
                    st.error('비밀번호를 확인하세요.')
            if st.session_state.get('authorized_for') != key:
                st.stop()
        if st.sidebar.button('로그아웃'):
            st.session_state.pop('authorized_for', None)
            write_auth_cookie('')
            st.info('로그아웃했습니다. 화면을 새로고침하면 로그인 화면이 표시됩니다.')
            st.stop()
        return True
    st.info('조회 모드입니다. 작업 실행은 UI_PASSWORD를 설정하고 로그인한 뒤 사용할 수 있습니다.')
    return False


def filter_candidates(rows, search='', industry='전체', signal='전체', rs='전체',
                      volume='전체', multiple=1.5, setup='전체', action='전체',
                      high52='전체', setup_condition='전체'):
    """Preserve unknown values; presentation filters never change decisions."""
    result = []
    for row in rows:
        if search.strip().casefold() not in ' '.join(str(row.get(k) or '') for k in
                                                  ('symbol','name','sector','industry_name')).casefold():
            continue
        if industry != '전체' and (row.get('industry_name') or row.get('sector')) != industry:
            continue
        ratio = row.get('volume_ratio')
        values = {'신호':row.get('signal'), 'RS':row.get('rs_pass'),
                  '거래량':None if ratio is None else ratio >= multiple,
                  '산업':row.get('sector_action')}
        choices = {'신호':signal,'RS':rs,'거래량':volume,'산업':action}
        if any(choice != '전체' and (values[k] is not None if choice == '미확인·미적용'
                else values[k] is not (choice == '통과')) for k, choice in choices.items()):
            continue
        if setup != '전체' and (row.get('setup_phase') or '미확인·미적용') != setup:
            continue
        breakout = row.get('high52_breakout')
        if high52 != '전체' and ('돌파' if breakout is True else '미돌파' if breakout is False else '자료 부족') != high52:
            continue
        setup_states = {'준비 충족':row.get('setup_ready') is True,
                        '준비 미충족':row.get('setup_ready') is False,
                        '셋업 돌파':row.get('setup_trigger') is True,
                        '셋업 무효화':row.get('setup_invalidated') is True,
                        '미확인':row.get('setup_ready') is None}
        if setup_condition != '전체' and not setup_states[setup_condition]:
            continue
        result.append(row)
    return result


def table(rows, names=None):
    if not rows:
        st.info('저장된 기록이 없습니다.')
    else:
        if names:
            rows = named_rows(rows, names)
        st.dataframe(pd.DataFrame(rows).rename(columns=LABELS), hide_index=True,
                     width='stretch')


def account_symbol_names(ops, key):
    return symbol_names(ops.path(ops.workspace(key)['universe']))


def stored_symbol_names(ops, key):
    master = ops.root / 'data' / 'market' / 'master'
    return symbol_names(master / 'kospi.csv', master / 'kosdaq.csv',
                        ops.path(ops.workspace(key)['universe']))


def symbol_label(code, names):
    return f'{names.get(code, "종목명 없음")} · {code}'


def candidate_table_rows(rows, source, include_reason=False):
    from .candidate_universe import _approx_amount

    amount_label = '최근 거래대금 추정액' if source == 'KIS market master' else '당일 거래대금'
    result = []
    for row in rows:
        item = {'종목코드': str(row.get('symbol', '')),
                '종목명': row.get('name') or '종목명 없음',
                '시장': {'KOSPI':'코스피','KOSDAQ':'코스닥'}.get(row.get('market'),row.get('market') or '미확인'),
                '산업': row.get('industry_name') or '산업 분류 없음',
                amount_label: _approx_amount(row.get('recent_turnover_proxy'))}
        if include_reason:
            item['제외 사유'] = row.get('reason_display') or '제외 사유 확인 불가'
        result.append(item)
    return result


def candidate_stage(row):
    if row.get('setup_trigger') is True or row.get('high52_breakout') is True:
        return '종가 돌파'
    close = row.get('close') or row.get('reference_price')
    high = row.get('high')
    levels = [value for value in (row.get('setup_resistance'),row.get('prior_52w_high'))
              if isinstance(value,(int,float)) and value > 0]
    if close is not None and high is not None and any(high >= value > close for value in levels):
        return '고가 접촉·종가 하회'
    if levels and close is not None and row.get('atr') is not None and 0 < min(levels)-close <= row['atr']:
        return '돌파선 접근'
    if row.get('setup_ready') is True:
        return '준비 후보'
    return '그 외'


def high52_distance(row):
    price = row.get('close') or row.get('reference_price')
    level = row.get('prior_52w_high')
    if not isinstance(price,(int,float)) or not isinstance(level,(int,float)) or level <= 0:
        return '자료 부족'
    percent = (price/level-1)*100
    return f'{abs(percent):.1f}% {"돌파" if percent > 0 else "남음"}'


def submit(ops, key, action, as_of, **extra):
    try:
        job = ops.submit(dict(workspace=key, action=action, as_of=as_of, **extra))
        st.session_state[f'job-status-seen-{key}'] = (job['id'],'queued')
        st.session_state[f'job-submitted-{key}'] = job['id']
        st.info(f'{ACTIONS[action]} 요청 접수 · {job["as_of"]}. 작업 상태 영역에서 진행·완료를 확인하세요.')
    except (ValueError, TypeError, KeyError, OSError) as error:
        st.error(str(error))


PHASE_LABELS = {'전체':'전체', 'seeking':'상승 파동 탐색', 'rising':'상승 파동 진행',
                'base':'조정·베이스', 'untradable':'거래량 0 · 계산 불가'}


@lru_cache(maxsize=1)
def wave_component():
    return st.components.v2.component('wave_filter',
        html=(CODE_ROOT / 'trendfollow/web/wave_filter.html').read_text(encoding='utf-8'),
        js="""export default function({parentElement, data, setTriggerValue}) {
            const figure = parentElement.querySelector('figure');
            figure.dataset.phase = data.phase;
            figure.dataset.condition = data.condition;
            for (const node of figure.querySelectorAll('[role=button]')) {
                const choice = node.dataset.phaseChoice || node.dataset.conditionChoice;
                node.setAttribute('aria-pressed', String(node.dataset.conditionChoice
                    ? data.condition === choice : data.phase === choice && (choice !== 'all' || data.condition === 'all')));
                node.onclick = () => setTriggerValue('choice', choice);
                node.onkeydown = e => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); node.onclick(); }
                };
            }
        }""")


def choose_wave(widget, phase, condition):
    choice = st.session_state[widget]['choice']
    previous = st.session_state.get(phase, '전체')
    current = st.session_state.get(condition, '전체')
    st.session_state[phase] = choice if choice in PHASE_LABELS and previous != choice else '전체'
    st.session_state[condition] = ('셋업 돌파' if choice == 'trigger' and
                                   (current != '셋업 돌파' or previous != '전체') else '전체')


@st.cache_resource(ttl=3600)
def chart_quote_client(root):
    from threading import Lock
    from .brokers import Credentials, QuoteClient
    return QuoteClient(Credentials.load('kis', Path(root) / '.env')), Lock()


@st.cache_data(ttl=60, max_entries=512, show_spinner=False)
def chart_session_prices(root, symbol, previous, today):
    client, lock = chart_quote_client(root)
    with lock:
        return client.daily(symbol, previous, today)


def workbench(ops, key, snapshot, as_of=None, can_write=False):
    st.subheader('종목 탐색')
    rows = snapshot['rows']
    if not rows:
        st.info('아직 분석 자료가 없습니다. 「오늘의 운용」에서 자료를 준비하고 분석하세요.')
        return
    st.html("""<style>
    .st-key-market-filters {gap:.35rem}
    .st-key-market-filters label p {font-size:.8rem}
    .st-key-market-filters [data-baseweb="select"] > div,
    .st-key-market-filters [data-baseweb="input"] {min-height:2rem}
    .st-key-market-board [data-testid="stColumn"] {display:flex;align-self:stretch}
    .st-key-market-board [data-testid="stColumn"] > div {flex:1}
    .st-key-market-board [class*="st-key-stagebox"] {
        background:white;border:1px solid #cedad6;border-left:4px solid #397bc1;
        border-radius:6px;padding:.8rem;min-height:8.8rem;height:100%;gap:.35rem}
    .st-key-market-board .st-key-stagebox2 {border-left-color:#17806b}
    .st-key-market-board .st-key-stagebox3 {border-left-color:#bb823c}
    .st-key-market-board .stage-heading {display:flex;justify-content:space-between;
        gap:.5rem;font-weight:650;color:#25384a;font-size:.92rem}
    .st-key-market-board [data-testid="stHorizontalBlock"] {row-gap:.25rem;column-gap:.3rem}
    .st-key-market-board button {min-height:1.55rem;padding:.1rem .4rem;
        background:white;border:1px solid #c8d6d3;border-radius:4px;color:#24463e}
    .st-key-market-board button p {font-size:.76rem;line-height:1.2}
    .st-key-market-board button:hover {border-color:#168575;background:#edf7f4}
    </style>""")
    choices = ['전체','통과','미통과','미확인·미적용']
    phase_key = f'phase-{key}'
    condition_key = f'condition-{key}'
    with st.container(key='market-filters'):
        a,b,c,d = st.columns(4)
        search = a.text_input('종목명·코드·산업 검색', key=f'search-{key}')
        industry = b.selectbox('산업', ['전체'] + sorted({str(r.get('industry_name') or r.get('sector')) for r in rows if r.get('industry_name') or r.get('sector')}))
        rs = c.selectbox('RS 조건', choices)
        signal = d.selectbox('최종 신호', choices)
        a,b,c,d,e = st.columns(5)
        setup = a.selectbox('파동 단계', list(PHASE_LABELS), format_func=PHASE_LABELS.get, key=phase_key)
        setup_condition = b.selectbox('셋업 조건',['전체','준비 충족','준비 미충족','셋업 돌파','셋업 무효화','미확인'], key=condition_key)
        high52 = c.selectbox('52주 신고가',['전체','돌파','미돌파','자료 부족'])
        action = d.selectbox('산업 액션', choices)
        volume = e.selectbox('거래량 조건', choices, help=f'직전 20거래일 평균 대비 {snapshot["rules"]["volume_multiple"]}배 기준')
    if snapshot['rules'].get('entry_method') == 'setup':
        widget = f'wave-{key}'
        wave_component()(key=widget, data={'phase': 'all' if setup == '전체' else setup,
                        'condition': 'trigger' if setup_condition == '셋업 돌파' else ('all' if setup_condition == '전체' else 'other')},
                        on_choice_change=lambda: choose_wave(widget, phase_key, condition_key))
    filtered = filter_candidates(rows, search, industry, signal, rs, volume,
                                 snapshot['rules']['volume_multiple'], setup, action,
                                 high52, setup_condition)
    st.caption(f'전체 {len(rows)}종목 중 {len(filtered)}종목 · 필터는 계좌나 신호를 변경하지 않습니다.')
    descriptions = {'준비 후보':'셋업 준비 조건 충족', '돌파선 접근':'저항선 아래 1 ATR 이내',
                    '종가 돌파':'셋업 저항선 또는 직전 52주 고가 돌파', '고가 접촉·종가 하회':'장중 저항선 접촉 후 종가 하회'}
    board = st.container(key='market-board')
    for idx,(col,(stage,description)) in enumerate(zip(board.columns(4), descriptions.items())):
        members = [r for r in filtered if candidate_stage(r) == stage]
        with col.container(key=f'stagebox{idx}'):
            st.html(f'<div class="stage-heading"><span>{stage}</span><strong>{len(members)}</strong></div>')
            st.caption(description)
            with st.container(horizontal=True, gap='small', vertical_alignment='center'):
                for r in members:
                    st.button(str(r.get('name') or r['symbol']), key=f'card-{key}-{r["symbol"]}',
                              on_click=st.session_state.update, args=({f'card-selection-{key}':r['symbol']},))
                if not members:
                    st.caption('해당 종목 없음')
    if not filtered:
        st.info('선택 조건에 맞는 종목이 없습니다.')
        return
    table_area = st.container()
    detail_area = st.container()
    def price(value):
        return '—' if value is None else f'{value:,.0f}'

    def status(value):
        return {True:'통과', False:'미통과'}.get(value,'미확인')

    shown = pd.DataFrame([{
        '종목': f'{r.get("name") or ""} {r["symbol"]}'.strip(), '산업': r.get('industry_name') or r.get('sector') or '—',
        '종가 / 52주 고가': f'{price(r.get("reference_price"))} / {price(r.get("prior_52w_high"))}',
        '52주 거리': high52_distance(r), 'RS 순위': r.get('rs_percentile'),
        '주간 Mansfield': r.get('mansfield_rs'), '다기간 Mansfield': r.get('multi_rs'),
        'RS 조건':status(r.get('rs_pass')), '산업 조건':status(r.get('sector_action')),
        '파동 단계':PHASE_LABELS.get(r.get('setup_phase'),'미확인'),
        '준비':status(r.get('setup_ready')), '셋업 돌파':status(r.get('setup_trigger')),
        '거래량 배수':r.get('volume_ratio'), '최종 신호':status(r.get('signal')),
        '신호 경로':r.get('signal_entry_path') or '—', '계획 수량':r.get('quantity'),
    } for r in filtered])
    colors = {'통과':'color:#10634d;background-color:#e6f2ec',
              '미통과':'color:#684d43;background-color:#f2edea', '미확인':'color:#67747d'}
    styled = shown.style.format({'RS 순위':'{:.1f}', '주간 Mansfield':'{:.2f}',
                                 '다기간 Mansfield':'{:.2f}', '거래량 배수':'{:.2f}배','계획 수량':'{:,.0f}'},
                                na_rep='—').map(colors.get, subset=['RS 조건','산업 조건','준비','셋업 돌파','최종 신호'])
    styled.map(lambda v:'color:#b43731' if '돌파' in v else 'color:#235cb0' if '남음' in v else '',
               subset=['52주 거리'])
    with table_area:
        st.markdown('**선별 종목**')
        event = st.dataframe(styled, hide_index=True, width='stretch', row_height=34,
                             on_select=lambda: st.session_state.pop(f'card-selection-{key}', None),
                             selection_mode='single-row', key=f'candidates-{key}-{as_of}')
        index = event.selection.rows[0] if event.selection.rows else 0
        index = min(index, len(filtered)-1)
    with detail_area:
        row = next((r for r in filtered if r['symbol'] == st.session_state.get(f'card-selection-{key}')), filtered[index])
        candidate_detail(ops, key, snapshot, row, as_of, can_write=can_write)
    with st.expander('산업별 후보 집계 · 전체 분석 자료 기준'):
        grouped = pd.DataFrame(rows)
        if 'sector' in grouped:
            st.dataframe(grouped.groupby('sector', dropna=False).agg(후보수=('symbol','size'),진입신호=('signal','sum')).reset_index(), hide_index=True)


@lru_cache(maxsize=1)
def candidate_board_component():
    assets = CODE_ROOT / 'trendfollow/web'
    html = (assets / 'candidate_board.html').read_text(encoding='utf-8')
    wave = (assets / 'wave_filter.html').read_text(encoding='utf-8')
    return st.components.v2.component(
        'market_candidate_board', html=html.replace('<div class="wave"></div>', f'<div class="wave">{wave}</div>'),
        css=(assets / 'candidate_board.css').read_text(encoding='utf-8'),
        js=(assets / 'src/filters.js').read_text(encoding='utf-8') + '\n' +
           (assets / 'candidate_board.js').read_text(encoding='utf-8'))


def market_board(ops, key, snapshot, day, selection, can_write=False):
    rows = snapshot['rows']
    if not rows:
        st.info('이 날짜에 표시할 분석 종목이 없습니다.')
        return
    display = []
    for row in rows:
        close, high = row.get('reference_price'), row.get('prior_52w_high')
        display.append(dict(row, stage=candidate_stage(row),
                            gap=(close/high-1)*100 if close is not None and high and high > 0 else None))
    selected_key = f'market-detail-{key}-{day}'
    market_board_controls(ops, key, day, display, snapshot['rules'], can_write, selected_key)
    row = next((r for r in rows if r['symbol'] == st.session_state.get(selected_key)), None)
    if row is None:
        st.caption('종목을 선택하면 이곳에 차트와 선정 근거, 계좌 진입 조건 및 계획 위험이 표시됩니다.')
        return
    candidate_detail(ops, key, snapshot, row, day, selection, show_heading=False)


@st.fragment
def market_board_controls(ops, key, day, display, rules, can_write, selected_key):
    widget = f'market-board-{key}-{day}'

    def save_star():
        change = st.session_state[widget]['favorite']
        if can_write and change and any(r['symbol'] == change.get('symbol') for r in display):
            ops.accounts.set_favorite(key, change['symbol'], change['selected'])

    event = candidate_board_component()(key=widget, data=dict(
        rows=display, rules=rules, day=day, favorites=ops.accounts.favorites(key), canWrite=can_write,
        selected=st.session_state.get(selected_key, ''),
        stages={'준비 후보':'셋업 준비 조건 충족', '돌파선 접근':'저항선 아래 1 ATR 이내',
                '종가 돌파':'셋업 저항선 또는 52주 고가 돌파', '고가 접촉·종가 하회':'장중 저항선 접촉 후 종가 하회'}),
        on_symbol_change=lambda: None, on_favorite_change=save_star)
    if event.symbol is not None and event.symbol != st.session_state.get(selected_key, ''):
        st.session_state[selected_key] = event.symbol
        st.rerun(scope='app')


def candidate_plan(ops, snapshot, day, symbol):
    checkpoint = ops.path(snapshot['workspace']['state']).parent / day / 'state.json'
    if not checkpoint.is_file():
        st.caption(f'{day} 종가 기준 계좌 처리 기록이 없어 계획 수량과 위험을 확인할 수 없습니다.')
        return
    state = json.loads(checkpoint.read_text(encoding='utf-8'))
    if not any(p['symbol'] == symbol for p in state['pending']):
        st.write('이 계좌는 다음 거래일에 이 종목을 매수할 계획이 없습니다.')
        st.caption('계속 살펴볼 종목은 별표로 저장하세요. 장중 관측에서 관심 종목을 불러올 수 있습니다.')
        return
    plans = pending_allocations(state, snapshot['limits'], state['pending'])
    for plan in plans[plans.symbol.eq(symbol)].to_dict('records'):
        st.write(f'예상 수량 **{plan["quantity"]:,.0f}주** · 손절 기준 **{plan["stop_price"]:,.0f}원** · '
                 f'계획 위험 **{plan["planned_risk"]:,.0f}원**')
    st.caption('다음 거래일 장 마감 후 「오늘의 운용」에서 종가 모의 처리를 실행하세요. '
               '종목별 매수 조작 없이 당일 시가와 가용 현금으로 체결 여부와 수량을 결정합니다.')


@st.fragment
def stock_detail_heading(ops, key, row, can_write):
    selected = row['symbol'] in ops.accounts.favorites(key)
    with st.container(horizontal=True, vertical_alignment='center'):
        st.button('★' if selected else '☆', key=f'detail-star-{key}-{row["symbol"]}',
                  help='관심 종목 제거' if selected else '관심 종목 추가', disabled=not can_write,
                  on_click=ops.accounts.set_favorite, args=(key, row['symbol'], not selected))
        st.subheader(str(row.get('name') or row['symbol']))
        st.button('관심 종목에서 제거' if selected else '관심 종목에 추가', disabled=not can_write,
                  key=f'detail-favorite-{key}-{row["symbol"]}', on_click=ops.accounts.set_favorite,
                  args=(key, row['symbol'], not selected))
    st.caption(row['symbol'])


def stock_price_chart(ops, key, snapshot, symbol, as_of):
    try:
        prices = ops.prices(key, symbol, as_of, include_warmup=True)
        if prices:
            from .brokers import BrokerError
            from .krx_sessions import observation_market, previous_close_date
            from .price_chart import provisional_bar
            now = ops.now()
            today = now.date().isoformat()
            if (observation_market(now) == 'krx' and snapshot['workspace'].get('mode') != 'recalculation'
                    and prices[-1]['date'] == (as_of or snapshot.get('as_of')) == previous_close_date(today)):
                try:
                    quotes = chart_session_prices(str(ops.root), symbol, prices[-1]['date'], today)
                    bar = provisional_bar(prices, quotes, today, snapshot['rules']['atr_days'])
                    prices = [*prices, bar]
                    st.caption(f'{today} 마지막 봉은 KIS 정규장 잠정 일봉입니다. 화면을 다시 읽으면 갱신됩니다(조회 간격 1분).')
                except (ValueError, BrokerError) as error:
                    st.caption(f'오늘 잠정봉 미표시 · {error}')
            st.components.v1.html(chart_html(prices), height=550, scrolling=False)
            st.caption('선택일 이전 최대 15개월 저장 일봉 · 봉을 가리키면 좌측 상단에 OHLCV·ATR 표시')
        else:
            st.info('가격 이력이 없습니다.')
    except (ValueError, OSError):
        st.info('저장된 가격 이력이 없습니다.')


def candidate_detail(ops, key, snapshot, row, as_of, selection=None, show_heading=True, can_write=False, chart_day=None):
    if show_heading:
        stock_detail_heading(ops, key, row, can_write)
    if selection:
        member = next((r for r in selection['members'] if r['symbol'] == row['symbol']), None)
        if member:
            summary = selection['summary']
            basis = '최근 거래대금 추정액' if summary.get('source') == 'KIS market master' else '당일 거래대금'
            from .candidate_universe import _approx_amount
            amount = _approx_amount(member.get('recent_turnover_proxy'))
            threshold = summary.get('min_daily_turnover', summary.get('min_recent_turnover_proxy'))
            criterion = f' · 선정 기준 {threshold/1e8:g}억원 이상' if threshold is not None else ''
            st.write(f'**시장 선정 근거** · {as_of} {summary["source"]} 선정 목록 · {basis} {amount}{criterion}')
    if as_of:
        close = row.get('reference_price')
        levels = [('셋업 저항선', row.get('setup_resistance')), ('직전 52주 고가', row.get('prior_52w_high'))]
        distances = []
        for label, level in levels:
            if close is not None and level and level > 0:
                gap = (close / level - 1) * 100
                distance = '일치' if gap == 0 else f'{abs(gap):.1f}% {"돌파" if gap > 0 else "남음"}'
                distances.append(f'{label} {level:,.0f}원 · {distance}')
        st.write('**후보 상태** · ' + ' / '.join([candidate_stage(row), *distances]))
        st.markdown('**다음 거래일 진입 계획**')
        candidate_plan(ops, snapshot, as_of, row['symbol'])
    with st.container(horizontal=True, gap='small'):
        st.button('장중 관측에서 열기', key='add-watch', on_click=open_watch_symbol,
                  args=(key, row['symbol']), help='이 종목을 관측 대상에 담고 장중 관측 화면으로 이동합니다.')
        if as_of and snapshot.get('account_as_of'):
            st.button('오늘의 운용 · 최근 진입 계획 보기', on_click=open_page, args=('오늘의 운용',))
            if (ops.path(snapshot['workspace']['state']).parent / as_of / 'state.json').is_file():
                st.button(f'운용 기록 · {as_of} 계획 보기', on_click=open_plan_history, args=(key, as_of))
    st.metric('저장된 종가', '—' if row.get('reference_price') is None else f'{row["reference_price"]:,.0f}원')
    stock_price_chart(ops, key, snapshot, row['symbol'], chart_day or as_of)
    st.markdown('#### 계좌 진입 조건 · '+str(as_of or snapshot.get('as_of') or '기준일 미확인'))
    rules = snapshot['rules']
    rs_rule = (f'주간 Mansfield > {rules["min_mansfield"]:g}' if rules.get('rs_method') == 'mansfield'
               else f'RS 순위 ≥ {rules["min_rs"]:g}, 시장 대비 초과수익 > 0')
    route = (('셋업 또는 52주 신고가 돌파' if rules.get('setup_52w_enabled') else '셋업 돌파')
             if rules.get('entry_method') == 'setup' else f'직전 {rules["breakout_days"]}거래일 고가 돌파')
    st.caption(f'{snapshot["workspace"]["name"]} · {rs_rule} · {route} · 거래량 {rules["volume_multiple"]:g}배 이상')
    verdicts = {'주봉 추세':row.get('weekly_trend'), rs_rule:row.get('rs_pass')}
    volume = row.get('volume_ratio')
    verdicts[f'거래량 ≥ 직전 20일 평균의 {rules["volume_multiple"]:g}배'] = (
        volume >= rules['volume_multiple'] if volume is not None else None)
    if rules.get('entry_method') == 'setup':
        if rules.get('sector_require_action'):
            verdicts['산업 동반 강세'] = row.get('sector_action')
        verdicts['셋업 경로 · 준비'] = row.get('setup_ready')
        verdicts['셋업 경로 · 저항선 돌파'] = row.get('setup_trigger')
        if rules.get('setup_52w_enabled'):
            verdicts['별도 경로 · 52주 신고가 돌파'] = row.get('high52_breakout')
    else:
        high, close = row.get('prior_breakout_high'), row.get('reference_price')
        verdicts[route] = close > high if close is not None and high is not None else None
    verdicts['최종 진입 신호'] = row.get('signal')
    st.dataframe([{'조건':label, '판정':{True:'충족', False:'미충족'}.get(result,'미확인')}
                  for label,result in verdicts.items()], hide_index=True, width='stretch')
    if not as_of:
        table([{field:row.get(field) for field in ('volume_ratio','stop_price','quantity','planned_risk','allocation_reason')}])
    if snapshot['rules'].get('entry_method') == 'setup':
        missing = ('조정·베이스 형성 전' if row.get('setup_phase') in ('seeking','rising') else '자료 없음')
        fields = {'setup_retracement':'준비 판정 반납률', 'setup_retracement_today':'오늘 저가 포함 반납률',
                  'setup_volume_ratio':'거래량 수축비', 'setup_range_ratio':'변동성 수축비',
                  'setup_resistance':'셋업 저항선', 'prior_52w_high':'직전 252거래일 최고가'}
        def value(field):
            number = row.get(field)
            if number is None:
                return missing if field.startswith('setup_') else '자료 없음'
            if 'retracement' in field:
                return f'{number:.1%}'
            return f'{number:.2f}' if field.endswith('ratio') else f'{number:,.0f}원'
        st.dataframe([{'항목':label, '기준일 값':value(field)} for field,label in fields.items()],
                     hide_index=True, width='stretch')


def observed_time_label(value):
    if not value:
        return '시각 기록 없음'
    if 'T' not in value:
        return f'{value} · 시각 기록 없음'
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    if stamp.tzinfo is None:
        return stamp.strftime('%Y-%m-%d %H:%M') + ' · 시간대 기록 없음'
    return stamp.astimezone(KST).strftime('%Y-%m-%d %H:%M KST')


def open_page(page):
    if page == '날짜별 시장 후보':
        page = '시장 후보'
    section = page if page == '시장 후보' else '운용 기록' if page in ('운용 기록', '보유·체결', '종목 탐색', '날짜별 시장 후보') else '오늘의 운용'
    st.session_state['page-picker'] = section
    st.session_state['detail-page'] = None if page in (section, '보유·체결') else page


def account_changed():
    open_page('오늘의 운용')


def open_watch_symbol(key, symbol):
    watch_key = f'watch-{key}'
    st.session_state[watch_key] = list(dict.fromkeys([*st.session_state.get(watch_key, []), symbol]))
    open_page('장중 관측')


def open_plan_history(key, day):
    st.session_state[f'history-day-{key}'] = day
    open_page('운용 기록')


def suggested_watch_symbols(snapshot, plans, selected=()):
    """Reuse an intentional watch list before falling back to account plans."""
    if selected:
        return sorted(set(selected)), '선택한 관측 목록'
    earlier = [plan for plan in plans if plan.get('targets')]
    if earlier:
        previous = max(earlier, key=lambda plan: (plan['session'], plan['id']))
        return sorted(set(previous['targets'])), f'{previous["session"]} 관측 대상 재사용'
    symbols = {row['symbol'] for group in ('pending', 'positions') for row in snapshot[group]
               if row.get('symbol')}
    return sorted(symbols), '계좌의 보유·다음 거래일 계획'


@st.fragment(run_every='5s')
def home_watch_status(ops, key, as_of, automatic_date):
    from .candidate_universe import latest_completed_session

    if automatic_date and latest_completed_session(ops.now()) != as_of:
        st.rerun(scope='app')
    watch = ops.intraday(key)
    if not watch['control']['active_or_unconfirmed']:
        st.rerun(scope='app')
    run = next((item for item in watch['runs'] if item['session'] == watch['today']), None)
    if not run:
        st.info('이 계좌의 오늘 관측 기록을 기다리고 있습니다.')
        return
    summary = run['summary']
    if summary.get('connection_state') == 'failed':
        st.error(f'연결 실패 · {summary.get("failure_reason") or summary.get("kis_error_code") or "연결 상태를 확인하세요."}')
    st.write(f'{run["session"]} 최근 관측 · 구독 승인 {summary.get("subscription_acks", 0)}'
             f' / 요청 {summary.get("subscriptions_requested", 0)} · 수신 체결 {summary.get("quote_ticks", 0)}건')
    quotes = summary.get('latest_target_quotes') or {}
    if quotes:
        names = account_symbol_names(ops, key)
        st.dataframe([{'종목': symbol_label(symbol, names), '마지막 체결 시각': observed_time_label(q.get('timestamp')),
                       '마지막 체결가': q.get('price'), '누적 거래량': q.get('cumulative_volume')}
                      for symbol, q in quotes.items()], hide_index=True, width='stretch')
    st.caption('정규장 종가가 준비되면 이 화면에서 당일 모의 처리를 진행하세요.')


def pending_allocations(state, account_limits, pending):
    from .risk import Account, Limits, allocate

    positions = [dict(position, symbol=symbol, current_price=position['last_price'])
                 for symbol, position in state['positions'].items()]
    cash = state['cash']
    equity = cash + sum(pos['quantity'] * pos['current_price'] for pos in positions)
    limits = Limits(**{**account_limits, 'units': state['units']})
    return allocate(pd.DataFrame(pending), Account(equity, cash, positions), limits)


def close_plan_summary(ops, key, snapshot):
    day = snapshot['account_as_of']
    if not day or snapshot['status'] != 'ready':
        return
    st.markdown(f'#### {day} 종가 기준 · 다음 거래일 진입 계획')
    pending = snapshot['pending']
    if not pending:
        st.write('진입 후보 0종목 · 신규 진입에 배정할 위험 0원')
        st.caption('다음 거래일 종가가 준비되면 다시 처리하세요. 기존 보유 종목은 계속 관리합니다.')
        return

    state = ops.state(ops.workspace(key))
    if not state or state['last_date'] != day:
        st.info('계좌 처리일이 바뀌었습니다. 자료를 다시 읽어 주세요.')
        return
    plans = pending_allocations(state, snapshot['limits'], pending)
    names = stored_symbol_names(ops, key)
    st.write(f'진입 후보 {plans.symbol.nunique()}종목 · 예상 배정 {int(plans.quantity.gt(0).sum())}건'
             f' · 합산 계획 위험 {plans.planned_risk.sum():,.0f}원')
    st.dataframe(pd.DataFrame([{
        '종목': symbol_label(row['symbol'], names),
        '진입 구분': '추가 매수' if row.get('order_intent') == 'add' else '신규 매수',
        '종가 기준가(원)': row['reference_price'], '손절 기준(원)': row.get('stop_price'),
        '예상 수량(주)': row['quantity'], '계획 위험(원)': round(row['planned_risk']),
    } for row in plans.to_dict('records')]), hide_index=True, width='stretch')
    st.caption('다음 거래일 장 마감 후 이 화면에서 종가 모의 처리를 실행하세요. 종목별 매수 조작 없이 '
               '당일 시가와 가용 현금으로 체결 여부와 수량을 결정합니다.')


def workflow(ops, key, snapshot, as_of, can_write):
    st.subheader('오늘의 운용 · 다음 단계')
    g = ops.guidance(key, as_of)
    st.caption('계좌를 선택하면 필요한 거래일과 자료를 확인합니다. 아래 안내된 버튼부터 누르세요.')
    pending = g.get('pending_sessions', [])
    ops.refresh_jobs()
    active_jobs = [job for job in ops.jobs.values() if job['status'] in {'queued', 'running'}]
    active = bool(active_jobs)
    recent = next((job for job in reversed(list(ops.jobs.values())) if job['workspace'] == key), None)
    priority_job = any(job['workspace'] == key for job in active_jobs) or bool(
        recent and recent['action'] in {'daily', 'paper', 'industry', 'analyze'}
        and recent['status'] in {'failed', 'interrupted'} and recent.get('as_of') == as_of)
    missing = g.get('candidate_missing_sessions', [])
    from .candidate_universe import historical_candidate_source
    from .krx_sessions import observation_market, previous_close_date

    historical = [day for day in missing if day != g['today']]
    candidate_source = st.session_state.get('historical-source', 'auto')
    source_error = None
    selected_source = None
    if historical:
        try:
            selected_source = historical_candidate_source(ops.root, candidate_source)
        except ValueError as error:
            source_error = str(error)
    market = observation_market(ops.now())
    market_open = market is not None
    watch = (ops.intraday(key) if market_open and (g['processed'] or market == 'krx') and as_of == g['recommended_as_of']
             and snapshot['rules'].get('entry_method') == 'setup'
             and not snapshot['rules'].get('market_units_enabled') else None)
    with st.container(border=True):
        st.markdown(f'**선택 계좌: {snapshot["workspace"]["name"]} · 자동 기준일: {as_of}**')
        st.caption({'krx': '정규장 · 장중 관측 시간', 'nxt_after': '정규장 마감 · 애프터마켓 관측 시간'}.get(market, '관측 시간 외 · 최근 완료 거래일 기준'))
        if g['blocked']:
            st.warning(g['blocked'])
        elif active:
            if active_jobs[-1]['workspace'] == key:
                st.info('이 계좌 작업이 진행 중입니다. 위에서 처리 날짜와 현재 단계를 확인하세요.')
            else:
                st.info('다른 계좌의 작업이 진행 중입니다. 해당 계좌를 선택해 진행 상태를 확인하세요.')
        elif watch:
            basis = previous_close_date(watch['today'])
            today_plan = next((plan for plan in reversed(watch['plans'])
                               if plan['session'] == watch['today'] and plan['as_of'] == basis), None)
            if watch['control']['active_or_unconfirmed']:
                home_watch_status(ops, key, as_of, as_of == g['recommended_as_of'])
                st.button('장중 관측 결과 보기', on_click=open_page, args=('장중 관측',))
            elif today_plan:
                st.write(f'{watch["today"]} 감시 계획이 준비됐습니다 · 종목 {len(today_plan["targets"])}개')
                if st.button('저장된 계획으로 장중 관측 시작', type='primary', disabled=not can_write):
                    try:
                        seconds = max(1, int((ops.now().replace(hour=20, minute=0, second=0, microsecond=0)
                                               - ops.now()).total_seconds()))
                        ops.start_watch(dict(workspace=key, plan_id=today_plan['id'],
                                                       seconds=min(seconds, 39600)))
                        st.session_state.pop(f'job-submitted-{key}', None)
                        st.rerun()
                    except (ValueError, OSError) as error:
                        st.error(str(error))
            else:
                symbols, origin = suggested_watch_symbols(snapshot, watch['plans'],
                                                           st.session_state.get(f'watch-{key}', []))
                if not symbols:
                    names = account_symbol_names(ops, key)
                    symbols = st.multiselect('오늘 관측할 종목', sorted(names), format_func=lambda code: symbol_label(code, names),
                                             key=f'home-watch-{key}')
                    origin = '선택한 종목'
                if symbols:
                    st.write(f'장중 관측 대상 {len(symbols)}종목 · {origin}')
                    st.caption(', '.join(symbols[:12]) + (' 외' if len(symbols) > 12 else ''))
                    if st.button('자료 준비 후 장중 관측 시작', type='primary', disabled=not can_write):
                        submit(ops, key, 'watch_auto', basis, session=watch['today'],
                               symbols=symbols, watch_mode='symbols')
                else:
                    st.info('장중 관측 대상이 없습니다. 처음 한 번은 종목을 선택해야 합니다.')

        elif g['processed']:
            st.success(f'{as_of}까지 종가 처리가 완료됐습니다.')
            st.caption('다음 거래일에는 새 종가가 준비된 뒤 이 화면에서 모의 처리를 실행하세요.')
        elif pending:
            st.write('빠진 시장 종목 목록·산업 자료·일봉을 준비하고, 거래일 순서대로 모의계좌에 반영합니다.')
            if missing:
                note = (f'과거 {len(historical)}일: {"KRX Open API" if selected_source == "krx_open_api" else "pykrx"}'
                        if historical and selected_source else '')
                today_note = '오늘 목록: KIS 현재 마스터' if g['today'] in missing else ''
                st.caption(f'자동 준비할 시장 종목 목록 {len(missing)}일 · {" · ".join(part for part in (note, today_note) if part)}')
            if source_error:
                st.warning(source_error)
                st.button('날짜별 시장 후보에서 수동 선택', on_click=open_page, args=('날짜별 시장 후보',))
            can_start = (can_write and not source_error and snapshot['status'] != 'configuration_mismatch'
                         and snapshot['workspace'].get('mode') != 'recalculation')
            if st.button(f'{as_of}까지 필요한 자료 준비·종가 처리', type='primary', disabled=not can_start):
                submit(ops, key, 'daily', as_of, price_source=st.session_state.get('price-source', 'naver'),
                       candidate_source=candidate_source)
            st.caption(f'일봉 원천: {"pykrx" if st.session_state.get("price-source") == "pykrx" else "네이버"} · '
                       '변경하려면 왼쪽 「날짜·자료 원천 직접 변경」을 여세요.')
            if not can_write:
                st.caption('실행하려면 운용 비밀번호로 로그인하세요.')
            elif snapshot['status'] == 'configuration_mismatch':
                st.button('계좌 설정 불일치 확인', on_click=open_page, args=('정정·검증',))
        else:
            st.info('선택한 날짜에 처리할 거래일이 없습니다. 목표일을 확인하세요.')
    if priority_job:
        job_status(ops, key, show_next=False)
    close_plan_summary(ops, key, snapshot)
    if g['processed']:
        orders = snapshot['logs'].get('orders', [])
        fills = [row for row in orders if row.get('status') == 'filled']
        st.write(f'계좌 현금 {snapshot["cash"]:,.0f}원 · 보유 {len(snapshot["positions"])}종목')
        if fills:
            with st.expander(f'{as_of} 모의 체결 내역'):
                table(fills, stored_symbol_names(ops, key))

    with st.expander('처리 날짜·자료 상태 확인'):
        a, b, c = st.columns(3)
        a.metric('마지막 종가 처리', g.get('last_processed_date') or '미시작')
        b.metric('목표 거래일', as_of)
        c.metric('남은 거래일', f'{len(pending)}일')
        if pending:
            text = ', '.join(pending) if len(pending) <= 8 else f'{pending[0]} ~ {pending[-1]}'
            st.caption(f'처리 순서: {text}')
            if len(pending) > 8:
                st.write(', '.join(pending))
    with st.expander('자료 상태와 수동 작업'):
        for col, page in zip(st.columns(2), ('장중 관측', '정정·검증')):
            col.button(page, key=f'nav-{page}', on_click=open_page, args=(page,))
        if g['requires_industry']:
            status = '준비됨' if g['industry_ready'] else '미수집'
            st.write(f'{as_of} 산업 동료 수익률: {status}')
            if g['industry_ready']:
                st.caption(('재구성에 사용한 산업 구성: ' if g.get('industry_reconstructed') else '산업 자료 관측: ')
                           + observed_time_label(g.get('industry_observed_at')))
                classification = g.get('industry_classification_observed_at')
                if classification and classification != g.get('industry_observed_at'):
                    st.caption(f'산업 분류 관측: {observed_time_label(classification)}')
            elif g.get('industry_latest'):
                st.caption(f'마지막 저장 수익률 날짜: {g["industry_latest"]}')
            st.caption('선택 날짜의 산업 자료가 없어도 종가 처리 중 준비할 수 있습니다. 과거 누락일은 이전 저장 구성을 사용해 재구성합니다.')
            if as_of == g['today'] and st.button('오늘 산업 자료 다시 수집', disabled=not can_write or active):
                submit(ops, key, 'industry', as_of)
        else:
            st.caption('이 계좌 전략에는 산업 자료 갱신이 필요하지 않습니다.')
        st.caption('첫 종가 처리는 계좌 기준점과 다음 계획을 저장합니다. 이후 거래일부터 현금·보유·모의 체결을 반영합니다. 실제 주문은 없습니다.')
        disabled = not can_write or bool(g['blocked']) or active
        if st.button('후보만 재계산', disabled=disabled or not snapshot['files_ready']):
            submit(ops,key,'analyze',as_of)
        if st.button('저장 가격으로 모의 처리', disabled=disabled or g['processed'] or not snapshot['files_ready'] or not g['industry_ready']):
            submit(ops,key,'paper',as_of)
    if not priority_job:
        job_status(ops, key, show_next=False)


def missing_candidate_sessions(start, end, saved_dates):
    from .krx_sessions import calendar

    if start > end or start < end - timedelta(days=180):
        raise ValueError('수집 시작일은 최근 완료 거래일 이전 180일 안에서 선택하세요.')
    exchange = calendar(end.year)
    if not exchange.is_session(start):
        raise ValueError('수집 시작일은 거래일로 선택하세요.')
    sessions = [day.date().isoformat() for day in exchange.sessions_in_range(start, end)]
    if len(sessions) > 120:
        raise ValueError('한 번에 최대 120거래일까지 수집할 수 있습니다. 시작일을 늦추세요.')
    saved = set(saved_dates)
    return [day for day in sessions if day not in saved]


@st.fragment(run_every='5s')
def complete_candidate_analysis(ops, key, day, inputs):
    from .market_candidate_analysis import saved_analysis_current

    attempt_key = '|'.join(('candidate-repair', key, day, *inputs))
    if saved_analysis_current(ops.root, key, day, *inputs):
        st.session_state.pop(attempt_key, None)
        st.rerun(scope='app')
    ops.refresh_jobs()
    with ops.lock:
        jobs = [dict(job) for job in ops.jobs.values()]
    active = next((job for job in jobs if job['status'] in {'queued', 'running'}), None)
    if active:
        if (active['workspace'], active['action'], active['as_of']) == (key, 'market_analyze', day):
            st.session_state[attempt_key] = active['id']
            st.info(f'{day} 후보 분석 자동 보완 중 · {active.get("progress") or "자료 확인 중"}')
        else:
            st.info(f'진행 중인 작업이 끝나면 {day} 후보 분석을 자동으로 보완합니다.')
        return
    previous = next((job for job in jobs if job['id'] == st.session_state.get(attempt_key)), None)
    if previous and previous['status'] != 'completed':
        st.warning(previous.get('error') or '후보 분석 보완 결과를 확인할 수 없습니다.')
        if st.button('자동 보완 다시 시도', key=f'retry-{key}-{day}'):
            del st.session_state[attempt_key]
            st.rerun()
        return
    try:
        job = ops.submit(dict(workspace=key, action='market_analyze', as_of=day, selected_only=True))
    except ValueError as error:
        st.warning(str(error))
        return
    st.session_state[attempt_key] = job['id']
    st.info(f'{day} 후보 분석에 필요한 자료를 확인하고 있습니다.')


@lru_cache(maxsize=256)
def analysis_symbols(path, modified_ns, size):
    return frozenset(pd.read_csv(path, usecols=['symbol'], dtype=str)['symbol'])


def account_favorite_board(ops, key, snapshot, dates, can_write):
    favorites = ops.accounts.favorites(key)
    if not favorites:
        st.info('저장한 관심 종목이 없습니다. 시장 후보 표에서 종목 옆 ☆를 누르세요.')
        return
    day = dates['capture_as_of']
    selection = ops.candidate_selection(day) if day in dates['selection_dates'] else None
    members = {r['symbol']: r for r in selection['members']} if selection else {}
    names = stored_symbol_names(ops, key)
    for code, member in members.items():
        if member.get('name'):
            names[code] = member['name']
    def label(code):
        status = ('후보에 포함' if code in members else '후보에 없음') if selection else '후보 목록 미저장'
        return f'{symbol_label(code, names)} · {status}'
    st.caption(f'관심 종목 {len(favorites)}개 · 후보 포함 여부는 최근 완료 거래일 {day} 기준')
    code = st.radio('상세를 볼 관심 종목', favorites, format_func=label, horizontal=True,
                    key=f'favorite-detail-{key}')
    st.button('★ 관심 종목에서 제거', disabled=not can_write, key=f'remove-favorite-{key}-{code}',
              on_click=ops.accounts.set_favorite, args=(key, code, False))
    for analysis_day in sorted(dates['signal_dates'], reverse=True):
        path = ops.latest_signals(snapshot['workspace'], analysis_day)
        if not path:
            continue
        stat = path.stat()
        if code not in analysis_symbols(path, stat.st_mtime_ns, stat.st_size):
            continue
        detail = ops.snapshot(key, analysis_day)
        row = next((item for item in detail['rows'] if item['symbol'] == code), None)
        if row:
            st.subheader(str(row.get('name') or names.get(code) or code))
            st.caption(f'최근 저장 분석 {analysis_day} · 차트 조회 기준 {day}')
            basis = ops.candidate_selection(analysis_day) if analysis_day in dates['selection_dates'] else None
            candidate_detail(ops, key, detail, row, analysis_day, basis, show_heading=False, chart_day=day)
            return
    st.subheader(symbol_label(code, names))
    st.info('이 계좌에서 이 종목의 전략 조건을 분석한 기록이 없습니다.')
    stock_price_chart(ops, key, snapshot, code, day)


def market_candidates(ops, key, snapshot, can_write):
    from .market_candidate_analysis import saved_analysis_current, selection_inputs
    st.subheader('날짜별 시장 후보')
    dates = ops.candidate_dates(key)
    mode = st.radio('조회 목록', ['거래일별 시장 후보', '계좌 관심 종목 전체'], horizontal=True,
                    key=f'market-view-{key}')
    if mode == '계좌 관심 종목 전체':
        account_favorite_board(ops, key, snapshot, dates, can_write)
        return
    available = sorted(set(dates['reference_dates'] + dates['signal_dates'] + dates['selection_dates']), reverse=True)
    preferred = next((day for group in (dates['market_analysis_dates'],dates['account_analysis_dates'],
                                        dates['selection_dates'],dates['reference_dates']) for day in group),available[0])
    selected = st.selectbox('보고 싶은 거래일', available,width=290,index=available.index(preferred),key=f'candidate-date-{key}',
                            help='그날의 저장된 종목 목록과 전략 조건 결과를 조회합니다. 목록이 없으면 ①에서 날짜별 자료 원천을 선택해 저장하세요.')
    st.caption(f'{selected}에 저장된 종목 목록·편입/제외·전략 조건 결과를 조회합니다. '
               '그날 목록이 없으면 ①에서 날짜별 자료 원천을 선택해 저장하세요.')
    selection_saved = selected in dates['selection_dates']
    inputs = selection_inputs(ops, snapshot['workspace'], selected) if selection_saved else ()
    analyzed = selection_saved and saved_analysis_current(ops.root, key, selected, *inputs)
    needs_analysis = selection_saved and not analyzed
    st.write('선정 기록:', '있음' if selection_saved else '없음', '· 시장 조건 계산:', '완료' if analyzed else '미완료')
    if needs_analysis:
        if can_write:
            complete_candidate_analysis(ops, key, selected, inputs)
        else:
            st.info('후보 분석 보완이 필요합니다. 쓰기 가능한 환경에서 자동으로 진행합니다.')

    with st.expander('종목 목록·전략 조건 준비', expanded=not (analyzed or selected in dates['account_analysis_dates'])):
        st.markdown('#### ① 날짜별 종목 목록 저장')
        st.caption('거래일별로 매수 검토 대상이 될 종목 목록을 저장합니다. 모의계좌 판단에 필요한 '
                   '각 종목의 일봉은 「오늘의 운용」에서 가격 갱신·종가 처리할 때 채웁니다.')
        source = st.selectbox('후보군 자료 원천', ['kis','krx_open_api','pykrx'],
                              format_func=lambda value: {'kis':'KIS 현재 마스터','krx_open_api':'KRX Open API 날짜별','pykrx':'pykrx 날짜별'}[value])
        if source == 'kis':
            st.caption('조회 시점의 현재 KIS 목록만 저장합니다. 과거 날짜 목록은 가져올 수 없습니다.')
        else:
            st.caption('완료 거래일의 종목·거래 자료로 누락 목록을 만들고, 직전 저장일과 편입·제외를 비교합니다. KIS 선정 결과와 다를 수 있습니다.')
        suggested = dates.get('suggested_capture_start') or dates.get('capture_window_start') or dates['reference_dates'][-1]
        start = None
        missing = []
        valid_range = False
        if source != 'kis':
            with st.expander('수집 시작일 직접 바꾸기 · 고급'):
                start = st.date_input('누락 자료 수집 시작일', value=date.fromisoformat(suggested),
                                      key=f'candidate-start-{key}',
                                      help='기본값은 자동으로 찾은 첫 누락일입니다. 이 날짜부터 최근 완료 거래일까지 저장되지 않은 목록을 채웁니다.')
            if not selection_saved and start.isoformat() > selected:
                st.warning(f'{selected}의 종목 목록이 없습니다. 현재 수집 시작일 {start}은 더 늦어 이 날짜를 채우지 않습니다.')
            if 'missing_selection_count' not in dates:
                st.warning('실행 중인 UI 서버의 후보 날짜 조회가 이전 버전입니다. 서버를 다시 시작하고 화면을 새로고침하세요.')
            end = date.fromisoformat(dates['capture_as_of'])
            try:
                missing = missing_candidate_sessions(start, end, dates['selection_dates'])
                valid_range = True
            except ValueError as error:
                st.warning(str(error))
            if valid_range and missing:
                st.info(f'{start}~{dates["capture_as_of"]}에서 저장되지 않은 거래일 목록을 채웁니다. '
                        f'누락 {len(missing)}거래일 · 이미 저장된 날짜는 건너뜁니다.')
            elif valid_range:
                st.success(f'{start}~{end}에 누락된 종목 목록이 없습니다.')
        can_capture = (can_write and (dates['capture_available'] if source == 'kis'
                       else source in dates.get('candidate_sources', []) and valid_range and bool(missing)))
        capture_label = ('저장할 누락 목록 없음' if source != 'kis' and valid_range and not missing
                         else '누락된 날짜의 종목 목록 저장')
        if st.button(capture_label, disabled=not can_capture):
            submit(ops,key,'candidate_universe',dates['capture_as_of'],candidate_source=source,
                   **({'from_as_of':start.isoformat()} if start else {}))
        st.markdown('#### ② 선택한 거래일의 전략 조건 확인')
        st.caption('화면에서 선택한 거래일의 종목별 RS·진입 조건 판정 결과를 보여줍니다. '
                   '계산·조회만으로 계좌 설정이나 계좌의 현금·보유 종목·모의 체결 내역은 바뀌지 않습니다.')
        if analyzed:
            st.caption(f'{selected} 기준 결과가 아래에 표시됩니다. 입력이 같으면 재계산 없이 저장된 결과를 사용합니다.')
        button_label = ('저장된 결과 표시 중 · 재계산 가능' if analyzed else '선택한 거래일까지 전략 조건 계산')
        if st.button(button_label, disabled=not can_write or not analyzed):
            submit(ops,key,'market_analyze',selected)
        if selection_saved:
            selection = ops.candidate_selection(selected, include_excluded=True)
            st.caption(f'{selected} 기준 선정 종목 {len(selection["members"])}개 · 자료 원천 {selection["summary"]["source"]}')
            with st.expander(f'{selected} 선정 종목과 편입·제외 기록'):
                st.caption('전일 대비 제외는 이전 저장일에는 선정됐지만 이날 빠진 종목입니다. '
                           '당일 선정 제외는 이날 시장 자료에서 확인했으나 선정 기준을 충족하지 못한 종목입니다.')
                with st.popover('표 항목 설명'):
                    st.write('거래대금: KRX Open API·pykrx는 해당 거래일의 거래대금, KIS는 최근 거래대금 추정액입니다. '
                             '선정 기준은 요약에 표시된 금액입니다.')
                    st.write('산업: 저장된 산업 구성으로 표시한 이름입니다. 해당 종목의 산업 정보가 없으면 산업 분류 없음으로 표시합니다.')
                    st.write('내부 저장값 benchmark는 상대강도 비교에 쓰는 대표 종목 코드입니다. '
                             '코스피 069500, 코스닥 229200이며 계좌 설정이 시장지수 기준이면 지수를 사용합니다. '
                             '산업 코드는 산업별 계산을 위한 내부 식별자입니다.')
                groups = [('선정', 'members', False), ('새 편입', 'added', False),
                          ('전일 대비 제외', 'removed', True), ('당일 선정 제외', 'excluded', True)]
                tabs = st.tabs([f'{label} {len(selection.get(group, []))}개' for label, group, _ in groups])
                for tab, (_, group, show_reason) in zip(tabs, groups):
                    with tab:
                        table(candidate_table_rows(selection.get(group, []),
                                                   selection['summary']['source'], show_reason))

    if analyzed or (not needs_analysis and selected in dates['account_analysis_dates']):
        detail = ops.snapshot(key, selected)
        analysis = detail.get('analysis') or {}
        if analysis.get('industry_membership_reconstructed'):
            st.warning(f'{selected} 산업 구성은 당시 저장본이 없어 '
                       f'{analysis["industry_membership_observed_at"]} 저장본으로 재구성했습니다. '
                       '당시 실제 구성과 다를 수 있으며 모의계좌 매매 기록에는 반영되지 않습니다.')
        market_board(ops,key,detail,selected,selection if selection_saved else None,can_write)


def account_history(ops, key, snapshot):
    from .operations.common import read_csv

    st.subheader('운용 기록')
    for col, page in zip(st.columns(2), ('종목 탐색', '날짜별 시장 후보')):
        col.button(page, key=f'nav-{page}', on_click=open_page, args=(page,))
    root = ops.path(snapshot['workspace']['state']).parent
    dates = sorted((path.parent.name for path in root.glob('????-??-??/state.json')
                    if path.parent.name <= (snapshot['account_as_of'] or '')), reverse=True)
    if not dates:
        st.info('저장된 일별 계좌 기록이 없습니다. 오늘의 운용에서 첫 종가 처리를 실행하세요.')
        return
    day = st.selectbox('계좌 처리 거래일', dates, key=f'history-day-{key}')
    record = json.loads((root / day / 'state.json').read_text(encoding='utf-8'))
    names = stored_symbol_names(ops, key)
    positions = record['positions']
    st.write(f'종가 처리 후 현금 {record["cash"]:,.0f}원 · 보유 {len(positions)}종목')
    st.markdown('#### 이 종가로 세운 다음 거래일 계획')
    plans = record['pending']
    if plans:
        table([{k: row.get(k) for k in ('symbol','reference_price','stop_price','volume_ratio')}
               for row in plans], names)
    else:
        st.write('진입 후보 없음')
    symbols = sorted(set(positions) | {row['symbol'] for row in plans})
    if symbols:
        symbol = st.selectbox('종목별 가격 차트', symbols, index=None,
                              format_func=lambda code: symbol_label(code, names),
                              key=f'history-symbol-{key}-{day}', placeholder='종목 선택')
        if symbol:
            bars = ops.prices(key, symbol, day, include_warmup=True)
            if bars:
                st.components.v1.html(chart_html(bars), height=550, scrolling=False)
            else:
                st.info('선택 거래일까지 저장된 가격이 없습니다.')
    if plans and dates.index(day) > 0:
        execution_day = dates[dates.index(day)-1]
        orders = read_csv(root / execution_day / 'orders.csv')
        codes = {row['symbol'] for row in plans}
        if not orders.empty:
            orders = orders.loc[orders.symbol.isin(codes) & orders.date.astype(str).str[:10].eq(execution_day)]
        st.markdown(f'#### 다음 처리일({execution_day}) 후보 종목의 모의 주문 기록')
        table(orders.to_dict('records'), names)
    elif plans:
        st.caption('다음 거래일 모의 처리 후 체결 결과가 표시됩니다.')
    with st.expander('이날 보유 종목'):
        table([dict(pos, symbol=symbol) for symbol,pos in positions.items()], names)
    for filename, label in [('orders','모의 주문·체결'),('trades','청산 손익'),('events','운용 이벤트'),
                            ('campaign-risk','위험 검토'),('pyramiding-review','추가매수 검토')]:
        with st.expander(f'{day} {label}'):
            table(read_csv(root / day / f'{filename}.csv').to_dict('records'), names)


def recovery(ops, key, snapshot, as_of, can_write):
    st.subheader('과거 자료 정정·운용 검증')
    st.caption('변경 내역을 먼저 대조합니다. 원계좌 계속 운용과 별도 재계산 계좌 중 결과를 확인한 뒤 선택하세요.')
    if st.button('과거 입력 변경 내역 대조',disabled=not can_write or not snapshot['account_as_of']):
        submit(ops,key,'revision_check',as_of)
    view = ops.revision_view(key,preview=False)
    policy = st.selectbox('정정 정책',['manual','volume_only'],
                          index=0 if view['policy']=='manual' else 1,
                          format_func=lambda value:'모든 정정 수동 검토' if value == 'manual' else '거래량만 자동 반영')
    if st.button('정정 정책 저장',disabled=not can_write):
        try:
            ops.revision_policy(dict(workspace=key,policy=policy))
            st.success('계좌별 정정 정책을 저장했습니다.')
        except ValueError as error:
            st.error(str(error))
    report = view['report']
    if not report:
        st.info('저장된 변경 보고서가 없습니다.')
        return
    st.write(f'대조 계좌 처리일 {report["account_as_of"]} · 변경 {report["row_changes"]}건 · 보고서 {report["id"]}')
    if report['applied']:
        st.success('정정 반영 완료 · 원래 결정과 변경 이력을 보존했습니다.')
    elif report['stale']:
        st.warning('보고서 이후 계좌가 바뀌었습니다. 다시 대조하세요.')
    else:
        st.warning(report.get('block_reason') or '변경 내역을 검토한 뒤 처리 방법을 선택하세요.')
    st.write(report.get('explanation',''))
    names = stored_symbol_names(ops, key)
    query = st.text_input('변경 종목명·코드 검색', key=f'revision-symbol-{key}',
                          placeholder='예: 삼성전자 또는 005930').strip()
    matches = matching_symbols(names, query)
    exact = [code for code in matches if code.casefold() == query.casefold()
             or names[code].casefold() == query.casefold()]
    selected = None
    direct_code = ''
    if query and len(exact) == 1:
        selected = exact[0]
    elif query and len(matches) == 1:
        selected = matches[0]
    elif query and matches:
        selected = st.selectbox('검색 결과에서 종목 선택', matches[:50], index=None,
                                format_func=lambda code: symbol_label(code, names),
                                key=f'revision-match-{key}')
        if len(matches) > 50:
            st.caption(f'{len(matches)}개가 검색됐습니다. 더 구체적인 종목명을 입력하세요.')
    elif query and len(query) == 6 and query.isascii() and query.isalnum():
        direct_code = query.upper()
    elif query:
        st.info('저장된 종목 목록에서 일치하는 이름이나 코드를 찾지 못했습니다.')
    if selected:
        st.caption(f'검색 종목: {symbol_label(selected, names)}')
    page = st.number_input('변경 내역 페이지 · 100건씩',min_value=1,value=1,key=f'revision-page-{key}')
    changes = ops.revision_view(key, report['id'], offset=(page-1)*100,
                                symbol=direct_code, symbols=([selected] if selected else [] if query and not direct_code else None))
    st.caption(f'조건에 맞는 변경 {changes["total"]}건')
    table(changes['rows'], names)
    if not report['applied'] and not report['stale']:
        reviewed = st.checkbox('변경 내역과 이전 결정 보존 방식을 확인했습니다.',key=f'revision-reviewed-{key}')
        price_reviewed = (st.checkbox('OHLC 가격·수량 기준의 변경도 확인했습니다.',key=f'price-reviewed-{key}')
                          if report.get('price_fields_changed') else False)
        allowed = can_write and reviewed and report.get('can_continue') and (not report.get('price_fields_changed') or price_reviewed)
        if st.button('정정 반영 후 원계좌 계속 운용',disabled=not allowed):
            submit(ops,key,'revision_continue',report['as_of'],report_id=report['id'],
                   catch_up_to=as_of,reviewed=True,price_reviewed=price_reviewed)
        if st.button('별도 정정 비교 계좌 생성',disabled=not can_write):
            submit(ops,key,'revision_replay',report['as_of'],report_id=report['id'])
    st.subheader('종목·계좌 위험과 추가매수 검토')
    table(snapshot['logs'].get('campaign-risk',[]), names)
    table(snapshot['logs'].get('pyramiding-review',[]), names)


def observation_table(rows, names, session, kind):
    if not rows:
        st.caption('기록 없음')
        return
    fields = {
        'hour':'시간대', 'timestamp':'판단 시각' if kind == 'events' else '체결 시각',
        'received_at':'수신 시각', 'decision_at':'판정 시각', 'kind':'기록 유형',
        'market':'시장', 'price':'체결가(원)', 'cumulative_volume':'누적 거래량(주)',
        'planned_previous_close':'계획 전일 종가(원)', 'previous_close':'KIS 전일 종가(원)',
        'price_basis_mismatch':'가격 기준', 'sample_status':'표본 상태',
        'breakout_present':'돌파', 'holding_stop_breached':'손절선 도달',
        'industry_action':'산업 조건', 'entry_watch':'진입 관찰', 'setup_invalidated':'셋업 무효화',
        'volume_pass':'거래량 조건', 'entry_confirmed':'진입 확인', 'volume_status':'거래량 판정 근거',
        'peer_count':'관측 동료 수', 'expected_peer_count':'대상 동료 수', 'peer_coverage':'동료 수신 비율',
        'peer_up_fraction':'동료 상승 비율', 'peer_excess_return':'동료 초과수익률',
        'day_low':'당일 저가(원)', 'holding_stop':'손절 기준(원)',
        'volume_ratio_same_time':'동시간 거래량 배수', 'volume_reference_sessions':'비교 거래일 수',
        'volume_sample_at':'거래량 표본 시각', 'volume_vs_previous_same_time':'전일 동시간 거래량 비율',
        'volume_current_cumulative':'표본 누적 거래량(주)', 'volume_reference_mean':'평균 누적 거래량(주)',
        'volume_multiple':'기준 배수', 'volume_min_sessions':'최소 비교 거래일 수', 'ui_current':'현재 유효',
    }
    labels = {'krx':'정규장', 'nxt_after':'애프터마켓', 'signal_state':'신호 변화',
              'holding_stop_state':'보유 손절 점검', 'verified':'충족', 'weak':'부족',
              'unavailable_same_time_history':'동시간 거래량 기록 없음', 'insufficient_history':'비교 거래일 부족',
              'current_sample_unavailable':'현재 표본 없음', 'stale_or_invalid_quote':'유효 시세 없음',
              'zero_reference_volume':'기준 거래량 0'}
    fields = {k:v for k,v in fields.items() if any(k in row for row in rows)}
    numbers = {}
    def display(field, value):
        if value is None:
            return '미확인'
        if field in ('timestamp','hour','received_at','decision_at','volume_sample_at'):
            try:
                stamp = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return '미확인'
            if stamp.tzinfo:
                stamp = stamp.astimezone(KST)
            prefix = '' if stamp.date().isoformat() == session else stamp.strftime('%m-%d ')
            return prefix + stamp.strftime('%H시' if field == 'hour' else '%H:%M:%S')
        if isinstance(value, bool):
            if field == 'price_basis_mismatch':
                return '불일치' if value else '일치'
            return '예' if value else '아니요'
        if isinstance(value, (int, float)):
            if field in ('peer_coverage','peer_up_fraction','peer_excess_return'):
                numbers[fields[field]] = '{:.1%}'
            else:
                numbers[fields[field]] = '{:,.2f}' if '배수' in fields[field] or '비율' in fields[field] else '{:,.0f}'
            return value
        return labels.get(value, value)
    records = []
    for row in rows:
        record = {'종목':symbol_label(row['symbol'], names) if row.get('symbol') else '—'}
        record.update({label:display(field,row.get(field)) for field,label in fields.items()})
        records.append(record)
    for record in records:
        for column in numbers:
            if record[column] == '미확인':
                record[column] = None
    frame = pd.DataFrame(records)
    colors = {'불일치':'color:#973d2c;background-color:#fbece6',
              '일치':'color:#14664f;background-color:#e7f3ed','미확인':'color:#647381'}
    styled = frame.style.format(numbers, na_rep='미확인').map(lambda v: colors.get(v,''))
    st.dataframe(styled, hide_index=True, width='stretch', row_height=32)


def matches_watch(record, day, chosen, mode):
    return (bool(chosen) and record['session'] == day and record.get('watch_mode') == mode
            and set(chosen) == set(record.get('selected_symbols', record['targets'])))


def observation(ops, key, as_of, can_write):
    st.subheader('장중 관측')
    st.caption('KIS 체결을 받아 잠정 조건을 확인합니다. 모의계좌의 현금·보유는 바뀌지 않으며 장 마감 후 종가 처리는 별도로 실행합니다.')
    data = ops.intraday(key)
    names = account_symbol_names(ops, key)
    watch_key = f'watch-{key}'
    selected = [code for code in st.session_state.get(watch_key, []) if code in names]
    if st.session_state.get(watch_key) != selected:
        st.session_state[watch_key] = selected
    market_open = data['regular_session']
    session = data['today'] if market_open else data['default_session']
    if market_open:
        st.info(f'오늘 {session} 관측 시간입니다. 시작할 계획은 자동으로 오늘 날짜를 사용합니다.')
    else:
        st.info(f'현재 관측 시간이 아닙니다. {session} 계획을 미리 저장할 수 있으며 관측 ON은 해당 거래 시간에만 가능합니다.')
    st.markdown('#### ① 대상 선택')
    favorites = ops.accounts.favorites(key)
    available = [symbol for symbol in favorites if symbol in names]
    if st.button('관심 종목 불러오기', disabled=not available,
                 help='선택 계좌의 관심 종목을 현재 선택에 추가합니다. 관측 시작은 별도로 선택하세요.'):
        st.session_state[watch_key] = list(dict.fromkeys([*selected, *available]))
    if len(available) < len(favorites):
        st.caption(f'계좌의 관측 대상 범위에 없는 관심 종목 {len(favorites)-len(available)}개는 불러올 수 없습니다.')
    chosen = st.multiselect('감시 종목명·코드 검색', sorted(names),
                            format_func=lambda code: symbol_label(code, names),
                            key=watch_key, placeholder='종목명을 입력하고 결과를 선택하세요')
    watch_mode = st.selectbox('감시 범위', ['symbols', 'industry'],
                              format_func=lambda value: '입력 종목만' if value == 'symbols' else '전체 산업 포함')
    st.caption(f'선택 {len(chosen)}종목 · 전체 산업 포함은 동종업종 종목까지 KIS 구독 요청이 늘어납니다.')
    basis = None
    try:
        basis = ops.intraday_service.plan_basis(key, session)
        st.caption(f'판단 기준: 직전 거래일 {basis["as_of"]} 종가 · 산업 자료 {"준비됨" if basis["industry_ready"] else "없음"}')
    except ValueError as error:
        st.warning(str(error))
    if basis and chosen:
        try:
            preview = ops.intraday_service.subscription_preview(
                key, basis['as_of'], chosen, watch_mode, session)
            st.caption(f'예상 KIS 구독 {preview["count"]}건 · 산업 {len(preview["industries"])}개')
        except ValueError as error:
            st.caption(f'구독 수는 계획 준비 후 확인할 수 있습니다: {error}')
    if basis and watch_mode == 'industry' and not basis['industry_ready']:
        st.caption('산업 자료가 없으면 입력 종목만 관측을 선택할 수 있습니다.')
    st.markdown('#### ② 관측 시작·종료')
    running = bool(data['control']['active_or_unconfirmed'])
    if running:
        st.success('관측이 실행 중이거나 KIS 연결 상태를 확인 중입니다.')
    elif data['control']['enabled']:
        st.warning('관측 설정은 ON이지만 실행 중인 연결은 확인되지 않습니다. 새 관측을 시작하기 전에 상태를 확인하세요.')
    else:
        st.caption('현재 실행 중인 관측이 없습니다.')
    start_disabled = (not can_write or not market_open or not chosen or basis is None or running
                      or watch_mode == 'industry' and not basis['industry_ready'])
    if market_open:
        if st.button('선택 종목으로 관측 ON', type='primary', disabled=start_disabled):
            submit(ops, key, 'watch_auto', basis['as_of'], symbols=chosen,
                   session=session, watch_mode=watch_mode)
        st.caption('필요한 기준 자료와 계획을 준비한 뒤 관측을 시작합니다. 20:00까지 관측하며 언제든 OFF할 수 있습니다.')
    elif st.button(f'{session} 관측 계획만 저장', disabled=not can_write or not chosen or basis is None
                   or watch_mode == 'industry' and not basis['industry_ready']):
        submit(ops, key, 'prepare_intraday', basis['as_of'], symbols=chosen,
               session=session, watch_mode=watch_mode)
    if st.button('관측 OFF', disabled=not can_write or not data['control']['enabled']):
        result = ops.stop_watch()
        st.success('관측 종료 요청을 보냈습니다. 연결 종료 상태를 확인하세요.')
    with st.expander('저장된 계획 확인·다시 시작'):
        plans = [p for p in data['plans'] if matches_watch(p, session, chosen, watch_mode)]
        if plans:
            plans_by_id = {p['id']: p for p in plans}
            plan = st.selectbox(f'{session} 저장 계획', list(plans_by_id),
                                format_func=lambda plan_id: ', '.join(
                                    symbol_label(code, names) for code in plans_by_id[plan_id]['targets'][:3]))
            if st.button('이 계획으로 관측 ON', disabled=not can_write or not market_open or running):
                try:
                    seconds = max(1, min(39600, int((ops.now().replace(
                        hour=20, minute=0, second=0, microsecond=0) - ops.now()).total_seconds())))
                    result = ops.start_watch(dict(workspace=key, plan_id=plan, seconds=seconds))
                    st.session_state.pop(f'job-submitted-{key}', None)
                    st.info(f'관측 시작 요청 접수 · 실행 ID {result["run_id"]} · KIS 연결 확인 중')
                except (ValueError, OSError) as error:
                    st.error(str(error))
        else:
            st.caption('선택한 종목의 대상 거래일 저장 계획이 없습니다. 위 버튼을 누르면 새로 준비합니다.')
    st.markdown('#### ③ 관측 결과')
    current_runs = [run for run in data['runs'] if matches_watch(run, data['today'], chosen, watch_mode)]
    if current_runs:
        run = current_runs[0]
        summary = run['summary']
        targets = ', '.join(symbol_label(code, names) for code in run['targets'][:3])
        connection = {'active':'연결 중', 'starting':'연결 준비 중', 'failed':'연결 실패',
                      'closed':'종료', 'completed':'완료', 'reconnecting':'재연결 중'}.get(summary.get('connection_state'), '미확인')
        st.write(f'오늘 최근 관측 · {targets} · {connection}'
                 f' · KIS 구독 요청 {summary.get("subscriptions_requested", "미확인")} / 승인 {summary.get("subscription_acks", "미확인")}'
                 f' · 수신 체결 {summary.get("quote_ticks", "미확인")}')
        if summary.get('kis_error_code') or summary.get('rejected_symbol'):
            rejected = summary.get('rejected_symbol')
            st.error(f'KIS 구독 또는 연결 실패 · 오류 {summary.get("kis_error_code", "미확인")}'
                     + (f' · 거절 종목 {symbol_label(rejected, names)}' if rejected else ''))
        elif summary.get('connection_state') == 'failed':
            reason = summary.get('failure_reason') or summary.get('last_disconnect_reason') or '상세 사유 기록 없음'
            display = 'KIS WebSocket 연결 오류' if reason == 'connection_error' else reason
            st.error(f'KIS 연결 실패 · {display} · 구독 승인 '
                     f'{summary.get("subscription_acks", "미확인")}/{summary.get("subscriptions_requested", "미확인")}. '
                     '연결 환경과 해당 실행 기록을 확인하세요.')
        quotes = summary.get('latest_target_quotes') or {}
        if quotes:
            st.write('선택 종목의 최근 체결')
            observation_table([dict(quote, symbol=code) for code, quote in quotes.items()],
                              names, run['session'], 'quotes')
        elif summary.get('connection_state') in {'starting', 'active'}:
            st.info('아직 선택 종목의 체결을 받지 못했습니다. 잠시 후 자료 다시 읽기를 누르세요.')
        with st.expander('시간대별 관측·판단 근거'):
            st.caption('시간대별 잠정 가격·거래량과 조건 변화 기록입니다. 종가 확정 결과는 아닙니다.')
            st.write('시간대별 잠정 관측')
            observation_table(summary.get('hourly_quotes') or [], names, run['session'], 'hourly')
            st.write('판단 이력')
            observation_table(run['events'], names, run['session'], 'events')
        with st.expander('이 관측의 원본 요약'):
            st.json(summary, expanded=False)
    else:
        st.info('현재 선택한 종목과 감시 범위로 실행한 오늘 관측 기록이 없습니다. 관측을 시작하면 이곳에 표시됩니다.')
    with st.expander('이전 관측 기록·종료 후 처리'):
        st.caption('과거 관측은 현재 선택 종목의 상태와 별개입니다. 종가 대조·거래량 누적은 관측 종료 후 필요할 때만 실행합니다.')
        if data['runs']:
            runs_by_id = {run['id']: run for run in data['runs']}
            run_id = st.selectbox('관측 기록 선택', list(runs_by_id),
                                  format_func=lambda selected_id: f'{runs_by_id[selected_id]["session"]} · '
                                  + ', '.join(symbol_label(code, names) for code in runs_by_id[selected_id]['targets'][:2])
                                  + f' · {selected_id}')
            history = runs_by_id[run_id]
            history_summary = history['summary']
            st.caption(f'연결 {history_summary.get("connection_state", "미확인")} · '
                       f'요청 {history_summary.get("subscriptions_requested", "미확인")} / '
                       f'승인 {history_summary.get("subscription_acks", "미확인")} · '
                       f'체결 {history_summary.get("quote_ticks", "미확인")}')
            history_mode = '전체 산업 포함' if history.get('watch_mode') == 'industry' else '입력 종목만'
            st.caption(f'실행 당시 감시 범위 · {history_mode}')
            if history_summary.get('kis_error_code'):
                rejected = history_summary.get('rejected_symbol')
                st.caption(f'이 실행의 KIS 오류 · {history_summary["kis_error_code"]}'
                           + (f' · 거절 종목 {symbol_label(rejected, names)}' if rejected else ''))
            with st.expander('선택한 관측 기록 상세'):
                old_quotes = history_summary.get('latest_target_quotes') or {}
                observation_table([dict(quote, symbol=code) for code, quote in old_quotes.items()],
                                  names, history['session'], 'quotes')
                st.write('시간대별 잠정 관측')
                observation_table(history_summary.get('hourly_quotes') or [], names, history['session'], 'hourly')
                st.write('판단 이력')
                observation_table(history['events'], names, history['session'], 'events')
            from .candidate_universe import latest_completed_session
            close_ready = history['session'] <= latest_completed_session(ops.now())
            if not close_ready:
                st.caption(f'{history["session"]} 종가와 대조·거래량 누적은 해당 거래일 16:10 이후 가능합니다.')
            for action in ['intraday_close', 'volume_import']:
                finished = history_summary.get('connection_state') not in {'starting', 'active'}
                if st.button(ACTIONS[action], disabled=not can_write or not close_ready
                             or action == 'volume_import' and not finished):
                    submit(ops, key, action, history['session'], run_id=run_id)
        else:
            st.caption('저장된 관측 기록이 없습니다.')
        if st.button('동시간 거래량 기준 생성', disabled=not can_write):
            submit(ops, key, 'volume_profile', as_of)
        st.caption('거래량 누적은 관측 종료 후 16:10 이후 수행합니다. 기준 생성에는 최소 5개 유효 거래일이 필요합니다.')


def settings(ops, snapshot, can_write):
    st.subheader('적용 전략')
    st.json(dict(rules=snapshot['rules'],limits=snapshot['limits']),expanded=False)
    workspace = snapshot['workspace']
    st.markdown('#### 날짜별 선정 종목을 이 계좌의 신규 매수 후보로 사용')
    st.caption('장 마감 후 「오늘의 운용」에서 새 거래일의 종가 모의 처리를 실행할 때 사용합니다. '
               '처리하는 거래일에 저장된 선정 종목만 신규 매수 대상으로 검토하며, 계좌의 RS·진입 조건도 충족해야 합니다. '
               '선정 목록에서 빠진 기존 보유 종목은 계속 관리합니다.')
    policy = snapshot.get('candidate_policy')
    workspace = snapshot['workspace']
    if policy:
        st.success(f'이 계좌에 이미 적용된 설정 · {policy["enabled_after"]}까지는 기존 방식, '
                   '다음 처리 거래일부터 날짜별 선정 종목 사용')
        button_label = '이미 적용됨'
    elif workspace.get('builtin'):
        st.info('기본 계좌에는 적용할 수 없습니다. 일반 계좌를 선택하세요.')
        button_label = '기본 계좌에는 적용 불가'
    elif workspace.get('mode') == 'recalculation':
        st.info('정정 비교 계좌에는 적용할 수 없습니다. 원계좌를 선택하세요.')
        button_label = '정정 비교 계좌에는 적용 불가'
    elif not snapshot['account_as_of']:
        st.info('첫 종가 처리 전입니다. 사이드바의 「오늘의 운용」에서 기준일을 정하고 '
                '「일봉 수집 + 미처리 거래일 모의 처리」를 완료한 뒤 적용할 수 있습니다.')
        button_label = '첫 종가 처리 후 사용 가능'
    elif snapshot['status'] != 'ready':
        st.warning('계좌 설정과 저장 자료 상태를 확인한 뒤 적용할 수 있습니다.')
        button_label = '계좌 상태 확인 필요'
    elif not can_write:
        st.info('운용 비밀번호로 로그인한 뒤 적용할 수 있습니다.')
        button_label = '로그인 후 사용 가능'
    else:
        st.caption(f'현재 계좌 처리일 {snapshot["account_as_of"]}까지의 기록은 유지합니다. '
                   '적용 후 다음 미처리 거래일부터 사용합니다.')
        button_label = '이 계좌에 날짜별 후보군 적용'
    can_apply = (can_write and not policy and bool(snapshot['account_as_of'])
                 and not workspace.get('builtin') and workspace.get('mode') != 'recalculation'
                 and snapshot['status'] == 'ready')
    if st.button(button_label, disabled=not can_apply):
        submit(ops,workspace['id'],'candidate_policy',snapshot['account_as_of'])

    st.subheader('계좌 관리')
    st.write(f'계좌 유형: {"정정 비교" if workspace.get("mode") == "recalculation" else "일반"} · 처리일: {snapshot["account_as_of"] or "미시작"} · 상태: {"보관" if workspace.get("archived_at") else "사용 중"}')
    if not workspace.get('builtin'):
        with st.form(f'rename-{workspace["id"]}'):
            name = st.text_input('계좌 이름 변경',value=workspace['name'])
            if st.form_submit_button('이름 저장',disabled=not can_write):
                try:
                    ops.rename_workspace(workspace['id'],name)
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
        label = '보관 해제' if workspace.get('archived_at') else '계좌 보관'
        if st.button(label,disabled=not can_write):
            try:
                ops.set_archived(workspace['id'],not bool(workspace.get('archived_at')))
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    st.subheader('새 비교 계좌 만들기')
    presets = ops.presets()
    preset = st.selectbox('전략 예제', presets, format_func=lambda p:p['name'])
    with st.form('new-account'):
        name = st.text_input('계좌 이름', value='추세추종 비교 계좌')
        capital = st.number_input('초기 모의자금(원)',min_value=1,value=10000000)
        config = st.text_area('전략 JSON · rules / limits',json.dumps(dict(rules=preset['rules'],limits=preset['limits']),ensure_ascii=False,indent=2),height=300,key=f'config-{preset["id"]}')
        if st.form_submit_button('별도 계좌 저장',disabled=not can_write):
            try:
                cfg = json.loads(config)
                if not isinstance(cfg,dict) or set(cfg)-{'rules','limits'}:
                    raise ValueError('rules와 limits만 지정하세요.')
                row = ops.create_workspace(dict(name=name,capital=capital,**cfg))
                st.session_state['account'] = row['id']
                st.rerun()
            except (ValueError,TypeError,KeyError) as error:
                st.error(str(error))
    st.caption('기존 계좌는 변경하지 않습니다. 저장만으로 모의 처리를 시작하지 않습니다.')


@st.fragment(run_every='3s')
def job_status(ops, key, show_next=True):
    ops.refresh_jobs()
    with ops.lock:
        jobs = [dict(j) for j in ops.jobs.values() if j['workspace']==key][-10:]
    if not jobs:
        return
    job = jobs[-1]
    status = job['status']
    error = job.get('error') or ''
    if status == 'failed' and '예상하지 못한 RuntimeError' in error:
        diagnostic = Path(ops.home) / 'jobs' / f'{job["id"]}.traceback.txt'
        if diagnostic.is_file() and 'cannot schedule new futures after interpreter shutdown' in diagnostic.read_text(encoding='utf-8'):
            status = 'interrupted'
            error = ('UI 서버 종료로 작업이 중단됐습니다. 완료된 거래일은 계좌에 보존됐으며, '
                     '남은 날짜는 서버 재시작 후 이어 처리할 수 있습니다.')
    statuses = {'queued':'대기','running':'진행 중','completed':'완료','failed':'실패','interrupted':'중단'}
    label = ACTIONS.get(job['action'], job['action'])
    as_of = job.get('as_of', '—')
    prominent = status in {'queued', 'running'} or st.session_state.get(f'job-submitted-{key}') == job['id']
    if status in {'queued','running'}:
        with st.container(border=True):
            st.markdown(f'#### {label} · {as_of}')
            current,total = job.get('progress_current'),job.get('progress_total')
            if isinstance(current,int) and isinstance(total,int) and total > 0:
                st.progress(min(1.0,current/total), text=f'전체 거래일 {current}/{total} 완료'
                            if job['action'] == 'daily' else f'전체 작업 {current}/{total}')
                st.write(f'현재 단계: {job.get("progress") or "작업 시작 중"}')
            else:
                st.info(job.get('progress') or '현재 단계를 처리 중입니다.')
            stage_current,stage_total = job.get('stage_current'),job.get('stage_total')
            if isinstance(stage_current,int) and isinstance(stage_total,int) and stage_total > 0:
                st.progress(min(1.0,stage_current/stage_total),
                            text=f'현재 단계 {stage_current}/{stage_total}')
            if job['action'] == 'daily':
                state = ops.state(ops.workspace(key))
                if state:
                    st.caption(f'계좌에 반영된 마지막 거래일: {state["last_date"]} · 목표: {as_of}')
            st.caption('작업 중 서버를 종료하면 중단될 수 있습니다. 완료 또는 실패 표시를 확인한 뒤 재시작하세요.')
    elif status == 'completed' and prominent:
        reused = (job.get('result') or {}).get('status') == 'already_present'
        st.markdown(f'✅ 최근 작업 · **{label}** · {as_of} · 완료'
                    f'{" (저장된 결과 사용)" if reused else ""}')
    elif status in {'failed','interrupted'} and prominent:
        st.error(f'{label} · {as_of} · {statuses[status]}: {error or "작업 기록을 확인하세요."}')
    if show_next and prominent and job['action'] == 'daily' and status in {'completed','failed','interrupted'}:
        state = ops.state(ops.workspace(key))
        last = state['last_date'] if state else None
        if last:
            st.caption(f'계좌에 반영된 마지막 거래일: {last} · 선택한 목표일: {as_of}')
        if status == 'completed' and last == as_of:
            st.success('다음 단계: 「운용 기록」에서 현금·보유 종목·다음 거래일 계획을 확인하세요. '
                       '장중 관측이 필요하면 그다음 「장중 관측」으로 이동하세요.')
            st.button('운용 기록 보기', key=f'job-holdings-{job["id"]}',
                      on_click=open_page, args=('운용 기록',))
        elif last and last < as_of:
            st.info(f'{last}까지 보존됐습니다. 서버를 다시 시작한 뒤 「오늘의 운용」에서 ②를 실행하면 '
                    f'{as_of}까지 남은 거래일을 이어 처리합니다.')
            st.button('오늘의 운용으로 이동', key=f'job-workflow-{job["id"]}',
                      on_click=open_page, args=('오늘의 운용',))
    if status not in {'queued','running'}:
        with st.expander(f'작업 기록 보기{f" · 이전 {len(jobs)-1}건" if len(jobs) > 1 else ""}'):
            st.caption(f'최근 작업 · {label} · {as_of} · {statuses.get(status,status)}')
            if status in {'failed', 'interrupted'}:
                st.write(error or '작업 기록을 확인하세요.')
            if job.get('result') is not None:
                st.json(job['result'], expanded=False)
            if len(jobs) > 1:
                for old in reversed(jobs[:-1]):
                    created = observed_time_label(old.get('created_at')).removesuffix(' KST')
                    st.caption(f'{created} · {ACTIONS.get(old["action"],old["action"])} · '
                               f'{statuses.get(old["status"],old["status"])}')
    seen_key = f'job-status-seen-{key}'
    previous = st.session_state.get(seen_key)
    st.session_state[seen_key] = (job['id'],status)
    if previous in {(job['id'],'queued'),(job['id'],'running')} and status in {'completed','failed','interrupted'}:
        st.rerun(scope='app')


def main():
    st.set_page_config(page_title='TrendFollow · 오늘의 운용',page_icon='📈',layout='wide')
    st.markdown('**TrendFollow** · 모의계좌 운용 · 실제 주문 없음')
    # Streamlit secrets remain server-side; brokers already accept environment variables.
    for provider, names in [('KIS',['ENV','APP_KEY','APP_SECRET']),('KIWOOM',['ENV','APP_KEY','SECRET_KEY']),
                            ('KRX',['ID','PW','OPEN_API_KEY'])]:
        for suffix in names:
            name = f'{provider}_{suffix}'
            value = setting(name)
            if value:
                os.environ[name] = str(value)
    can_write = authorize()
    ops = operations(str(ROOT))
    if getattr(type(ops), 'source_fingerprint', None) != source_fingerprint(CODE_ROOT):
        ops.refresh_jobs()
        active = [job for job in ops.jobs.values() if job['status'] in {'queued','running'}]
        st.error('실행 중인 UI 서버의 코드가 변경됐습니다. 새 작업 접수를 막았습니다.')
        if active:
            st.warning(f'진행 중인 작업 {len(active)}건은 기존 코드로 계속 실행됩니다. '
                       '아래에서 완료·실패를 확인한 뒤 Streamlit 서버를 다시 시작하세요. '
                       '지금 서버를 종료하면 작업이 중단될 수 있습니다.')
            job_status(ops, active[-1]['workspace'])
        else:
            st.info('Streamlit 서버를 종료하고 `streamlit run streamlit_app.py`로 다시 시작한 뒤 '
                    '이 화면을 새로고침하세요. 저장된 후보·계좌 결과는 유지됩니다.')
        st.stop()
    rows = ops.workspaces()
    ids = [w['id'] for w in rows]
    names = {w['id']:w['name'] + (' · 보관' if w.get('archived_at') else '') for w in rows}
    with st.sidebar:
        st.header('운용 계좌')
        preferred = st.session_state.pop('account', None)
        if preferred in ids:
            st.session_state['account-picker'] = preferred
        account_col, settings_col = st.columns([3, 1], vertical_alignment='bottom')
        key = account_col.selectbox('계좌 선택', ids, format_func=names.get, key='account-picker',
                                    on_change=account_changed)
        settings_col.button('설정', key='nav-전략·설정', help='계좌·전략 설정',
                            on_click=open_page, args=('전략·설정',))
        sections = ['오늘의 운용', '시장 후보', '운용 기록']
        if st.session_state.get('page-picker') not in sections:
            account_changed()
        section = st.radio('화면', sections, horizontal=True, key='page-picker',
                           on_change=lambda: st.session_state.pop('detail-page', None))
        page = st.session_state.get('detail-page') or section
        if page != section:
            st.button(f'{section}으로 돌아가기', on_click=open_page, args=(section,))
        recommended = ops.guidance(key)['recommended_as_of']
        st.caption(f'자동 기준일: {recommended}')
        with st.expander('날짜·자료 원천 직접 변경'):
            as_of = st.date_input('종가 기준일',value=date.fromisoformat(recommended),
                                  key=f'date-{key}-{recommended}').isoformat()
            st.selectbox('일봉 자료 원천', ['naver', 'pykrx'],
                         format_func=lambda value: {'naver': '네이버', 'pykrx': 'pykrx'}[value],
                         key='price-source')
            st.selectbox('과거 후보 자료 원천', ['auto', 'krx_open_api', 'pykrx'],
                         format_func=lambda value: {'auto': '자동 선택', 'krx_open_api': 'KRX Open API',
                                                     'pykrx': 'pykrx'}[value], key='historical-source')
        st.button('자료 다시 읽기')
        with st.expander('배포·저장 안내'):
            st.caption('Community Cloud의 로컬 저장은 영구 보존되지 않습니다. 앱 휴면 시 관측의 지속 실행도 보장되지 않습니다.')
    try:
        snapshot = ops.snapshot(key)
        if page != '오늘의 운용':
            st.caption(f'{names[key]} · 저장 기준일 {snapshot["as_of"] or "분석 전"} · 계좌 처리일 {snapshot["account_as_of"] or "미시작"}')
        if page != '오늘의 운용':
            job_status(ops,key)
        if snapshot['status']=='configuration_mismatch':
            st.error('계좌 설정과 저장 자료가 일치하지 않습니다. 기존 계좌를 보존하고 새 비교 계좌를 만드세요.')
        if not snapshot['files_ready']:
            st.warning('시세 또는 유니버스 자료가 준비되지 않았습니다. 저장된 실제 데이터 없이 가상 결과를 표시하지 않습니다.')
        if page=='종목 탐색': workbench(ops,key,snapshot,can_write=can_write)
        elif page=='시장 후보': market_candidates(ops,key,snapshot,can_write)
        elif page=='오늘의 운용': workflow(ops,key,snapshot,as_of,can_write)
        elif page=='운용 기록': account_history(ops,key,snapshot)
        elif page=='장중 관측': observation(ops,key,as_of,can_write)
        elif page=='정정·검증': recovery(ops,key,snapshot,as_of,can_write)
        else: settings(ops,snapshot,can_write)
    except (ValueError,KeyError,TypeError,OSError) as error:
        st.error(f'자료 표시 오류 ({type(error).__name__}): {error}')
        st.caption('저장된 계좌·후보 자료는 변경되지 않았습니다. 표시된 오류 내용을 확인하세요.')
