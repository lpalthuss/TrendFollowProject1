import argparse
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from requests import RequestException

from .brokers import BrokerError, Credentials, QuoteClient
from .demo import generate
from .risk import Account, Limits, allocate
from .strategy import Rules, screen, validate_prices
from .units import replay
from .market import acquire_research, add_index_data, build_universe, download_master
from .quality import compare_sources
from .workflow import daily_run, paper_checkpoint, run_comparison
from .industries import industry_sync


def read_universe(path):
    return pd.read_csv(path, dtype={'symbol': str, 'benchmark': str}, keep_default_na=False)


def run_scan(prices_path, universe_path, account_path, asof, out, units=1, events=None, synthetic=False, config=None, rs_method=None, mansfield_benchmark=None, entry_method=None):
    from .research_store import require_published_research
    require_published_research(prices_path)
    prices = pd.read_csv(prices_path, dtype={'symbol': str})
    universe = read_universe(universe_path)
    account = Account(**json.loads(Path(account_path).read_text(encoding='utf-8-sig')))
    settings = json.loads(Path(config).read_text(encoding='utf-8-sig')) if config else {}
    if set(settings)-{'rules', 'limits'}:
        raise ValueError('config에는 rules,limits만 지정할 수 있습니다.')
    rules = Rules(**settings.get('rules', {}))
    if rs_method is not None:
        rules = replace(rules, rs_method=rs_method)
    if mansfield_benchmark is not None:
        rules = replace(rules, mansfield_benchmark=mansfield_benchmark)
    if entry_method is not None:
        rules=replace(rules,entry_method=entry_method)
    limit_settings = dict(settings.get('limits', {}))
    if events:
        event_list = json.loads(Path(events).read_text(encoding='utf-8-sig'))
        cutoff = pd.Timestamp(asof).tz_localize('Asia/Seoul') + pd.Timedelta(days=1)
        eligible_events = []
        for event in event_list:
            stamp = pd.Timestamp(event['timestamp'])
            if stamp.tz is None:
                raise ValueError('이벤트 timestamp에 +09:00 등 시간대가 필요합니다.')
            if stamp < cutoff:
                eligible_events.append(event)
        units = replay(eligible_events, limit_settings.get('max_units', 5))
    limit_settings['units'] = units
    limits = Limits(**limit_settings)
    result = allocate(screen(prices, universe, asof, rules), account, limits)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    result.to_csv(out/'candidates.csv', index=False, encoding='utf-8-sig')
    report = {'as_of': asof, 'generated_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
              'synthetic': synthetic, 'mode': 'research_only', 'units': units,
              'note': '종가 기반 다음 거래일 계획입니다. 체결가·수정주가·호가단위 재확인 필요. 주문 기능 없음.',
              'universe_size': len(universe), 'rs_scope': 'provided_universe_only',
              'rules': asdict(rules), 'limits': asdict(limits),
              'rs_method': rules.rs_method,
              'entry_method':rules.entry_method,
              'rs_candidate_count': int(result.signal_rs.sum()),
              'mansfield_candidate_count': int(result.signal_mansfield.sum()),
              'candidate_count': int(result.signal.sum()),
              'allocated_count': int((result.quantity > 0).sum()),
              'planned_cash': float(result.cash_required.sum()),
              'planned_risk': float(result.planned_risk.sum()),
              'results': json.loads(result.to_json(orient='records', force_ascii=False))}
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    label = '합성 데이터' if synthetic else '입력 데이터'
    print(f'{label} | 기준일 {asof} | 신호 {report["candidate_count"]} | 수량 배분 {report["allocated_count"]}')
    print(f'계획 투입액 {report["planned_cash"]:,.0f}원 | 계획 위험 {report["planned_risk"]:,.0f}원')
    print(f'결과: {(out/"candidates.csv").resolve()}')
    return result, report


