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
from .config import Rules, strategy_config
from .relative_strength import decision_rule_settings, mansfield_benchmark_symbol


LIMITATIONS = [
    'Current surviving, recent-liquidity-selected universe: survivorship and selection bias.',
    'Vendor-adjusted OHLC; executable raw prices, corporate-action cash flows and queues not reconstructed.',
    'Current industry memberships reused historically; no point-in-time sector history.',
    'Research comparison only; not evidence that the strategy is profitable in live trading.',
]


def read_inputs(prices_path, universe_path, end, config_path=None):
    from .research_store import require_published_research
    require_published_research(prices_path)
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


def save_pyramiding_logs(target,events):
    from .pyramiding_execution import execution_events,LOT_COLUMNS,RISK_COLUMNS
    for name,kind,columns in [('campaign-lots','campaign_lot_opened',LOT_COLUMNS),
            ('campaign-risk','campaign_risk',RISK_COLUMNS)]:
        execution_events(events,kind,columns).to_csv(target/f'{name}.csv',index=False,encoding='utf-8-sig')


def paper_input_digest(prices,universe,rules,peer_history=None):
    symbols=set(universe.symbol.astype(str))|set(universe.benchmark.astype(str))
    if rules.rs_method=='mansfield':
        symbols|={mansfield_benchmark_symbol(item,rules) for item in universe.to_dict('records')}
    if rules.market_units_enabled:
        from .market_units import market_symbol
        symbols|={market_symbol(item,rules) for item in universe.to_dict('records')}
    # Extra observation-only index rows must not invalidate a percentile paper account.
    digest=hashlib.sha256()
    class DigestWriter:
        def write(self, text):
            digest.update(text.encode())
            return len(text)
    writer = DigestWriter()
    prices.loc[prices.symbol.isin(symbols)].to_csv(writer, index=False)
    if rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import load_peer_history,PEER_DECISION_COLUMNS
        peers=load_peer_history(universe) if peer_history is None else peer_history
        peers=peers[peers.date<=pd.Timestamp(prices.date.max())].sort_values(['date','symbol'])
        digest.update(b'\nindustry-peer-history\n')
        peers[PEER_DECISION_COLUMNS].to_csv(writer, index=False)
    return digest.hexdigest()


def run_comparison(prices_path, universe_path, start, split, end, output_dir,
                   capital=10_000_000, config_path=None,stop_limit_enabled=None,initial_stop_method=None,pyramiding_enabled=None):
    if not start < split <= end:
        raise ValueError('start < split <= end 순서여야 합니다.')
    prices,universe,rules,limits=read_inputs(prices_path,universe_path,end,config_path)
    if stop_limit_enabled is not None:rules=replace(rules,stop_limit_enabled=stop_limit_enabled)
    if initial_stop_method is not None:rules=replace(rules,initial_stop_method=initial_stop_method,initial_stop_reference='entry_fill')
    if pyramiding_enabled is not None:
        rules=replace(rules,pyramiding_enabled=pyramiding_enabled,
            pyramiding_review_enabled=rules.pyramiding_review_enabled or pyramiding_enabled)
    rules.validate()
    if rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import require_peer_sessions
        require_peer_sessions(prices,universe,start,end)
    prepared,rejected=features(prices,universe,rules)
    if pyramiding_enabled is not None:
        # Isolate additional buys: both comparison arms use identical initial-buy sizing.
        prepared['campaign_risk_enabled']=True
        prepared['pyramiding_enabled']=rules.pyramiding_enabled
        prepared['pyramiding_max_additions']=rules.pyramiding_max_additions
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
            if rules.pyramiding_review_enabled:
                from .pyramiding import review_events
                reviews=review_events(events)
                reviews.to_csv(target/'pyramiding-review.csv',index=False,encoding='utf-8-sig')
            summary['results'][key]=metrics(curve,trades,capital)
            if rules.pyramiding_enabled:
                save_pyramiding_logs(target,events)
                additions=orders[orders.order_intent.eq('add')] if 'order_intent' in orders else pd.DataFrame()
                summary['results'][key].update(filled_additions=int(additions.status.eq('filled').sum()) if not additions.empty else 0,
                    rejected_additions=int(additions.status.eq('rejected').sum()) if not additions.empty else 0)
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
                     approved_symbols=None,revision_id=None,fingerprint_universe_path=None):
    from .observation_control import control_lock
    with control_lock(Path(state_path)):
        result = _paper_checkpoint(prices_path,universe_path,as_of,state_path,capital,config_path,approved_symbols,revision_id,fingerprint_universe_path)
    from .r2_backup import checkpoint_saved
    checkpoint_saved()
    return result


