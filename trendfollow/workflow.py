"""Research backtest comparisons and local, resumable paper-trading checkpoints."""
from dataclasses import asdict,replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .backtest import clean_research_prices, features, json_records, metrics, simulate
from .risk import Limits
from .strategy import Rules
from .relative_strength import decision_rule_settings, mansfield_benchmark_symbol


LIMITATIONS = [
    'Current surviving, recent-liquidity-selected universe: survivorship and selection bias.',
    'Vendor-adjusted OHLC; executable raw prices, corporate-action cash flows and queues not reconstructed.',
    'Current industry memberships reused historically; no point-in-time sector history.',
    'Research comparison only; not evidence that the strategy is profitable in live trading.',
]


def read_inputs(prices_path, universe_path, end, config_path=None):
    prices=pd.read_csv(prices_path,dtype={'symbol':str})
    prices=prices[prices.date<=end]
    universe=pd.read_csv(universe_path,dtype={'symbol':str,'benchmark':str},keep_default_na=False)
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig')) if config_path else {}
    rules=Rules(**config.get('rules',{})); rules.validate()
    limits=Limits(**config.get('limits',{})); limits.validate()
    return prices,universe,rules,limits


def save_json(path, payload):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def paper_input_digest(prices,universe,rules):
    symbols=set(universe.symbol.astype(str))|set(universe.benchmark.astype(str))
    if rules.rs_method=='mansfield':
        symbols|={mansfield_benchmark_symbol(item,rules) for item in universe.to_dict('records')}
    if rules.market_units_enabled:
        from .market_units import market_symbol
        symbols|={market_symbol(item,rules) for item in universe.to_dict('records')}
    # Extra observation-only index rows must not invalidate a percentile paper account.
    digest=hashlib.sha256(prices[prices.symbol.isin(symbols)].to_csv(index=False).encode())
    if rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import load_peer_history,PEER_DECISION_COLUMNS
        peers=load_peer_history(universe)
        peers=peers[peers.date<=pd.Timestamp(prices.date.max())].sort_values(['date','symbol'])
        digest.update(b'\nindustry-peer-history\n'+peers[PEER_DECISION_COLUMNS].to_csv(index=False).encode())
    return digest.hexdigest()


def run_comparison(prices_path, universe_path, start, split, end, output_dir,
                   capital=10_000_000, config_path=None,stop_limit_enabled=None):
    if not start < split <= end:
        raise ValueError('start < split <= end 순서여야 합니다.')
    prices,universe,rules,limits=read_inputs(prices_path,universe_path,end,config_path)
    if stop_limit_enabled is not None:rules=replace(rules,stop_limit_enabled=stop_limit_enabled)
    if rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import require_peer_sessions
        require_peer_sessions(prices,universe,start,end)
    prepared,rejected=features(prices,universe,rules)
    root=Path(output_dir); root.mkdir(parents=True,exist_ok=True)
    rejected.to_csv(root/'rejected-bars.csv',index=False)
    clean,_=clean_research_prices(prices)
    summary={'source':str(prices_path),'universe_size':len(universe),
        'rules':asdict(rules),'limits':asdict(limits),'limitations':LIMITATIONS,
        'split_policy':'two independent simulations with warmup data, no parameter optimization',
        'results':{},'rejected_bar_count':len(rejected)}
    periods={'train':(start,str((pd.Timestamp(split)-pd.Timedelta(days=1)).date())), 'holdout':(split,end)}
    for period,(first,last) in periods.items():
        for mode in ['fixed','progressive']:
            key=f'{period}-{mode}'
            print(f'백테스트 {key} | {first}~{last}',flush=True)
            curve,trades,orders,events,state=simulate(clean,prepared,universe,first,last,capital,mode,limits)
            target=root/key; target.mkdir(exist_ok=True)
            for name,frame in [('equity',curve),('trades',trades),('orders',orders),('events',events)]:
                frame.to_csv(target/f'{name}.csv',index=False,encoding='utf-8-sig')
            summary['results'][key]=metrics(curve,trades,capital)
            save_json(target/'final-state.json',state)
    benchmarks={}
    for symbol in sorted(set(universe.benchmark)):
        b=clean[(clean.symbol==symbol)&(clean.date>=pd.Timestamp(split))&(clean.date<=pd.Timestamp(end))]
        if not b.empty:
            benchmarks[symbol]={'period':'holdout','first_date':str(b.date.iloc[0].date()),
                'last_date':str(b.date.iloc[-1].date()),
                'buy_hold_return_before_costs':float(b.close.iloc[-1]/b.open.iloc[0]-1)}
    summary['benchmarks']=benchmarks
    save_json(root/'summary.json',summary)
    return summary