def run_entry_comparison(args):
    root=Path(args.out)
    baseline,_=run_scan(args.prices,args.universe,args.account,args.as_of,root/'breakout',
                        config=args.config,entry_method='breakout')
    setup,_=run_scan(args.prices,args.universe,args.account,args.as_of,root/'setup',
                     config=args.config,entry_method='setup')
    result=setup.rename(columns={c:f'{c}_setup' for c in ['signal','quantity','cash_required',
                                                       'planned_risk','reasons','allocation_reason']})
    baseline_columns=['symbol','signal','quantity','cash_required','planned_risk','reasons','allocation_reason']
    other=baseline[baseline_columns].rename(columns={c:f'{c}_breakout' for c in baseline_columns if c!='symbol'})
    result=result.merge(other,on='symbol',validate='one_to_one').drop(columns=['entry_method','allocation_score'],errors='ignore')
    result.to_csv(root/'comparison.csv',index=False,encoding='utf-8-sig')
    summary={'as_of':args.as_of,'selected_entry_method':None,'universe_size':len(result),
             'breakout_candidates':int(result.signal_breakout.sum()),
             'setup_candidates':int(result.signal_setup.sum()),
             'setup_ready':int(result.setup_ready.eq(True).sum()) if 'setup_ready' in result else 0,
             'setup_triggers':int(result.setup_trigger.eq(True).sum()) if 'setup_trigger' in result else 0,
             'note':'두 방식의 수량은 같은 계좌를 각각 독립 적용한 계획이며 합산하지 않습니다.'}
    (root/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


def run_rs_comparison(args):
    out = Path(args.out)
    results = {}
    for method in ['percentile', 'mansfield']:
        results[method] = run_scan(args.prices, args.universe, args.account, args.as_of,
                                  out/method, config=args.config, rs_method=method,
                                  mansfield_benchmark=args.mansfield_benchmark)
    percentile, _ = results['percentile']
    mansfield, _ = results['mansfield']
    comparison = percentile.rename(columns={'quantity':'quantity_rs', 'cash_required':'cash_required_rs',
        'planned_risk':'planned_risk_rs','allocation_reason':'allocation_reason_rs'})
    extra = mansfield[['symbol','quantity','cash_required','planned_risk','allocation_reason']].rename(
        columns={c:f'{c}_mansfield' for c in ['quantity','cash_required','planned_risk','allocation_reason']})
    comparison = comparison.merge(extra,on='symbol',validate='one_to_one')
    # There is no single chosen strategy/signal in the side-by-side comparison.
    comparison = comparison.drop(columns=['signal','rs_method','allocation_score','reasons'],errors='ignore')
    comparison.to_csv(out/'comparison.csv',index=False,encoding='utf-8-sig')
    summary = {'as_of':args.as_of,'universe_size':len(comparison),'selected_method':None,
               'rs_candidates':int(comparison.signal_rs.sum()),
               'mansfield_candidates':int(comparison.signal_mansfield.sum()),
               'overlap':int((comparison.signal_rs & comparison.signal_mansfield).sum()),
               'mansfield_benchmarks':sorted(comparison.mansfield_benchmark.unique().tolist()),
               'note':'수량은 동일 계좌를 각 방식에 독립 적용한 가정입니다. 두 수량을 합쳐 사용하지 않습니다.'}
    (out/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description='추세추종 장마감 분석·위험 계획. 주문 기능 없음.')
    subs = parser.add_subparsers(dest='command', required=True)
    ui=subs.add_parser('ui',help='로컬 운용 웹 UI')
    ui.add_argument('--port',type=int,default=8765)
    demo = subs.add_parser('demo', help='API 없이 합성 데이터로 전체 흐름 실행')
    demo.add_argument('--out', default='outputs/demo')
    scan = subs.add_parser('scan', help='CSV 일봉을 분석')
    for key in ['prices', 'universe', 'account', 'as-of']:
        scan.add_argument(f'--{key}', required=True)
    scan.add_argument('--out', default='outputs/scan')
    scan.add_argument('--units', type=int, default=1)
    scan.add_argument('--events', help='검증된 거래 이벤트 JSON. 있으면 units 대신 재생 결과 적용')
    scan.add_argument('--config', help='rules,limits 설정 JSON')
    scan.add_argument('--rs-method', choices=['percentile','mansfield'], help='이번 분석에 적용할 RS 방식')
    scan.add_argument('--mansfield-benchmark',choices=['universe','market_index'],help='Mansfield 비교 기준')
    scan.add_argument('--entry-method',choices=['breakout','setup'],help='55일 돌파 또는 파동 셋업')
    compare_entry=subs.add_parser('compare-entry',help='기존 고가 돌파와 과정 기반 셋업을 비교')
    for key in ['prices','universe','account','as-of']:
        compare_entry.add_argument(f'--{key}',required=True)
    compare_entry.add_argument('--config')
    compare_entry.add_argument('--out',default='outputs/entry-comparison')
    stop_compare=subs.add_parser('compare-stop-limit',help='동일 조건에서 손절 폭 제한 ON/OFF 후보·수량 비교')
    for key in ['prices','universe','account','as-of']:
        stop_compare.add_argument(f'--{key}',required=True)
    stop_compare.add_argument('--config')
    stop_compare.add_argument('--out',default='outputs/stop-limit-comparison')
    initial_compare=subs.add_parser('compare-initial-stops',help='같은 모의 체결가 기준 고정·ATR·자동 전환 손절·수량 비교')
    for key in ['prices','universe','account','as-of']:
        initial_compare.add_argument(f'--{key}',required=True)
    initial_compare.add_argument('--config')
    initial_compare.add_argument('--out',default='outputs/initial-stop-comparison')
    compare_rs = subs.add_parser('compare-rs', help='RS 순위와 Mansfield를 동일 계좌 가정으로 각각 비교')
    for key in ['prices','universe','account','as-of']:
        compare_rs.add_argument(f'--{key}',required=True)
    compare_rs.add_argument('--config')
    compare_rs.add_argument('--out',default='outputs/rs-comparison')
    compare_rs.add_argument('--mansfield-benchmark',choices=['universe','market_index'])
    auth = subs.add_parser('check', help='키를 표시하지 않고 설정 확인. --connect 지정 시 토큰 발급')
    auth.add_argument('--provider', choices=['kis', 'kiwoom'], required=True)
    auth.add_argument('--env-file', default='.env')
    auth.add_argument('--connect', action='store_true')
    fetch = subs.add_parser('fetch', help='선택한 종목·벤치마크의 수정 일봉을 시세 API로 수집')
    fetch.add_argument('--provider', choices=['kis', 'kiwoom'], required=True)
    fetch.add_argument('--env-file', default='.env')
    fetch.add_argument('--universe', required=True)
    fetch.add_argument('--start', required=True)
    fetch.add_argument('--end', required=True)
    fetch.add_argument('--out', default='data/prices.csv')
    audit = subs.add_parser('audit', help='독립 원천과 가격·거래량 차이 검증')
    audit.add_argument('--primary', required=True)
    audit.add_argument('--reference', required=True)
    audit.add_argument('--out', default='outputs/audit')
    market = subs.add_parser('market-universe', help='공식 현재 종목 마스터로 연구 유니버스 생성')
    market.add_argument('--out', default='data/market')
    market.add_argument('--min-recent-turnover', type=float, default=1e9)
    market.add_argument('--refresh', action='store_true')
    industries=subs.add_parser('industry-sync',help='인더스트리 액션용 표준 산업 목록·넓은 동료 종목군 수집')
    industries.add_argument('--universe',default='data/market/universe.csv')
    industries.add_argument('--master-dir',default='data/market/master')
    industries.add_argument('--out',default='data/industries')
    industries.add_argument('--overrides',help='수정 근거·출처·적용일을 포함하는 CSV')
    industries.add_argument('--refresh',action='store_true')
    research = subs.add_parser('research-data', help='공개 차트로 유니버스 연구용 일봉 수집')
    research.add_argument('--universe', default='data/market/universe.csv')
    research.add_argument('--out', default='data/market/history')
    research.add_argument('--start', default='2020-01-01')
    research.add_argument('--end', required=True)
    research.add_argument('--refresh', action='store_true')
    research.add_argument('--include-indices',action='store_true',help='KOSPI·KOSDAQ 종합지수 일봉 포함')
    index_data=subs.add_parser('index-data',help='저장된 일봉에 KOSPI·KOSDAQ 종합지수 추가')
    index_data.add_argument('--prices',required=True)
    index_data.add_argument('--out',default='data/market/history/prices-with-indices.csv')
    index_data.add_argument('--cache-dir',default='data/market/indices')
    index_data.add_argument('--start',default='2020-01-01')
    index_data.add_argument('--end',required=True)
    backtest = subs.add_parser('backtest', help='고정 비중·점진적 베팅의 포트폴리오 연구 비교')
    backtest.add_argument('--prices', required=True)
    backtest.add_argument('--universe', required=True)
    backtest.add_argument('--start', default='2021-01-01')
    backtest.add_argument('--split', default='2024-01-01')
    backtest.add_argument('--end', required=True)
    backtest.add_argument('--capital', type=float, default=10000000)
    backtest.add_argument('--config')
    backtest.add_argument('--stop-limit',choices=['on','off'],help='다른 설정은 유지하고 손절 폭 제한만 변경')
    backtest.add_argument('--initial-stop-method',choices=['fixed','atr','hybrid'],help='모의 체결가 기준 초기 손절 비교')
    backtest.add_argument('--pyramiding',choices=['on','off'],help='같은 셋업 설정에서 추가매수 모의 체결 ON/OFF 비교')
    backtest.add_argument('--out', default='outputs/backtest')
    paper = subs.add_parser('paper', help='로컬 모의 계좌 장마감 체크포인트 (동일 날짜 중복 실행 방지)')
    paper.add_argument('--prices', required=True)
    paper.add_argument('--universe', required=True)
    paper.add_argument('--as-of', required=True)
    paper.add_argument('--capital', type=float, default=10000000)
    paper.add_argument('--config')
    paper.add_argument('--state', default='outputs/paper/state.json')
    daily = subs.add_parser('daily', help='장마감 데이터 갱신·증권사 후보 검증·로컬 모의 계좌 처리')
    daily.add_argument('--universe', default='data/market/universe.csv')
    daily.add_argument('--data-dir', default='data/market/history')
    daily.add_argument('--state', default='outputs/paper/state.json')
    daily.add_argument('--as-of')
    daily.add_argument('--capital', type=float, default=10000000)
    daily.add_argument('--config')
    daily.add_argument('--env-file', default='.env')
    daily.add_argument('--verify-provider', choices=['kis','kiwoom'], default='kiwoom')
    daily.add_argument('--price-source', choices=['naver','pykrx'], default='naver')
    intraday = subs.add_parser('intraday-prepare',help='장중 관측용 전일 셋업·산업 감시 계획 생성')
    for key in ['signals','prices','universe','session','config','out']:
        intraday.add_argument(f'--{key}',required=True)
    intraday.add_argument('--symbols',nargs='+',required=True)
    intraday.add_argument('--paper-state')
    intraday.add_argument('--watch-mode',choices=['industry','symbols'],default='industry')
    intraday.add_argument('--volume-profile',help='과거 동시간 누적 거래량 기준 JSON')
    volume_import=subs.add_parser('intraday-volume-import',help='종료된 관측 기록에서 분별 누적 거래량 이력 수집')
    volume_import.add_argument('--run-dirs',nargs='+',required=True)
    volume_import.add_argument('--out',default='data/intraday-volume/samples.csv')
    volume_profile=subs.add_parser('intraday-volume-profile',help='과거 동시간 누적 거래량 평균 기준 생성')
    for key in ['history','as-of','out']:
        volume_profile.add_argument(f'--{key}',required=True)
    volume_profile.add_argument('--lookback',type=int,default=20)
    volume_profile.add_argument('--min-sessions',type=int,default=5)
    volume_profile.add_argument('--multiple',type=float,default=1.5)
    volume_profile.add_argument('--max-sample-age-seconds',type=int,default=60)
    replay_parser = subs.add_parser('intraday-replay',help='저장된 시세로 장중 관측 신호 재생')
    for key in ['plan','ticks','out']:
        replay_parser.add_argument(f'--{key}',required=True)
    live_parser = subs.add_parser('intraday-live',help='KIS 실시간 시세 관측·기록 (주문 없음)')
    live_parser.add_argument('--plan')
    live_parser.add_argument('--out')
    live_parser.add_argument('--observation',choices=['on','off','status'],help='실제 연결 시작·중단·상태 확인. 생략 시 저장된 설정 적용')
    live_parser.add_argument('--control-file',default='outputs/intraday/control.json')
    live_parser.add_argument('--stop-wait-seconds',type=float,default=10,help='OFF 종료 확인 대기 초 (0~60)')
    live_parser.add_argument('--env-file',default='.env')
    live_parser.add_argument('--seconds',type=float,default=300)
    live_parser.add_argument('--evaluation-seconds',type=float,default=5,help='돌파·산업 판단 간격 (기본 5초)')
    live_parser.add_argument('--tick-storage',choices=['all','summary'],default='summary',help='전체 체결 또는 주기별 요약 저장')
    live_parser.add_argument('--idle-timeout',type=float,default=120,help='모든 메시지의 무응답 재접속 기준 초 (기본 120초)')
    live_parser.add_argument('--probe',action='store_true',help='휴장 중에도 접속·구독 응답만 확인')
    close_parser = subs.add_parser('intraday-close',help='장중 관측과 완료 종가의 돌파·산업 상태 대조')
    for key in ['plan','prices','out']:
        close_parser.add_argument(f'--{key}',required=True)
    close_parser.add_argument('--peer-returns',help='해당 거래일의 산업 동료 수익률 CSV')
    close_parser.add_argument('--run-dir',help='실제 관측 또는 재생 출력 폴더')
    close_parser.add_argument('--observation',choices=['on','off'],help='생략 시 기록과 저장된 관측 설정으로 결정')
    close_parser.add_argument('--control-file',default='outputs/intraday/control.json')
    args = parser.parse_args(argv)
    try:
        if args.command=='ui':
            from .ui_server import serve
            serve(Path.cwd(),args.port)
            return
        if args.command == 'compare-initial-stops':
            from .initial_stop import compare_initial_stops
            now=datetime.now(ZoneInfo('Asia/Seoul'));target=pd.Timestamp(args.as_of)
            if target.date()>now.date() or target.date()==now.date() and now.hour*60+now.minute<970:
                raise ValueError('완료된 장마감 거래일만 비교하세요. 당일은 16:10 이후 허용합니다.')
            print(json.dumps(compare_initial_stops(args.prices,args.universe,args.account,args.as_of,args.config,args.out),ensure_ascii=False))
        elif args.command == 'compare-stop-limit':
            from .stop_limit import compare_stop_limit
            now=datetime.now(ZoneInfo('Asia/Seoul'));target=pd.Timestamp(args.as_of)
            if target.date()>now.date() or target.date()==now.date() and now.hour*60+now.minute<970:
                raise ValueError('완료된 장마감 거래일만 비교하세요. 당일은 16:10 이후 허용합니다.')
            print(json.dumps(compare_stop_limit(args.prices,args.universe,args.account,args.as_of,args.config,args.out),ensure_ascii=False))
        elif args.command == 'intraday-volume-import':
            from .intraday_volume import import_runs
            print(json.dumps(import_runs(args.run_dirs,args.out),ensure_ascii=False))
        elif args.command == 'intraday-volume-profile':
            from .intraday_volume import build_reference
            print(json.dumps(build_reference(args.history,args.as_of,args.out,args.lookback,args.min_sessions,args.multiple,args.max_sample_age_seconds),ensure_ascii=False))
        elif args.command == 'intraday-close':
            from .intraday_close import close_comparison
            from .observation_control import read_control
            observation=args.observation
            if observation is None:
                has_run=args.run_dir is not None and (Path(args.run_dir)/'summary.json').exists()
                observation='on' if has_run or read_control(args.control_file)['enabled'] else 'off'
            print(json.dumps(close_comparison(args.plan,args.prices,args.peer_returns,args.out,
                args.run_dir,observation),ensure_ascii=False))
        elif args.command == 'intraday-prepare':
            from .intraday import prepare_plan
            plan=prepare_plan(args.signals,args.prices,args.universe,args.session,args.symbols,args.config,args.out,args.paper_state,args.volume_profile,watch_mode=args.watch_mode)
            print(json.dumps({'mode':plan['mode'],'targets':list(plan['targets']),
                'subscriptions':len(plan['subscriptions']),'session':plan['session'],'plan':args.out},ensure_ascii=False))
        elif args.command == 'intraday-replay':
            from .intraday import replay_ticks
            print(json.dumps(replay_ticks(args.plan,args.ticks,args.out),ensure_ascii=False))
        elif args.command == 'intraday-live':
            from .kis_stream import watch
            print(json.dumps(watch(args.plan,args.out,args.env_file,args.seconds,args.probe,
                args.evaluation_seconds,args.tick_storage,args.idle_timeout,args.control_file,
                args.observation,args.stop_wait_seconds),ensure_ascii=False))
        elif args.command == 'demo':
            out = Path(args.out)
            generate(out)
            run_scan(out/'prices.csv', out/'universe.csv', out/'account.json', '2026-11-06', out, synthetic=True)
        elif args.command == 'scan':
            now = datetime.now(ZoneInfo('Asia/Seoul'))
            target = pd.Timestamp(args.as_of)
            if target.date() > now.date() or (target.date() == now.date() and now.hour*60+now.minute < 16*60+10):
                raise ValueError('완료된 장마감 거래일만 지정하세요. 당일은 한국시간 16:10 이후 허용합니다.')
            run_scan(args.prices, args.universe, args.account, args.as_of, args.out, args.units, args.events,
                     config=args.config, rs_method=args.rs_method,mansfield_benchmark=args.mansfield_benchmark,
                     entry_method=args.entry_method)
        elif args.command=='compare-entry':
            run_entry_comparison(args)
        elif args.command == 'compare-rs':
            run_rs_comparison(args)
        elif args.command == 'check':
            credentials = Credentials.load(args.provider, Path(args.env_file))
            if args.connect:
                QuoteClient(credentials).authenticate()
            print(f'{args.provider} | {credentials.environment} | '+('토큰 발급 성공' if args.connect else '키·시크릿 설정됨 (서버 인증 미확인)'))
        elif args.command == 'fetch':
            credentials = Credentials.load(args.provider, Path(args.env_file))
            universe = read_universe(args.universe)
            symbols = sorted(set(universe.symbol) | set(universe.benchmark))
            if not symbols:
                raise ValueError('수집할 종목이 없습니다.')
            client = QuoteClient(credentials)
            frames = [client.daily(symbol, args.start, args.end) for symbol in symbols]
            prices = validate_prices(pd.concat(frames, ignore_index=True))
            out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
            # Commit the output only after all symbols have been collected successfully.
            temporary = out.with_suffix('.csv.tmp')
            prices.to_csv(temporary, index=False)
            temporary.replace(out)
            metadata = {'provider': args.provider, 'environment': credentials.environment,
                        'requested_start': args.start, 'requested_end': args.end,
                        'collected_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
                        'exchange': 'KRX', 'price_basis': 'provider_adjusted', 'symbols': symbols}
            out.with_suffix('.metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'시세 수집 완료: {len(prices)}행 → {out.resolve()}')
        elif args.command == 'audit':
            primary = pd.read_csv(args.primary, dtype={'symbol':str})
            reference = pd.read_csv(args.reference, dtype={'symbol':str})
            summary, detail = compare_sources(primary, reference)
            out = Path(args.out); out.mkdir(parents=True,exist_ok=True)
            detail.to_csv(out/'differences.csv',index=False,encoding='utf-8-sig')
            (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f'허용오차 초과 가격 행 {summary["price_mismatch_rows"]} | 거래량 행 {summary["volume_mismatch_rows"]}')
        elif args.command == 'market-universe':
            root = Path(args.out); master = root/'master'
            if args.refresh or not all((master/f'{m}.csv').exists() for m in ['kospi','kosdaq']):
                frames = download_master(master)
            else:
                frames = [pd.read_csv(master/f'{m}.csv',dtype=str,keep_default_na=False) for m in ['kospi','kosdaq']]
            universe, excluded = build_universe(frames,args.min_recent_turnover)
            universe.to_csv(root/'universe.csv',index=False,encoding='utf-8-sig')
            excluded.to_csv(root/'excluded.csv',index=False,encoding='utf-8-sig')
            (root/'universe-metadata.json').write_text(json.dumps({
                'snapshot_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
                'min_recent_turnover_proxy':args.min_recent_turnover,
                'eligible':len(universe),'excluded':len(excluded),
                'historical_point_in_time':False},ensure_ascii=False,indent=2),encoding='utf-8')
            print(f'연구 유니버스 {len(universe)}종목 | 제외 {len(excluded)}종목')
        elif args.command=='industry-sync':
            report=industry_sync(args.universe,args.master_dir,args.out,args.overrides,args.refresh)
            print(json.dumps(report,ensure_ascii=False))
        elif args.command == 'research-data':
            manifest = acquire_research(args.universe,args.out,args.start,args.end,args.refresh,args.include_indices)
            if manifest['errors']:
                raise ValueError(f'일부 연구 데이터 수집에 실패했습니다. 기존 파일은 보존됩니다. 기록: {manifest["attempt_report"]}')
            print(f'수집 완료: {manifest["collected_symbols"]}종목, {manifest["rows"]}행')
        elif args.command == 'index-data':
            metadata=add_index_data(args.prices,args.out,args.cache_dir,args.start,args.end)
            print(f'KOSPI·KOSDAQ 지수 {metadata["index_rows"]}행 추가 → {Path(args.out).resolve()}')
        elif args.command == 'backtest':
            summary = run_comparison(args.prices,args.universe,args.start,args.split,args.end,
                                     args.out,args.capital,args.config,
                                     None if args.stop_limit is None else args.stop_limit=='on',args.initial_stop_method,
                                     None if args.pyramiding is None else args.pyramiding=='on')
            print(f'백테스트 완료: {Path(args.out).resolve()} | 탐색 연구: 생존·유동성 선택 편향 존재')
        elif args.command == 'paper':
            now=datetime.now(ZoneInfo('Asia/Seoul')); target=pd.Timestamp(args.as_of)
            if target.date()>now.date() or (target.date()==now.date() and now.hour*60+now.minute<970):
                raise ValueError('모의 계좌는 완료된 장마감 거래일만 처리합니다.')
            report=paper_checkpoint(args.prices,args.universe,args.as_of,args.state,args.capital,args.config)
            print(json.dumps(report,ensure_ascii=False))
        elif args.command == 'daily':
            report=daily_run(args.universe,args.data_dir,args.state,args.as_of,args.capital,
                             args.config,args.env_file,args.verify_provider,price_source=args.price_source)
            print(json.dumps(report,ensure_ascii=False))
    except (ValueError, TypeError, KeyError, OSError, BrokerError,RequestException) as exc:
        if isinstance(exc,RequestException):
            parser.exit(1,'외부 산업·시세 원천에 접속하지 못했습니다. 기존 파일은 확인 후 사용하세요.\n')
        # Known exceptions contain only controlled messages or local input diagnostics.
        if isinstance(exc, (TypeError, KeyError, OSError)):
            parser.exit(1, '입력 파일·필드·출력 경로를 확인하세요.\n')
        parser.exit(1, str(exc)+'\n')