def _paper_checkpoint(prices_path, universe_path, as_of, state_path, capital=10_000_000,config_path=None,
                     approved_symbols=None,revision_id=None,fingerprint_universe_path=None):
    """First run seeds a close-of-day plan. Later runs simulate only subsequent sessions."""
    base_config = config_path
    state_file = Path(state_path)
    old = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else None
    config_path = strategy_config(state_path, base_config, as_of)
    prices,universe,rules,limits=read_inputs(prices_path,universe_path,as_of,config_path)
    if rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import require_peer_sessions
        require_peer_sessions(prices,universe,as_of,as_of)
    since = pd.Timestamp(as_of)
    if old and old['last_date'] < as_of:
        since = pd.Timestamp(old['last_date']) + pd.Timedelta(days=1)
    prepared,rejected=features(prices,universe,rules,since=since)
    asof=pd.Timestamp(as_of)
    if approved_symbols is not None:
        prepared.loc[(prepared.date==asof)&~prepared.symbol.isin(approved_symbols),'signal']=False
    if 'candidate_active' in prepared:
        prepared.loc[(prepared.date==asof)&~prepared.candidate_active.eq(True),'signal']=False
    benchmark_rows=prices[(prices.symbol.isin(set(universe.benchmark)))&(prices.date==as_of)]
    if benchmark_rows.symbol.nunique()!=universe.benchmark.nunique():
        raise ValueError('기준일의 벤치마크가 누락되었습니다. 휴장·수집 상태를 확인하세요.')
    state_file=Path(state_path)
    from .industries import decision_universe_records
    fingerprint_universe=(pd.read_csv(fingerprint_universe_path,dtype={'symbol':str,'benchmark':str},keep_default_na=False)
        if fingerprint_universe_path else universe)
    def config_fingerprint(rules, limits):
        return hashlib.sha256(json.dumps({'universe':decision_universe_records(fingerprint_universe),
            'rules':decision_rule_settings(rules),'limits':asdict(limits),'capital':capital},sort_keys=True).encode()).hexdigest()
    fingerprint = config_fingerprint(rules, limits)
    history_fingerprint=paper_input_digest(prices,universe,rules)
    applied_revision=None
    todays=prepared[prepared.date==asof]
    if old:
        prior_config = strategy_config(state_path, base_config, old['last_date'])
        previous_fingerprint = fingerprint
        if prior_config != config_path:
            saved = json.loads(Path(prior_config).read_text(encoding='utf-8-sig')) if prior_config else {}
            prior_rules = Rules(**saved.get('rules', {}))
            prior_limits = Limits(**saved.get('limits', {}))
            expected = replace(prior_rules, pyramiding_review_enabled=rules.pyramiding_review_enabled,
                               pyramiding_enabled=rules.pyramiding_enabled,
                               initial_stop_reference='entry_fill' if rules.pyramiding_enabled else prior_rules.initial_stop_reference)
            if expected != rules or prior_limits != limits:
                raise ValueError('예약한 추가매수 설정 외의 변경이 있습니다.')
            previous_fingerprint = config_fingerprint(prior_rules, prior_limits)
        if old['config_fingerprint']!=previous_fingerprint:
            raise ValueError('모의 계좌 설정·유니버스가 변경되었습니다. 새 상태 파일을 사용하세요.')
        if revision_id is not None:
            from .revisions import validated_approval
            applied_revision=validated_approval(state_path,revision_id,prices_path,universe_path,prior_config,as_of)
            if history_fingerprint!=applied_revision['new_snapshot']:
                raise ValueError('계산 입력이 승인한 정정 스냅샷과 다릅니다. 변경 대조를 다시 실행하세요.')
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
        prior_universe=(pd.read_csv(old['decision_universe_path'],dtype={'symbol':str,'benchmark':str},keep_default_na=False)
            if fingerprint_universe_path and old.get('decision_universe_path') else fingerprint_universe if fingerprint_universe_path else universe)
        prior_digest=paper_input_digest(prior_history,prior_universe,rules)
        if prior_digest!=old['input_fingerprint']:
            if fingerprint_universe_path:
                from .revisions import load_snapshot,snapshot_path
                saved_path=snapshot_path(state_path,old.get('input_snapshot',old['input_fingerprint']))
                if not (saved_path/'manifest.json').is_file():
                    raise ValueError('이전 처리일의 보존 입력 스냅샷이 없어 계좌 처리를 보류했습니다.')
                saved_prices,saved_peers,_=load_snapshot(saved_path)
                sort=lambda frame:frame.sort_values(['symbol','date']).reset_index(drop=True)
                saved_digest=paper_input_digest(sort(saved_prices),prior_universe,rules,peer_history=saved_peers)
                current_digest=paper_input_digest(sort(prior_history),prior_universe,rules)
                if saved_digest!=current_digest:
                    raise ValueError('이전 처리일의 가격·산업 입력이 바뀌었습니다. 날짜별 후보군 계좌는 과거 입력 정정 검토 후 계속 처리하세요. 계좌는 보존되었습니다.')
                prior_digest=old['input_fingerprint']
        if prior_digest!=old['input_fingerprint']:
            from .revisions import report_revision,validated_approval,approve_revision,DataRevisionError
            if revision_id is None:
                report=report_revision(prices_path,universe_path,prior_config,state_path,as_of)
                if not report['automatic_eligible']:raise DataRevisionError(report)
                approve_revision(state_path,report['id'],prices_path,universe_path,prior_config,as_of,automatic=True)
                revision_id=report['id']
            if applied_revision is None:applied_revision=validated_approval(state_path,revision_id,prices_path,universe_path,prior_config,as_of)
            if history_fingerprint!=applied_revision['new_snapshot']:
                raise ValueError('계산 입력이 승인한 정정 스냅샷과 다릅니다. 변경 대조를 다시 실행하세요.')
        start=str((pd.Timestamp(old['last_date'])+pd.Timedelta(days=1)).date())
        if rules.entry_method=='setup' and 'industry_peer_history' in universe:
            from .industries import require_peer_sessions
            require_peer_sessions(prices,universe,start,as_of)
        if rules.pyramiding_review_enabled:
            missing = {code: pos for code, pos in old['positions'].items()
                       if pos.get('success') and not pos.get('success_3r_date')}
            if missing:
                for path in sorted(state_file.parent.glob('????-??-??/events.csv'), reverse=True):
                    if path.parent.name > old['last_date']:
                        continue
                    from .operations.common import read_csv
                    events = read_csv(path)
                    if 'kind' not in events or 'symbol' not in events:
                        continue
                    for event in events.loc[events.kind.eq('success_3r')].to_dict('records'):
                        code, date = event['symbol'], str(event['date'])[:10]
                        if code in missing and missing[code]['entry_date'] <= date <= old['last_date']:
                            missing.pop(code)['success_3r_date'] = date
                    if not missing:
                        break
        curve,trades,orders,events,state=simulate(prices,prepared,universe,start,as_of,capital,
                                               'progressive',limits,initial_state=old,close_rules=rules)
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
    if rules.pyramiding_review_enabled:
        from .pyramiding import review_events
        reviews=review_events(events)
        reviews.to_csv(root/'pyramiding-review.csv',index=False,encoding='utf-8-sig')
    if rules.pyramiding_enabled:save_pyramiding_logs(root,events)
    state['pyramiding_settings'] = dict(pyramiding_enabled=rules.pyramiding_enabled,
                                        pyramiding_review_enabled=rules.pyramiding_review_enabled)
    state.update(config_fingerprint=fingerprint,input_fingerprint=history_fingerprint,
                 generated_at=datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),source=str(prices_path),
                 limitations=LIMITATIONS,execution='local simulation; no broker order API')
    if fingerprint_universe_path:
        state['decision_universe_path']=str(Path(universe_path).resolve())
    from .revisions import snapshot_inputs,write_json,report_path
    state['input_snapshot']=snapshot_inputs(state_path,prices,universe,rules)
    if state_file.exists():state['revision_lineage']=old.get('revision_lineage',[])
    if applied_revision:
        state['revision_lineage']=[*state.get('revision_lineage',[]),dict(report_id=revision_id,
            prior_date=old['last_date'],applied_on=as_of,old_digest=old['input_fingerprint'],new_prior_digest=applied_revision['new_prior_digest'])]
    save_json(root/'state.json',state)
    save_json(state_file,state)
    if applied_revision:write_json(report_path(state_path,revision_id)/'applied.json',dict(as_of=as_of,state_fingerprint=history_fingerprint))
    result={'status':'processed','as_of':as_of,'cash':state['cash'],'positions':len(state['positions']),
            'units':state['units'],'pending_signals':len(state['pending']),'rejected_bar_count':len(rejected),
            'state':str(state_file),'mode':'paper_only','rs_method':rules.rs_method,
            'rs_candidates':int(todays.signal_rs.sum()),'mansfield_candidates':int(todays.signal_mansfield.sum()),
            'mansfield_ready_count':int(todays.mansfield_rs.notna().sum())}
    if rules.pyramiding_review_enabled:
        result.update(pyramiding_review_candidates=int(reviews.eligible_for_risk_review.sum()),
            pyramiding_execution='local_simulation' if rules.pyramiding_enabled else 'review_only; combined risk management pending')
    if rules.pyramiding_enabled:
        additions=orders[orders.order_intent.eq('add')] if 'order_intent' in orders else pd.DataFrame()
        result.update(pending_additions=sum(row.get('order_intent')=='add' for row in state['pending']),
            filled_additions=int(additions.status.eq('filled').sum()) if not additions.empty else 0,
            rejected_additions=int(additions.status.eq('rejected').sum()) if not additions.empty else 0)
    save_json(root/'report.json',result)
    return result


