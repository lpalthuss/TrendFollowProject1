"""Paper Blue workbench. Rendering delegates all mutations to Operations."""
from datetime import date, datetime
import hmac
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from .ui_service import KST, Operations

ROOT = Path(__file__).resolve().parents[1]
LABELS = {'symbol':'종목코드','name':'종목명','industry_name':'산업','sector':'산업 코드',
          'reference_price':'종가','rs_percentile':'RS 순위','mansfield_rs':'Mansfield RS',
          'rs_pass':'RS 통과','sector_action':'산업 액션','setup_phase':'셋업',
          'volume_ratio':'거래량 배수','signal':'최종 신호','quantity':'수량',
          'stop_price':'손절가','planned_risk':'계획 위험','cash_required':'필요 현금',
          'allocation_reason':'배분 사유','last_price':'최근 가격','entry_price':'진입가',
          'target_3r':'3R','addition_count':'추가매수 횟수'}
ACTIONS = {'industry':'산업 자료 갱신','daily':'가격 갱신 + 종가 모의 처리',
           'analyze':'후보만 재계산','paper':'저장 가격으로 모의 처리',
           'prepare_intraday':'관측 계획 생성','intraday_close':'장중 기록과 종가 대조',
           'volume_import':'관측 거래량 누적','volume_profile':'동시간 거래량 기준 생성'}



@st.cache_resource
def operations(root: str):
    # Shared across reruns and sessions in this process, including its job lock.
    return Operations(root)


