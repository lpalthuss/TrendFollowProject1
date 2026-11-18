"""Paper Blue workbench. Rendering delegates all mutations to Operations."""
from datetime import date, datetime
from functools import lru_cache
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

import pandas as pd
import altair as alt
import streamlit as st
from dotenv import dotenv_values

from .ui_service import KST, Operations
from .runtime_guard import source_fingerprint

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
ACTIONS = {'industry':'산업 자료 갱신','daily':'가격 갱신 + 종가 모의 처리',
           'analyze':'후보만 재계산','paper':'저장 가격으로 모의 처리',
           'prepare_intraday':'관측 계획 생성','intraday_close':'장중 기록과 종가 대조',
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


def table(rows):
    if not rows:
        st.info('저장된 기록이 없습니다.')
    else:
        st.dataframe(pd.DataFrame(rows).rename(columns=LABELS), hide_index=True,
                     width='stretch')


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
    if close is not None and row.get('atr') is not None and any(
        close < value and value-close <= row['atr'] for value in levels):
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
        st.info(f'{ACTIONS[action]} 요청 접수 · {as_of}. 화면 상단 「최근 작업」에서 진행·완료 상태를 확인하세요. '
                f'작업 ID: {job["id"]}')
    except (ValueError, TypeError, KeyError, OSError) as error:
        st.error(str(error))


def workbench(ops, key, snapshot, as_of=None):
    st.subheader('종목 탐색')
    rows = snapshot['rows']
    if not rows:
        st.info('아직 분석 자료가 없습니다. 「오늘의 운용」에서 자료를 준비하고 분석하세요.')
        return
    search = st.text_input('종목명·코드·산업 검색', key=f'search-{key}')
    cols = st.columns(3)
    industry = cols[0].selectbox('산업', ['전체'] + sorted({str(r.get('industry_name') or r.get('sector')) for r in rows if r.get('industry_name') or r.get('sector')}))
    choices = ['전체','통과','미통과','미확인·미적용']
    signal = cols[1].selectbox('최종 신호', choices)
    rs = cols[2].selectbox('RS 조건', choices)
    with st.expander('상세 필터'):
        a,b,c = st.columns(3)
        volume = a.selectbox('거래량 조건', choices)
        action = b.selectbox('산업 액션', choices)
        setup = c.selectbox('셋업 단계', ['전체'] + sorted({r.get('setup_phase') or '미확인·미적용' for r in rows}))
        d,e = st.columns(2)
        high52 = d.selectbox('52주 신고가',['전체','돌파','미돌파','자료 부족'])
        setup_condition = e.selectbox('셋업 조건',['전체','준비 충족','준비 미충족','셋업 돌파','셋업 무효화','미확인'])
        st.caption(f'거래량 배수 = 기준일 거래량 / 직전 20거래일 평균. 통과 기준 {snapshot["rules"]["volume_multiple"]}배. 필터는 AND 조건입니다.')
    filtered = filter_candidates(rows, search, industry, signal, rs, volume,
                                 snapshot['rules']['volume_multiple'], setup, action,
                                 high52, setup_condition)
    st.caption(f'전체 {len(rows)}종목 중 {len(filtered)}종목 · 필터는 계좌나 신호를 변경하지 않습니다.')
    stages = {name:[r for r in rows if candidate_stage(r) == name]
              for name in ['준비 후보','돌파선 접근','종가 돌파','고가 접촉·종가 하회']}
    for column,(name,members) in zip(st.columns(4),stages.items()):
        column.metric(name,len(members))
    with st.expander('단계별 종목과 판정 기준'):
        st.caption('종가 돌파·고가 접촉은 저장 가격의 셋업 저항선 또는 직전 52주 고가 기준입니다. 계좌 매수 신호와 별개입니다.')
        for name,members in stages.items():
            st.write(f'{name} · {len(members)}종목 · '+(', '.join(str(r.get('name') or r['symbol']) for r in members[:10]) or '해당 없음'))
    if not filtered:
        st.info('선택 조건에 맞는 종목이 없습니다.')
        return
    left, right = st.columns([1.6,1])
    fields = ['symbol','name','industry_name','reference_price','prior_52w_high','high52_breakout',
              'rs_percentile','mansfield_rs','multi_rs','rs_pass','setup_phase',
              'setup_ready','setup_trigger','volume_ratio','signal','signal_entry_path','quantity']
    frame = pd.DataFrame(filtered)
    frame['52주 거리'] = [high52_distance(r) for r in filtered]
    fields.insert(5,'52주 거리')
    with left:
        shown = frame[[f for f in fields if f in frame]].rename(columns=LABELS)
        event = st.dataframe(shown, hide_index=True, width='stretch',
                             on_select='rerun', selection_mode='single-row', key=f'candidates-{key}')
        index = event.selection.rows[0] if event.selection.rows else 0
        index = min(index, len(filtered)-1)
    with right:
        row = filtered[index]
        st.subheader(str(row.get('name') or row['symbol']))
        st.caption(row['symbol'])
        st.metric('저장된 종가', '—' if row.get('reference_price') is None else f'{row["reference_price"]:,.0f}원')
        try:
            prices = ops.prices(key, row['symbol'], as_of)
            if prices:
                chart = pd.DataFrame(prices)
                chart['date'] = pd.to_datetime(chart['date'])
                months = st.slider('차트 기간(개월)',1,15,6,key=f'chart-months-{key}')
                chart = chart[chart.date.ge(chart.date.max() - pd.DateOffset(months=months))]
                chart_type = st.radio('차트 종류',['캔들','종가 선'],horizontal=True,key=f'chart-type-{key}')
                tooltip = [alt.Tooltip('date:T',title='날짜')]
                tooltip += [alt.Tooltip(f'{field}:Q',title=label,format=',.2f')
                            for field,label in [('open','시가'),('high','고가'),('low','저가'),
                                                ('close','종가'),('volume','거래량'),('atr','ATR')]
                            if field in chart]
                base = alt.Chart(chart).encode(x=alt.X('date:T',title='거래일'),tooltip=tooltip)
                if chart_type == '캔들' and {'open','high','low','close'} <= set(chart):
                    wick = base.mark_rule().encode(y=alt.Y('low:Q',title='가격(원)'),y2='high:Q')
                    body = base.mark_bar(size=5).encode(y='open:Q',y2='close:Q',
                        color=alt.condition('datum.close >= datum.open',alt.value('#b33f38'),alt.value('#276eaa')))
                    st.altair_chart(wick + body,width='stretch')
                else:
                    st.altair_chart(base.mark_line(color='#255BC1').encode(y=alt.Y('close:Q',title='종가(원)')),width='stretch')
                if 'volume' in chart:
                    st.bar_chart(chart.set_index('date')[['volume']].rename(columns={'volume':'거래량'}),width='stretch')
                st.caption('선택일 이전 최대 15개월 완료 일봉 · 호버로 OHLCV·ATR 확인')
            else:
                st.info('가격 이력이 없습니다.')
        except (ValueError, OSError):
            st.info('저장된 가격 이력이 없습니다.')
        st.write({LABELS.get(k,k):row.get(k) for k in ['rs_pass','sector_action','setup_phase','volume_ratio','stop_price','quantity','planned_risk','allocation_reason']})
        if st.button('관측 목록에 추가', key='add-watch'):
            symbols = st.session_state.setdefault(f'watch-{key}', [])
            if row['symbol'] not in symbols:
                symbols.append(row['symbol'])
            st.session_state[f'watch-input-{key}'] = ', '.join(symbols)
            st.success('장중 관측 화면의 목록에 추가했습니다. 관측은 시작하지 않습니다.')
    with st.expander('산업별 후보 집계 · 전체 분석 자료 기준'):
        grouped = pd.DataFrame(rows)
        if 'sector' in grouped:
            st.dataframe(grouped.groupby('sector', dropna=False).agg(후보수=('symbol','size'),진입신호=('signal','sum')).reset_index(), hide_index=True)


def workflow(ops, key, snapshot, as_of, can_write):
    st.subheader('오늘의 운용')
    g = ops.guidance(key, as_of)
    st.caption('계좌 확인 → 산업 자료 준비 → 종가 처리 → 결과 확인 → 선택적 장중 관측')
    st.write('선택 계좌:', snapshot['workspace']['name'])
    if g['blocked']:
        st.warning(g['blocked'])
    if g['processed']:
        st.success('선택한 기준일은 이미 처리했습니다. 보유·체결 화면에서 결과를 확인하세요.')
    st.write('산업 자료:', '준비됨' if g['industry_ready'] else '준비 필요')
    disabled = not can_write or bool(g['blocked'])
    if st.button('① 산업 자료 갱신', disabled=disabled or not g['requires_industry'] or as_of != g['today']):
        submit(ops,key,'industry',as_of)
    if st.button('② 가격 갱신 + 종가 모의 처리', type='primary',
                 disabled=disabled or g['processed'] or snapshot['status'] == 'configuration_mismatch'
                 or snapshot['workspace'].get('mode') == 'recalculation'):
        submit(ops,key,'daily',as_of,price_source=st.session_state.get('price-source','naver'))
    if g['requires_industry'] and not g['industry_ready']:
        st.caption('누락 산업 자료는 종가 처리 중 준비합니다. 과거 날짜는 저장된 산업 구성으로 재구성할 수 있습니다.')
    st.info('첫 종가 처리는 시작 기준점과 다음 계획을 저장합니다. 이후 처리에서 모의 체결·현금·보유를 반영합니다. 증권사 주문은 없습니다.')
    with st.expander('저장 가격으로 실행 · 고급'):
        if st.button('후보만 재계산', disabled=disabled or not snapshot['files_ready']):
            submit(ops,key,'analyze',as_of)
        if st.button('저장 가격으로 모의 처리', disabled=disabled or g['processed'] or not snapshot['files_ready'] or not g['industry_ready']):
            submit(ops,key,'paper',as_of)


def market_candidates(ops, key, snapshot, can_write):
    st.subheader('날짜별 시장 후보')
    dates = ops.candidate_dates(key)
    available = sorted(set(dates['reference_dates'] + dates['signal_dates'] + dates['selection_dates']), reverse=True)
    preferred = next((day for group in (dates['market_analysis_dates'],dates['account_analysis_dates'],
                                        dates['selection_dates'],dates['reference_dates']) for day in group),available[0])
    selected = st.selectbox('보고 싶은 거래일', available,index=available.index(preferred),key=f'candidate-date-{key}',
                            help='그날의 저장된 종목 목록과 전략 조건 결과를 조회합니다. 목록이 없으면 ①에서 날짜별 자료 원천을 선택해 저장하세요.')
    st.caption(f'{selected}에 저장된 종목 목록·편입/제외·전략 조건 결과를 조회합니다. '
               '그날 목록이 없으면 ①에서 날짜별 자료 원천을 선택해 저장하세요.')
    selection_saved = selected in dates['selection_dates']
    analyzed = selected in dates['market_analysis_dates']
    st.write('선정 기록:', '있음' if selection_saved else '없음', '· 시장 조건 계산:', '완료' if analyzed else '미완료')

    st.markdown('#### ① 날짜별 종목 목록 저장')
    source = st.selectbox('후보군 자료 원천', ['kis','krx_open_api','pykrx'],
                          format_func=lambda value: {'kis':'KIS 현재 마스터','krx_open_api':'KRX Open API 날짜별','pykrx':'pykrx 날짜별'}[value])
    suggested = dates.get('suggested_capture_start') or dates.get('capture_window_start') or dates['reference_dates'][-1]
    start = None
    if source != 'kis':
        with st.expander('수집 시작일 직접 바꾸기 · 고급'):
            start = st.date_input('누락 자료 수집 시작일', value=date.fromisoformat(suggested),
                                  key=f'candidate-start-{key}',
                                  help='기본값은 자동으로 찾은 첫 누락일입니다. 이 날짜부터 최근 완료 거래일까지 저장되지 않은 목록을 채웁니다.')
        if not selection_saved and start.isoformat() > selected:
            st.warning(f'{selected}의 종목 목록이 없습니다. 현재 수집 시작일 {start}은 더 늦어 이 날짜를 채우지 않습니다.')
        if 'missing_selection_count' not in dates:
            st.warning('실행 중인 UI 서버의 후보 날짜 조회가 이전 버전입니다. 서버를 다시 시작하고 화면을 새로고침하면 누락 시작일을 자동으로 확인할 수 있습니다.')
        elif dates['missing_selection_count']:
            missing = [day for day in dates['missing_selection_dates'] if day >= start.isoformat()]
            st.info(f'{start}~{dates["capture_as_of"]}에서 저장되지 않은 거래일 목록을 채웁니다. '
                    f'자동 확인된 누락 {len(missing)}거래일 · 이미 저장된 날짜는 건너뜁니다.')
        else:
            st.success(f'{dates["capture_window_start"]}~{dates["capture_as_of"]}에 누락된 후보군이 없습니다.')
    can_capture = (can_write and (dates['capture_available'] if source == 'kis'
                   else source in dates['candidate_sources'] and start.isoformat() <= dates['capture_as_of']))
    if st.button('누락된 날짜의 종목 목록 저장', disabled=not can_capture):
        submit(ops,key,'candidate_universe',dates['capture_as_of'],candidate_source=source,
               **({'from_as_of':start.isoformat()} if start else {}))
    if source == 'kis':
        st.caption('KIS 현재 마스터는 오늘 완료 거래일만 저장합니다. 과거 누락일은 날짜별 원천을 선택하세요.')

    st.markdown('#### ② 선택한 거래일의 전략 조건 확인')
    st.caption('저장된 종목 목록에 선택 계좌의 RS·진입 조건을 계산해 보여줍니다. 이 계산만으로 모의계좌의 매수·보유 기록은 바뀌지 않습니다.')
    if st.button('선택한 거래일까지 전략 조건 계산', disabled=not can_write or not selection_saved):
        submit(ops,key,'market_analyze',selected)
    if selection_saved:
        selection = ops.candidate_selection(selected, include_excluded=True)
        st.caption(f'선정 종목 {len(selection["members"])}개 · 자료 원천 {selection["summary"]["source"]}')
        with st.expander('선정 종목과 편입·제외 기록'):
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

    st.markdown('#### ③ 이후 모의계좌의 매수 대상에 사용')
    st.caption('이 설정을 켜면 다음 미처리 거래일부터 날짜별 종목 목록을 새 매수 후보의 범위로 사용합니다. ②의 계산 결과를 그대로 매수하거나 과거 기록을 바꾸지는 않습니다.')
    policy = snapshot.get('candidate_policy')
    if policy:
        st.success(f'날짜별 후보군 사용 설정됨 · {policy["enabled_after"]} 이후 거래일부터 적용')
    elif snapshot['account_as_of']:
        st.caption('이전 계좌 기록은 유지하고, 설정한 날 이후 거래일부터 날짜별 후보군을 사용합니다.')
    can_apply = (can_write and not policy and bool(snapshot['account_as_of'])
                 and not snapshot['workspace'].get('builtin')
                 and snapshot['workspace'].get('mode') != 'recalculation'
                 and snapshot['status'] == 'ready')
    if st.button('이 계좌에 날짜별 후보군 적용', disabled=not can_apply):
        submit(ops,key,'candidate_policy',snapshot['account_as_of'])

    if analyzed or selected in dates['account_analysis_dates']:
        detail = ops.snapshot(key, selected)
        analysis = detail.get('analysis') or {}
        if analysis.get('industry_membership_reconstructed'):
            st.warning(f'{selected} 산업 구성은 당시 저장본이 없어 '
                       f'{analysis["industry_membership_observed_at"]} 저장본으로 재구성했습니다. '
                       '당시 실제 구성과 다를 수 있으며 모의계좌 매매 기록에는 반영되지 않습니다.')
        workbench(ops,key,detail,selected)
    elif selection_saved:
        st.info('선정 목록은 저장됐습니다. ② 시장 후보 계산 후 조건별 결과를 볼 수 있습니다.')


def holdings(snapshot):
    a,b,c = st.columns(3)
    a.metric('가용 현금',f'{snapshot["cash"]:,.0f}원')
    b.metric('보유 종목',len(snapshot['positions']))
    c.metric('다음 거래일 대기',len(snapshot['pending']))
    st.subheader('다음 거래일 진입·추가매수 계획')
    table(snapshot['pending'])
    st.subheader('보유 종목 · 모의계좌')
    table(snapshot['positions'])
    for name, label in [('orders','모의 주문·체결'),('trades','청산 손익'),('events','청산·운용 이벤트'),('campaign-risk','위험 검토'),('pyramiding-review','추가매수 검토')]:
        with st.expander(label):
            table(snapshot['logs'].get(name, []))


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
    symbol = st.text_input('변경 종목코드 검색',key=f'revision-symbol-{key}')
    page = st.number_input('변경 내역 페이지 · 100건씩',min_value=1,value=1,key=f'revision-page-{key}')
    changes = ops.revision_view(key,report['id'],offset=(page-1)*100,symbol=symbol)
    st.caption(f'조건에 맞는 변경 {changes["total"]}건')
    table(changes['rows'])
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
    table(snapshot['logs'].get('campaign-risk',[]))
    table(snapshot['logs'].get('pyramiding-review',[]))


def observation(ops, key, as_of, can_write):
    st.subheader('장중 관측')
    st.info('관측은 잠정 신호와 기록만 남깁니다. 현금·보유를 변경하지 않으며 종가 처리를 대신하지 않습니다.')
    data = ops.intraday(key)
    symbols = st.text_input('종목 코드 · 쉼표로 구분', value=', '.join(st.session_state.get(f'watch-{key}', [])), key=f'watch-input-{key}')
    watch_mode = st.selectbox('감시 범위',['industry','symbols'],
                              format_func=lambda value: '전체 산업 포함' if value == 'industry' else '입력 종목만')
    session = st.date_input('관측 거래일', value=date.fromisoformat(data['default_session']))
    chosen = [s.strip() for s in symbols.split(',') if s.strip()]
    basis = None
    try:
        basis = ops.intraday_service.plan_basis(key,session.isoformat())
        st.caption(f'관측 기준 종가 {basis["as_of"]} · 산업 자료 {"준비됨" if basis["industry_ready"] else "없음"}')
    except ValueError as error:
        st.warning(str(error))
    if st.button('구독 수 미리보기',disabled=not chosen or basis is None):
        try:
            preview = ops.intraday_service.subscription_preview(
                key,basis['as_of'],chosen,watch_mode,session.isoformat())
            st.info(f'예상 KIS 구독 {preview["count"]}건 · 산업 {len(preview["industries"])}개')
        except ValueError as error:
            st.warning(str(error))
    if st.button('자료 준비 및 관측 계획 생성', disabled=not can_write or not chosen or basis is None
                 or watch_mode == 'industry' and not basis['industry_ready']):
        submit(ops,key,'prepare_intraday',basis['as_of'], symbols=chosen,
               session=session.isoformat(),watch_mode=watch_mode)
    if basis and watch_mode == 'industry' and not basis['industry_ready']:
        st.caption('산업 자료가 없으면 입력 종목만 관측을 선택할 수 있습니다.')
    st.write('관측 상태:', data['control']['status'])
    plans = {p['id']:p for p in data['plans']}
    if plans:
        plan = st.selectbox('저장한 계획', list(plans), format_func=lambda p:f'{plans[p]["session"]} · {", ".join(plans[p]["targets"])}')
        seconds = st.number_input('관측 시간(초)', min_value=1,max_value=39600,value=39600)
        if st.button('관측 ON', disabled=not can_write):
            try:
                st.success(ops.start_watch(dict(workspace=key,plan_id=plan,seconds=seconds)))
            except (ValueError, OSError) as error:
                st.error(str(error))
    else:
        st.info('생성한 관측 계획이 없습니다.')
    if st.button('관측 OFF', disabled=not can_write):
        st.write(ops.stop_watch())
    if data['runs']:
        runs = {r['id']:r for r in data['runs']}
        run = st.selectbox('관측 기록', list(runs),
                           format_func=lambda run_id:f'{runs[run_id]["session"]} · {run_id}')
        summary = runs[run]['summary']
        st.write(f'연결 {summary.get("connection_state", "미확인")} · 요청 {summary.get("subscriptions_requested", "미확인")} / 승인 {summary.get("subscription_acks", "미확인")} · 체결 {summary.get("quote_ticks", "미확인")}')
        if summary.get('kis_error_code') or summary.get('rejected_symbol'):
            st.error(f'KIS 오류 {summary.get("kis_error_code", "미확인")} · 거절 종목 {summary.get("rejected_symbol", "미확인")}')
        quotes = summary.get('latest_target_quotes') or {}
        if quotes:
            st.write('최근 체결');table([{**quote,'symbol':symbol} for symbol,quote in quotes.items()])
        if summary.get('hourly_quotes'):
            st.write('시간대별 잠정 관측');table(summary['hourly_quotes'])
        st.write('판단 이력');table(runs[run]['events'])
        with st.expander('관측 원본 요약'):
            st.json(summary, expanded=False)
        for action in ['intraday_close','volume_import']:
            if st.button(ACTIONS[action], disabled=not can_write):
                submit(ops,key,action,as_of,run_id=run)
    if st.button('동시간 거래량 기준 생성', disabled=not can_write):
        submit(ops,key,'volume_profile',as_of)
    st.caption('거래량 누적은 관측 종료 후 16:10 이후 수행하세요. 기준 생성에는 최소 5개 유효 거래일이 필요합니다.')


def settings(ops, snapshot, can_write):
    st.subheader('적용 전략')
    st.json(dict(rules=snapshot['rules'],limits=snapshot['limits']),expanded=False)
    workspace = snapshot['workspace']
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
def job_status(ops, key):
    ops.refresh_jobs()
    with ops.lock:
        jobs = [dict(j) for j in ops.jobs.values() if j['workspace']==key][-10:]
    if not jobs:
        return
    job = jobs[-1]
    status = job['status']
    statuses = {'queued':'대기','running':'진행 중','completed':'완료','failed':'실패','interrupted':'중단'}
    label = ACTIONS.get(job['action'], job['action'])
    with st.container(border=True):
        st.markdown(f'#### 최근 작업 · {label} · {statuses.get(status,status)}')
        st.caption(f'대상 거래일 {job.get("as_of", "—")} · 작업 ID {job["id"]}')
        if status in {'queued','running'}:
            current,total = job.get('progress_current'),job.get('progress_total')
            if isinstance(current,int) and isinstance(total,int) and total > 0:
                st.progress(min(1.0,current/total),text=f'{job.get("progress","진행 중")} · {current}/{total}일 완료')
            else:
                st.info(job.get('progress') or '현재 단계를 처리 중입니다.')
        elif status == 'completed':
            result = job.get('result') or {}
            if result.get('status') == 'already_present':
                st.success('이미 저장된 결과가 있어 추가 계산 없이 완료됐습니다. 아래에서 결과를 확인하세요.')
            else:
                st.success('작업이 완료됐습니다. 아래 결과 화면에 반영됩니다.')
        elif job.get('error'):
            st.error(job['error'])
        if job.get('result') is not None:
            with st.expander('작업 상세 결과'):
                st.json(job['result'], expanded=False)
    if len(jobs) > 1:
        with st.expander(f'이전 작업 기록 {len(jobs)-1}건'):
            for old in reversed(jobs[:-1]):
                st.caption(f'{old.get("created_at", "")} · {ACTIONS.get(old["action"],old["action"])} · '
                           f'{statuses.get(old["status"],old["status"])} · {old["id"]}')
    seen_key = f'job-status-seen-{key}'
    previous = st.session_state.get(seen_key)
    st.session_state[seen_key] = (job['id'],status)
    if previous in {(job['id'],'queued'),(job['id'],'running')} and status in {'completed','failed'}:
        st.rerun(scope='app')


def main():
    st.set_page_config(page_title='TrendFollow · 종목 탐색',page_icon='📈',layout='wide')
    st.title('TrendFollow')
    st.caption('종목 탐색 작업대 · Paper Blue · 모의계좌 / 실제 주문 없음')
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
        st.error('실행 중인 UI 서버의 코드가 변경됐습니다. 새 화면과 이전 계산 객체가 섞이지 않도록 '
                 '작업 실행을 중지했습니다.')
        if active:
            st.warning(f'진행 중인 작업 {len(active)}건이 있습니다. 작업이 끝난 뒤 Streamlit 서버를 다시 시작하세요.')
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
        key = st.selectbox('계좌 선택',ids,format_func=names.get,key='account-picker')
        page = st.radio('화면',['종목 탐색','날짜별 시장 후보','오늘의 운용','보유·체결','장중 관측','정정·검증','전략·설정'],key='page-picker')
        recommended = ops.guidance(key)['recommended_as_of']
        as_of = st.date_input('종가 기준일',value=date.fromisoformat(recommended),key=f'date-{key}').isoformat()
        st.selectbox('일봉 수집 원천',['naver','pykrx'],
                     format_func=lambda value: {'naver':'네이버','pykrx':'pykrx'}[value],key='price-source')
        st.button('자료 다시 읽기')
        st.caption('Community Cloud의 로컬 저장은 영구 보존되지 않습니다. 앱 휴면 시 관측의 지속 실행도 보장되지 않습니다.')
    try:
        snapshot = ops.snapshot(key)
        st.caption(f'{names[key]} · 저장 기준일 {snapshot["as_of"] or "분석 전"} · 계좌 처리일 {snapshot["account_as_of"] or "미시작"}')
        job_status(ops,key)
        if snapshot['status']=='configuration_mismatch':
            st.error('계좌 설정과 저장 자료가 일치하지 않습니다. 기존 계좌를 보존하고 새 비교 계좌를 만드세요.')
        if not snapshot['files_ready']:
            st.warning('시세 또는 유니버스 자료가 준비되지 않았습니다. 저장된 실제 데이터 없이 가상 결과를 표시하지 않습니다.')
        if page=='종목 탐색': workbench(ops,key,snapshot)
        elif page=='날짜별 시장 후보': market_candidates(ops,key,snapshot,can_write)
        elif page=='오늘의 운용': workflow(ops,key,snapshot,as_of,can_write)
        elif page=='보유·체결': holdings(snapshot)
        elif page=='장중 관측': observation(ops,key,as_of,can_write)
        elif page=='정정·검증': recovery(ops,key,snapshot,as_of,can_write)
        else: settings(ops,snapshot,can_write)
    except (ValueError,KeyError,TypeError,OSError) as error:
        st.error(f'자료 표시 오류 ({type(error).__name__}): {error}')
        st.caption('저장된 계좌·후보 자료는 변경되지 않았습니다. 표시된 오류 내용을 확인하세요.')