def daily_run(universe_path, data_dir, state_path, as_of=None, capital=10_000_000,
              config_path=None, env_file='.env', verify_provider='kiwoom', catch_up=False,
              reconstruct_missing_peers=False, progress=None, use_existing_prices=False,
              price_source='naver', universe_by_session=None,
              fingerprint_universe_path=None, acquisition_universe_path=None):
    """Process one close, or every missing close when the UI requests catch-up."""
    from .market import NaverResearchSource, acquire_research
    if price_source not in {'naver','pykrx'}:
        raise ValueError('가격 자료 원천은 네이버 또는 pykrx여야 합니다.')
    if price_source == 'pykrx':
        from .krx_bulk import configure_krx_credentials
        configure_krx_credentials(env_file)
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
    if price_source == 'pykrx':
        from .krx_sessions import calendar
        if not calendar(day.year).is_session(day.date()):
            return {'status':'skipped_no_confirmed_session','as_of':as_of}
    else:
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
    # Fast idempotent path: an already processed date must not issue fresh broker tokens.
    state_file=Path(state_path)
    state=None
    sessions=[as_of]
    if state_file.exists():
        state=json.loads(state_file.read_text(encoding='utf-8'))
        if state['last_date']==as_of:
            decision_path=state.get('decision_universe_path',universe_path) if fingerprint_universe_path else universe_path
            return paper_checkpoint(Path(data_dir)/'prices.csv',decision_path,as_of,state_path,capital,config_path,
                                    fingerprint_universe_path=fingerprint_universe_path)
        if state['last_date']>as_of:
            raise ValueError('처리 완료일보다 과거의 daily 실행은 허용하지 않습니다.')
        if price_source == 'pykrx':
            from exchange_calendars import get_calendar
            sessions=[d.date().isoformat() for d in get_calendar('XKRX',start=state['last_date'],end=as_of).sessions_in_range(state['last_date'],as_of)
                      if d.date().isoformat()>state['last_date']]
        else:
            previous_days=source.daily(benchmark_symbols[0],state['last_date'],as_of)
            sessions=sorted(d for d in previous_days.date if d>state['last_date'])
        if len(sessions)>1 and not catch_up:
            raise ValueError('미처리 거래일이 여러 개입니다. --as-of로 각 거래일을 순서대로 실행하세요.')
        if not sessions or sessions[-1]!=as_of:
            return {'status':'skipped_no_confirmed_session','as_of':as_of}
    reconstructed=[]
    manifest=None
    if active_rules.entry_method=='setup' and 'industry_peer_history' in universe:
        from .industries import load_peer_history
        peers=load_peer_history(universe)
        missing=sorted(set(sessions)-set(peers.date.dt.strftime('%Y-%m-%d')))
        if missing:
            if reconstruct_missing_peers and catch_up:
                from .industries import reconstruct_peer_sessions
                reconstructed=reconstruct_peer_sessions(universe,sessions,Path(data_dir)/'raw',progress,price_source)
            else:
                raise ValueError('산업 동료 수익률 기록이 없는 거래일: '+', '.join(missing)+'. 과거 산업 자료를 현재 스냅샷으로 대체하지 않습니다.')
    if not use_existing_prices:
        if progress:progress('선택 날짜까지 가격 자료 수집 중')
        if price_source == 'pykrx':
            from .krx_bulk import acquire_missing_sessions
            manifest=acquire_missing_sessions(acquisition_universe_path or universe_path,data_dir,sessions,include_market_indices=include_indices,progress=progress)
        else:
            start=state['last_date'] if state else '2020-01-01'
            manifest=acquire_research(acquisition_universe_path or universe_path,data_dir,start,as_of,include_market_indices=include_indices,required_session=as_of,progress=progress,append_sessions=sessions)
        if manifest['errors']:
            raise ValueError('일부 연구 데이터 수집에 실패했습니다. 모의 계좌는 변경하지 않습니다.')
    prices_path=Path(data_dir)/'prices.csv'
    completed=[]
    for session in sessions:
        print(f'종가 처리 {session} ({len(completed)+1}/{len(sessions)})',flush=True)
        if progress:progress(f'{session} 종가 처리 중',len(completed)+1,len(sessions))
        try:
            decision_path=(universe_by_session or {}).get(session,universe_path)
            prices,u,rules,limits=read_inputs(prices_path,decision_path,session,strategy_config(state_path,config_path,session))
            print(f'{session} 종가 지표 계산 시작 · {len(u)}종목', flush=True)
            prepared,rejected=features(prices,u,rules,since=pd.Timestamp(session))
            candidates=prepared[(prepared.date==pd.Timestamp(session))&prepared.signal]
            if 'candidate_active' in candidates:
                candidates=candidates[candidates.candidate_active.eq(True)]
            print(f'{session} 종가 후보 검증 시작 · {len(candidates)}종목', flush=True)
            approved=verify_paper_candidates(candidates,prices,rules,session,state_path,env_file,verify_provider,include_indices)
            del prices, prepared, candidates, rejected
            checkpoint_options = (
                {"fingerprint_universe_path": fingerprint_universe_path}
                if fingerprint_universe_path is not None else {}
            )
            print(f'{session} 계좌 처리·기록 저장 시작', flush=True)
            report=paper_checkpoint(prices_path,decision_path,session,state_path,capital,config_path,approved,
                                    **checkpoint_options)
            print(f'{session} 계좌 처리·기록 저장 완료', flush=True)
            report['approved_signals']=len(approved)
        except (ValueError,FileNotFoundError) as exc:
            if len(sessions)>1:
                error=ValueError(f'{session} 처리 실패 (완료: {len(completed)}/{len(sessions)}거래일): {exc}')
                if hasattr(exc,'report'):error.report=exc.report
                raise error from exc
            raise
        completed.append(session)
    report['processed_dates']=completed
    report['reconstructed_industry_dates']=reconstructed
    report['price_source']=price_source
    if manifest and manifest.get('source_comparison'):
        report['source_comparison']=manifest['source_comparison']
    return report