def paper_checkpoint(prices_path, universe_path, as_of, state_path, capital=10_000_000,config_path=None,
                     approved_symbols=None):
    """First run seeds a close-of-day plan. Later runs simulate only subsequent sessions."""
    prices,universe,rules,limits=read_inputs(prices_path,universe_path,as_of,config_path)
    if rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import require_peer_sessions
        require_peer_sessions(prices,universe,as_of,as_of)
    prepared,rejected=features(prices,universe,rules)
    asof=pd.Timestamp(as_of)
    if approved_symbols is not None:
        prepared.loc[(prepared.date==asof)&~prepared.symbol.isin(approved_symbols),'signal']=False
    benchmark_rows=prices[(prices.symbol.isin(set(universe.benchmark)))&(prices.date==as_of)]
    if benchmark_rows.symbol.nunique()!=universe.benchmark.nunique():
        raise ValueError('기준일의 벤치마크가 누락되었습니다. 휴장·수집 상태를 확인하세요.')
    state_file=Path(state_path)
    from .industries import decision_universe_records
    fingerprint=hashlib.sha256(json.dumps({'universe':decision_universe_records(universe),
        'rules':decision_rule_settings(rules),'limits':asdict(limits),'capital':capital},sort_keys=True).encode()).hexdigest()
    history_fingerprint=paper_input_digest(prices,universe,rules)
    todays=prepared[prepared.date==asof]
    if state_file.exists():
        old=json.loads(state_file.read_text(encoding='utf-8'))
        if old['config_fingerprint']!=fingerprint:
            raise ValueError('모의 계좌 설정·유니버스가 변경되었습니다. 새 상태 파일을 사용하세요.')
        if old['last_date']>as_of:
            raise ValueError('모의 계좌를 과거 날짜로 되돌릴 수 없습니다.')
        if old['last_date']==as_of:
            if old['input_fingerprint']!=history_fingerprint:
                raise ValueError('같은 날짜 데이터가 변경되었습니다. 기존 상태는 보존됩니다.')
            return {'status':'already_processed','as_of':as_of,'state':str(state_file),
                    'rs_method':rules.rs_method,'rs_candidates':int(todays.signal_rs.sum()),
                    'mansfield_candidates':int(todays.signal_mansfield.sum()),
                    'mansfield_ready_count':int(todays.mansfield_rs.notna().sum())}
        prior_history=prices[prices.date<=old['last_date']]
        prior_digest=paper_input_digest(prior_history,universe,rules)
        if prior_digest!=old['input_fingerprint']:
            raise ValueError('이미 처리한 과거 일봉이 수정되었습니다. 가격 조정·수집 정정을 확인하세요. 모의 계좌는 보존됩니다.')
        start=str((pd.Timestamp(old['last_date'])+pd.Timedelta(days=1)).date())
        if rules.entry_method=='setup' and 'industry_peer_history' in universe:
            from .industries import require_peer_sessions
            require_peer_sessions(prices,universe,start,as_of)
        curve,trades,orders,events,state=simulate(prices,prepared,universe,start,as_of,capital,
                                               'progressive',limits,initial_state=old)
    else:
        selected=todays[todays.signal].sort_values(['allocation_score','symbol'],ascending=[False,True])
        state={'cash':capital,'positions':{},'units':1,'pending':json_records(selected),
               'last_date':as_of,'initial_cash':capital,'mode':'progressive'}
        curve=pd.DataFrame([dict(date=as_of,equity=capital,cash=capital,positions=0,units=1,stale_positions=0)])
        trades,orders,events=pd.DataFrame(),pd.DataFrame(),pd.DataFrame()
    root=state_file.parent/as_of
    root.mkdir(parents=True,exist_ok=True)
    for name,frame in [('signals',todays),('equity',curve),('trades',trades),('orders',orders),('events',events)]:
        frame.to_csv(root/f'{name}.csv',index=False,encoding='utf-8-sig')
    state.update(config_fingerprint=fingerprint,input_fingerprint=history_fingerprint,
                 generated_at=datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),source=str(prices_path),
                 limitations=LIMITATIONS,execution='local simulation; no broker order API')
    save_json(root/'state.json',state)
    save_json(state_file,state)
    result={'status':'processed','as_of':as_of,'cash':state['cash'],'positions':len(state['positions']),
            'units':state['units'],'pending_signals':len(state['pending']),'rejected_bar_count':len(rejected),
            'state':str(state_file),'mode':'paper_only','rs_method':rules.rs_method,
            'rs_candidates':int(todays.signal_rs.sum()),'mansfield_candidates':int(todays.signal_mansfield.sum()),
            'mansfield_ready_count':int(todays.mansfield_rs.notna().sum())}
    save_json(root/'report.json',result)
    return result