def setting(name, default=''):
    try:
        return st.secrets.get(name, os.environ.get(name, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return os.environ.get(name, default)


def authorize():
    password = str(setting('UI_PASSWORD'))
    if password:
        if not st.session_state.get('authorized'):
            entered = st.text_input('운용 비밀번호', type='password')
            if st.button('로그인'):
                if hmac.compare_digest(entered, password):
                    st.session_state.authorized = True
                    st.rerun()
                else:
                    st.error('비밀번호를 확인하세요.')
            st.stop()
        return True
    st.info('조회 모드입니다. 작업 실행은 UI_PASSWORD를 설정하고 로그인한 뒤 사용할 수 있습니다.')
    return False


def filter_candidates(rows, search='', industry='전체', signal='전체', rs='전체',
                      volume='전체', multiple=1.5, setup='전체', action='전체'):
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
        result.append(row)
    return result


def table(rows):
    if not rows:
        st.info('저장된 기록이 없습니다.')
    else:
        st.dataframe(pd.DataFrame(rows).rename(columns=LABELS), hide_index=True,
                     use_container_width=True)


def submit(ops, key, action, as_of, **extra):
    try:
        job = ops.submit(dict(workspace=key, action=action, as_of=as_of, **extra))
        st.success(f'{ACTIONS[action]} 접수 완료 · {job["id"]}')
    except (ValueError, TypeError, KeyError, OSError) as error:
        st.error(str(error))


def workbench(ops, key, snapshot):
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
        st.caption(f'거래량 배수 = 기준일 거래량 / 직전 20거래일 평균. 통과 기준 {snapshot["rules"]["volume_multiple"]}배. 필터는 AND 조건입니다.')
    filtered = filter_candidates(rows, search, industry, signal, rs, volume,
                                 snapshot['rules']['volume_multiple'], setup, action)
    st.caption(f'전체 {len(rows)}종목 중 {len(filtered)}종목 · 필터는 계좌나 신호를 변경하지 않습니다.')
    if not filtered:
        st.info('선택 조건에 맞는 종목이 없습니다.')
        return
    left, right = st.columns([1.6,1])
    fields = ['symbol','name','industry_name','reference_price','rs_percentile','mansfield_rs','rs_pass','setup_phase','volume_ratio','signal','quantity']
    frame = pd.DataFrame(filtered)
    with left:
        shown = frame[[f for f in fields if f in frame]].rename(columns=LABELS)
        event = st.dataframe(shown, hide_index=True, use_container_width=True,
                             on_select='rerun', selection_mode='single-row', key=f'candidates-{key}')
        index = event.selection.rows[0] if event.selection.rows else 0
        index = min(index, len(filtered)-1)
    with right:
        row = filtered[index]
        st.subheader(str(row.get('name') or row['symbol']))
        st.caption(row['symbol'])
        st.metric('저장된 종가', '—' if row.get('reference_price') is None else f'{row["reference_price"]:,.0f}원')
        try:
            prices = ops.prices(key, row['symbol'])
            if prices:
                chart = pd.DataFrame(prices)
                chart['date'] = pd.to_datetime(chart['date'])
                st.line_chart(chart.set_index('date')[['close']].rename(columns={'close':'종가'}), color='#255BC1')
                st.caption('최근 최대 180개 완료 일봉 · 원')
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
    if st.button('① 산업 자료 갱신', disabled=disabled):
        submit(ops,key,'industry',as_of)
    if st.button('② 가격 갱신 + 종가 모의 처리', type='primary',
                 disabled=disabled or g['processed'] or not g['industry_ready']):
        submit(ops,key,'daily',as_of)
    st.info('첫 종가 처리는 시작 기준점과 다음 계획을 저장합니다. 이후 처리에서 모의 체결·현금·보유를 반영합니다. 증권사 주문은 없습니다.')
    with st.expander('저장 가격으로 실행 · 고급'):
        if st.button('후보만 재계산', disabled=disabled or not snapshot['files_ready']):
            submit(ops,key,'analyze',as_of)
        if st.button('저장 가격으로 모의 처리', disabled=disabled or g['processed'] or not snapshot['files_ready'] or not g['industry_ready']):
            submit(ops,key,'paper',as_of)


def holdings(snapshot):
    st.subheader('다음 거래일 진입·추가매수 계획')
    table(snapshot['pending'])
    st.subheader('보유 종목 · 모의계좌')
    table(snapshot['positions'])
    for name, label in [('orders','모의 주문·체결'),('trades','청산 손익'),('events','청산·운용 이벤트'),('campaign-risk','위험 검토'),('pyramiding-review','추가매수 검토')]:
        with st.expander(label):
            table(snapshot['logs'].get(name, []))


def observation(ops, key, as_of, can_write):
    st.subheader('장중 관측')
    st.info('관측은 잠정 신호와 기록만 남깁니다. 현금·보유를 변경하지 않으며 종가 처리를 대신하지 않습니다.')
    symbols = st.text_input('종목 코드 · 쉼표로 구분', value=', '.join(st.session_state.get(f'watch-{key}', [])), key=f'watch-input-{key}')
    session = st.date_input('관측 거래일', value=datetime.now(KST).date())
    if st.button('관측 계획 생성', disabled=not can_write):
        submit(ops,key,'prepare_intraday',as_of, symbols=[s.strip() for s in symbols.split(',') if s.strip()], session=session.isoformat())
    data = ops.intraday(key)
    st.write('관측 상태:', data['control']['status'])
    plans = {p['id']:p for p in data['plans']}
    if plans:
        plan = st.selectbox('저장한 계획', list(plans), format_func=lambda p:f'{plans[p]["session"]} · {", ".join(plans[p]["targets"])}')
        seconds = st.number_input('관측 시간(초)', min_value=1,max_value=23400,value=300)
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
        run = st.selectbox('관측 기록', list(runs))
        st.json(runs[run]['summary'], expanded=False)
        table(runs[run]['events'])
        for action in ['intraday_close','volume_import']:
            if st.button(ACTIONS[action], disabled=not can_write):
                submit(ops,key,action,as_of,run_id=run)
    if st.button('동시간 거래량 기준 생성', disabled=not can_write):
        submit(ops,key,'volume_profile',as_of)
    st.caption('거래량 누적은 관측 종료 후 16:10 이후 수행하세요. 기준 생성에는 최소 5개 유효 거래일이 필요합니다.')


def settings(ops, snapshot, can_write):
    st.subheader('적용 전략')
    st.json(dict(rules=snapshot['rules'],limits=snapshot['limits']),expanded=False)
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
    with ops.lock:
        jobs = [dict(j) for j in ops.jobs.values() if j['workspace']==key][-10:]
    if jobs:
        st.subheader('작업 상태')
        for job in reversed(jobs):
            statuses = {'queued':'대기','running':'진행 중','completed':'완료','failed':'실패','interrupted':'중단'}
            with st.expander(f'{ACTIONS.get(job["action"],job["action"])} · {statuses.get(job["status"],job["status"])}',expanded=job['status'] in {'running','failed'}):
                st.caption(job['created_at'])
                if job.get('error'):
                    st.error(job['error'])
                if job.get('result') is not None:
                    st.json(job['result'],expanded=False)
        st.caption('작업 상태는 자동 갱신됩니다. 완료된 분석·계좌 자료는 「자료 다시 읽기」로 반영하세요.')


def main():
    st.set_page_config(page_title='TrendFollow · 종목 탐색',page_icon='📈',layout='wide')
    st.title('TrendFollow')
    st.caption('종목 탐색 작업대 · Paper Blue · 모의계좌 / 실제 주문 없음')
    # Streamlit secrets remain server-side; brokers already accept environment variables.
    for provider, names in [('KIS',['ENV','APP_KEY','APP_SECRET']),('KIWOOM',['ENV','APP_KEY','SECRET_KEY'])]:
        for suffix in names:
            name = f'{provider}_{suffix}'
            value = setting(name)
            if value:
                os.environ[name] = str(value)
    can_write = authorize()
    ops = operations(str(ROOT))
    rows = ops.workspaces()
    ids = [w['id'] for w in rows]
    names = {w['id']:w['name'] for w in rows}
    with st.sidebar:
        st.header('운용 계좌')
        preferred = st.session_state.pop('account', None)
        if preferred in ids:
            st.session_state['account-picker'] = preferred
        key = st.selectbox('계좌 선택',ids,format_func=names.get,key='account-picker')
        page = st.radio('화면',['종목 탐색','오늘의 운용','보유·체결','장중 관측','전략·설정'])
        recommended = ops.guidance(key)['recommended_as_of']
        as_of = st.date_input('종가 기준일',value=date.fromisoformat(recommended),key=f'date-{key}').isoformat()
        st.button('자료 다시 읽기')
        st.caption('Community Cloud의 로컬 저장은 영구 보존되지 않습니다. 앱 휴면 시 관측의 지속 실행도 보장되지 않습니다.')
    try:
        snapshot = ops.snapshot(key)
        st.caption(f'{names[key]} · 저장 기준일 {snapshot["as_of"] or "분석 전"} · 계좌 처리일 {snapshot["account_as_of"] or "미시작"}')
        if snapshot['status']=='configuration_mismatch':
            st.error('계좌 설정과 저장 자료가 일치하지 않습니다. 기존 계좌를 보존하고 새 비교 계좌를 만드세요.')
        if not snapshot['files_ready']:
            st.warning('시세 또는 유니버스 자료가 준비되지 않았습니다. 저장된 실제 데이터 없이 가상 결과를 표시하지 않습니다.')
        if page=='종목 탐색': workbench(ops,key,snapshot)
        elif page=='오늘의 운용': workflow(ops,key,snapshot,as_of,can_write)
        elif page=='보유·체결': holdings(snapshot)
        elif page=='장중 관측': observation(ops,key,as_of,can_write)
        else: settings(ops,snapshot,can_write)
        job_status(ops,key)
    except (ValueError,KeyError,TypeError,OSError):
        st.error('자료를 읽지 못했습니다. 설정·유니버스·저장 파일을 확인하세요.')