def verify_paper_candidates(candidates,prices,active_rules,as_of,state_path,env_file,verify_provider,include_indices):
    from .brokers import Credentials,QuoteClient
    from .quality import compare_sources
    day=pd.Timestamp(as_of)
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
    audit_root=Path(state_path).parent/as_of
    save_json(audit_root/'broker-audit.json',{'provider':verify_provider,'approved_symbols':approved,
        'mansfield_benchmark_policy':active_rules.mansfield_benchmark,
        'index_source':'NAVER public index chart; not cross-checked with stock quote API' if include_indices else None,
        'rejected_symbols':sorted(set(candidates.symbol)-set(approved)),'details':audits})
    return approved


def continue_revised_checkpoint(prices_path,universe_path,as_of,state_path,capital,config_path,revision_id,env_file,verify_provider='kiwoom'):
    """Use reviewed stored inputs, preserve prior decisions, and re-audit today's candidates."""
    from .revisions import validated_approval
    validated_approval(state_path,revision_id,prices_path,universe_path,config_path,as_of)
    prices,u,rules,_=read_inputs(prices_path,universe_path,as_of,strategy_config(state_path,config_path,as_of))
    prepared,rejected=features(prices,u,rules,since=pd.Timestamp(as_of))
    candidates=prepared[prepared.date.eq(pd.Timestamp(as_of))&prepared.signal]
    approved=verify_paper_candidates(candidates,prices,rules,as_of,state_path,env_file,verify_provider,
        rules.mansfield_benchmark=='market_index' or rules.market_units_enabled and rules.market_units_benchmark=='market_index')
    del prices, prepared, candidates, rejected
    result=paper_checkpoint(prices_path,universe_path,as_of,state_path,capital,config_path,approved,revision_id=revision_id)
    result.update(revision_id=revision_id,approved_signals=len(approved))
    return result