def daily_run(universe_path, data_dir, state_path, as_of=None, capital=10_000_000,
              config_path=None, env_file='.env', verify_provider='kiwoom'):
    """One manual/scheduled local run. A skipped checkpoint is not silently backfilled."""
    from .brokers import Credentials, QuoteClient
    from .market import NaverResearchSource, acquire_research
    from .quality import compare_sources
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    as_of=as_of or str(now.date())
    day=pd.Timestamp(as_of)
    if day.date()>now.date():
        raise ValueError('미래 날짜는 처리할 수 없습니다.')
    if day.weekday()>=5:
        return {'status':'skipped_weekend','as_of':as_of}
    if day.date()==now.date() and now.hour*60+now.minute<970:
        raise ValueError('장마감 확정 데이터 확인을 위해 한국시간 16:10 이후 실행하세요.')
    universe=pd.read_csv(universe_path,dtype={'symbol':str,'benchmark':str})
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig')) if config_path else {}
    active_rules=Rules(**config.get('rules',{}));active_rules.validate()
    include_indices=(active_rules.mansfield_benchmark=='market_index' or
                     active_rules.market_units_enabled and active_rules.market_units_benchmark=='market_index')
    source=NaverResearchSource(Path(data_dir)/'raw')
    benchmark_symbols=sorted(set(universe.benchmark))
    probe_symbols=set(benchmark_symbols)
    if active_rules.mansfield_benchmark=='market_index':
        probe_symbols|={mansfield_benchmark_symbol(item,active_rules) for item in universe.to_dict('records')}
    if active_rules.market_units_enabled:
        from .market_units import market_symbol
        probe_symbols|={market_symbol(item,active_rules) for item in universe.to_dict('records')}
    probes=[source.daily(symbol,as_of,as_of) for symbol in sorted(probe_symbols)]
    if any(f.empty for f in probes):
        return {'status':'skipped_no_confirmed_session','as_of':as_of}
    if active_rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import load_peer_history
        peers=load_peer_history(universe)
        if not peers.date.eq(day).any():
            raise ValueError('해당 거래일의 산업 동료 수익률 스냅샷이 없습니다. 산업 데이터를 갱신한 후 실행하세요.')
    # Fast idempotent path: an already processed date must not issue fresh broker tokens.
    state_file=Path(state_path)
    if state_file.exists():
        state=json.loads(state_file.read_text(encoding='utf-8'))
        if state['last_date']==as_of:
            return paper_checkpoint(Path(data_dir)/'prices.csv',universe_path,as_of,state_path,capital,config_path)
        if state['last_date']>as_of:
            raise ValueError('처리 완료일보다 과거의 daily 실행은 허용하지 않습니다.')
        calendar=source.daily(benchmark_symbols[0],state['last_date'],as_of)
        if int((calendar.date>state['last_date']).sum())>1:
            raise ValueError('미처리 거래일이 여러 개입니다. --as-of로 각 거래일을 순서대로 실행하세요.')
    manifest=acquire_research(universe_path,data_dir,'2020-01-01',as_of,include_market_indices=include_indices)
    if manifest['errors']:
        raise ValueError('일부 연구 데이터 수집에 실패했습니다. 모의 계좌는 변경하지 않습니다.')
    prices_path=Path(data_dir)/'prices.csv'
    prices,u,rules,limits=read_inputs(prices_path,universe_path,as_of,config_path)
    prepared,_=features(prices,u,rules)
    candidates=prepared[(prepared.date==day)&prepared.signal]
    approved=[]; audits={}
    if not candidates.empty:
        client=QuoteClient(Credentials.load(verify_provider,Path(env_file)))
        start=str((day-pd.Timedelta(days=300)).date())
        required=sorted(set(candidates.symbol)|set(candidates.benchmark))
        symbol_ok={}
        for symbol in required:
            primary=client.daily(symbol,start,as_of)
            reference=prices[(prices.symbol==symbol)&(prices.date>=str(primary.date.min()))&
                             (prices.date<=str(primary.date.max()))]
            audit,_=compare_sources(primary,reference)
            audits[symbol]=audit
            symbol_ok[symbol]=audit['price_mismatch_rows']==0 and audit['volume_mismatch_rows']==0
        approved=[row['symbol'] for row in candidates.to_dict('records')
                  if symbol_ok[row['symbol']] and symbol_ok[row['benchmark']]]
    audit_root=state_file.parent/as_of
    save_json(audit_root/'broker-audit.json',{'provider':verify_provider,'approved_symbols':approved,
        'mansfield_benchmark_policy':active_rules.mansfield_benchmark,
        'index_source':'NAVER public index chart; not cross-checked with stock quote API' if include_indices else None,
        'rejected_symbols':sorted(set(candidates.symbol)-set(approved)),'details':audits})
    report=paper_checkpoint(prices_path,universe_path,as_of,state_path,capital,config_path,approved)
    report['approved_signals']=len(approved)
    return report
