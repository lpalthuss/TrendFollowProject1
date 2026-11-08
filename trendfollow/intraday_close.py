"""Compare recorded intraday observations against completed-session prices."""
from datetime import datetime, time
import hashlib
import json
from pathlib import Path

import pandas as pd

from .backtest import clean_research_prices
from .intraday import IntradayMonitor, KST, stamp, validate_plan
from .intraday_sampling import restore_snapshot


def truth_seen(events, field):
    values = [e[field] for e in events if e.get(field) is not None]
    return any(values) if values else None


def outcome(observed, close, status):
    if status != 'recorded': return 'skipped_'+status
    if observed is None: return 'unknown_intraday'
    if close is None: return 'unknown_close'
    if observed: return 'held_at_close' if close else 'not_held_at_close'
    return 'close_only' if close else 'not_seen_in_recorded_window'


def load_observations(plan, run_dir, observation):
    empty = {'status':'disabled' if observation=='off' else 'missing_recordings',
             'events':[],'quotes':{s:[] for s in plan['targets']},'resets':0,
             'connection_gaps':None,'source_status':None,'sources':[]}
    if observation == 'off' or run_dir is None: return empty
    root = Path(run_dir)
    if not (root/'summary.json').exists(): return empty
    metadata = json.loads((root/'summary.json').read_text(encoding='utf-8'))
    empty['source_status'] = metadata.get('status')
    empty['sources'] = [str(root/'summary.json')]
    if metadata.get('status')=='stopped_by_off' and metadata.get('quote_ticks')==0:
        empty['status']='disabled';return empty
    if metadata.get('mode') == 'connection_probe':
        empty['status'] = 'probe_only'; return empty
    if not (root/'plan.json').exists():
        raise ValueError('관측 실행에 사용한 plan.json 사본이 필요합니다.')
    recorded_plan = json.loads((root/'plan.json').read_text(encoding='utf-8'))
    if recorded_plan != plan:
        raise ValueError('대조 계획과 관측 실행의 감시 계획이 다릅니다.')
    ticks_path = root/'ticks.jsonl'
    if not ticks_path.exists() and metadata.get('mode') == 'replay_observation_only':
        ticks_path = Path(metadata.get('ticks_source',''))
    if not ticks_path.is_file() or not (root/'events.jsonl').exists(): return empty
    result = dict(empty,status='recorded')
    result['sources'] = [str(root/'summary.json'),str(root/'plan.json'),str(ticks_path),str(root/'events.jsonl')]
    events = []
    with (root/'events.jsonl').open(encoding='utf-8') as source:
        previous = None
        for line in source:
            event = json.loads(line)
            at = stamp(event['timestamp'])
            if str(at.date()) != plan['session']:
                raise ValueError('관측 신호의 거래일이 감시일과 다릅니다.')
            if previous is not None and at < previous:
                raise ValueError('관측 신호 기록은 시간순이어야 합니다.')
            previous = at
            if event.get('symbol') in plan['targets']: events.append(event)
    result['events'] = events
    monitor = IntradayMonitor(plan)
    keys = {}
    with ticks_path.open(encoding='utf-8') as source:
        previous = None
        for line in source:
            record = json.loads(line)
            at = stamp(record['received_at'])
            if str(at.date()) != plan['session']:
                raise ValueError('시세 기록의 수신 거래일이 감시일과 다릅니다.')
            if previous is not None and at < previous:
                raise ValueError('시세 기록은 수신 시간순이어야 합니다.')
            previous = at
            kind = record.get('kind')
            if kind == 'connection_reset':
                result['resets'] += 1
                monitor.reset_connection()
            elif kind == 'quote_snapshot': restore_snapshot(monitor,record)
            elif 'tick' in record: monitor.update(record['tick'],record['received_at'],evaluate=False)
            for symbol in plan['targets']:
                quote = monitor.quotes.get(symbol)
                if quote is None: continue
                key = (quote['timestamp'],quote['price'],quote['cumulative_volume'])
                if key != keys.get(symbol):
                    result['quotes'][symbol].append(stamp(quote['timestamp']))
                    keys[symbol] = key
    connection_file = root/'connection-events.jsonl'
    if connection_file.exists():
        result['sources'].append(str(connection_file))
        with connection_file.open(encoding='utf-8') as source:
            result['connection_gaps'] = sum(json.loads(line).get('kind')=='connection_gap' for line in source)
    if not any(result['quotes'].values()): result['status'] = 'no_target_quotes'
    return result


def close_comparison(plan_path, prices_path, peer_returns_path, output,
                     run_dir=None, observation='on', now=None):
    if observation not in {'on','off'}: raise ValueError('관측 상태는 on 또는 off여야 합니다.')
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    validate_plan(plan)
    now = now or datetime.now(KST)
    day = datetime.strptime(plan['session'],'%Y-%m-%d').date()
    if not plan.get('synthetic') and (day > now.date() or day == now.date() and now.hour*60+now.minute < 970):
        raise ValueError('종가 대조는 완료된 거래일만, 당일은 한국시간 16:10 이후 실행하세요.')
    raw = pd.read_csv(prices_path,dtype={'symbol':str})
    raw = raw[raw.date <= plan['session']]
    prices, rejected = clean_research_prices(raw)
    frames = {str(s):f.set_index('date') for s,f in prices.groupby('symbol')}
    asof, session = pd.Timestamp(plan['as_of']), pd.Timestamp(plan['session'])
    benchmarks = {m['benchmark'] for m in plan['members'].values()}
    for benchmark in benchmarks:
        f = frames.get(benchmark)
        if f is not None and ((f.index > asof)&(f.index < session)).any():
            raise ValueError('계획 기준일 이후 미반영 거래일이 있습니다. 최신 기준일의 계획으로 대조하세요.')
    peers = pd.DataFrame()
    if peer_returns_path:
        peers = pd.read_csv(peer_returns_path,dtype={'symbol':str},keep_default_na=False)
        if not {'date','symbol','day_return','quote_eligible'}.issubset(peers):
            raise ValueError('종가 산업 수익률 파일의 필드가 부족합니다.')
        peers = peers[peers.date == plan['session']].copy()
        if peers.symbol.duplicated().any(): raise ValueError('종가 산업 수익률이 중복되었습니다.')
        for column in ['day_return','quote_eligible']:
            peers[column] = pd.to_numeric(peers[column],errors='coerce')
        peers = peers.set_index('symbol')
    observed = load_observations(plan,run_dir,observation)
    rows = []
    def bar(symbol, date):
        f = frames.get(symbol)
        return f.loc[date] if f is not None and date in f.index else None
    for symbol,target in plan['targets'].items():
        events = [e for e in observed['events'] if e['symbol']==symbol]
        quotes = sorted(set(observed['quotes'][symbol]))
        status = observed['status']
        if status == 'recorded' and not quotes: status = 'no_target_quotes'
        seen = {name:truth_seen(events,name) if status=='recorded' else None
                for name in ['breakout_present','industry_action','entry_watch','holding_stop_breached']}
        previous, today = bar(symbol,asof),bar(symbol,session)
        basis_valid = (abs(float(previous.close)/target['previous_close']-1) <= .001) if previous is not None else None
        close = float(today.close) if today is not None else None
        close_status = ('missing_close_bar' if today is None else 'missing_baseline_bar' if previous is None
                        else 'price_basis_mismatch' if basis_valid is False else 'available')
        close_breakout = (bool(close > target['resistance'])
                          if close is not None and basis_valid is True and target['resistance'] is not None else None)
        healthy = (bool(float(today.low) >= target['healthy_floor'])
                   if today is not None and basis_valid is True and target['healthy_floor'] is not None else None)
        stop = (bool(float(today.low) <= target['holding_stop'])
                if today is not None and basis_valid is True and target['holding_stop'] is not None else None)
        excess, positive = [],0
        changed = 0
        for peer in target['peer_symbols']:
            if peers.empty or peer not in peers.index: continue
            row = peers.loc[peer]
            benchmark = plan['members'][peer]['benchmark']
            b0,b1 = bar(benchmark,asof),bar(benchmark,session)
            if row.quote_eligible != 1 or pd.isna(row.day_return) or not float('-inf') < row.day_return < float('inf') or b0 is None or b1 is None: continue
            excess.append(float(row.day_return)-(float(b1.close)/float(b0.close)-1))
            positive += row.day_return > 0
            changed += 'sector' in row and row['sector'] != target['sector']
        count, expected = len(excess),len(target['peer_symbols'])
        coverage = count/expected if expected else 0.
        breadth = positive/count if count else None
        mean_excess = sum(excess)/count if count else None
        industry = (bool(breadth >= plan['sector_min_breadth'] and mean_excess > 0)
                    if count >= plan['sector_min_peers'] and coverage >= plan['min_peer_coverage'] else None)
        start = datetime.combine(day,time(9),KST); end = datetime.combine(day,time(15,30),KST)
        unknown_gap_seconds = sum(max(0.,(b-a).total_seconds()-plan['max_quote_age_seconds']) for a,b in zip(quotes,quotes[1:]))
        partial = (not quotes or (quotes[0]-start).total_seconds() > plan['max_quote_age_seconds'] or
                   (end-quotes[-1]).total_seconds() > plan['max_quote_age_seconds'] or unknown_gap_seconds > 0 or
                   observed['resets'] > 1 or bool(observed['connection_gaps']) or observed['source_status'] in {'failed','interrupted','stopped_by_off'})
        if status != 'recorded': partial = None
        rows.append({'symbol':symbol,'name':target.get('name',symbol),'session':plan['session'],
            'synthetic':bool(plan.get('synthetic')),'observation_status':status,
            'observation_run_status':observed['source_status'],'close_data_status':close_status,
            'intraday_breakout_seen':seen['breakout_present'],'intraday_industry_action_seen':seen['industry_action'],
            'intraday_entry_watch_seen':seen['entry_watch'],'intraday_stop_contact_seen':seen['holding_stop_breached'],
            'first_quote_at':quotes[0].isoformat() if quotes else None,'last_quote_at':quotes[-1].isoformat() if quotes else None,
            'recorded_quote_samples':len(quotes),'partial_session':partial,
            'quote_gap_seconds':unknown_gap_seconds,'connection_gap_count':observed['connection_gaps'],
            'connection_reset_records':observed['resets'],'close':close,'resistance':target['resistance'],
            'close_price_basis_valid':basis_valid,'close_above_resistance':close_breakout,'close_setup_healthy':healthy,
            'close_stop_contact':stop,'close_industry_action':industry,
            'close_peer_count':count,'expected_peer_count':expected,'close_peer_coverage':coverage,
            'close_peer_up_fraction':breadth,'close_peer_excess_return':mean_excess,
            'peer_classification_changes':int(changed),'industry_scope':'frozen_plan_members',
            'breakout_outcome':outcome(seen['breakout_present'],close_breakout,status),
            'industry_outcome':outcome(seen['industry_action'],industry,status),
            'close_stop_contact_without_observation':bool(stop and seen['holding_stop_breached'] is not True) if stop is not None and status=='recorded' else None})
    result = pd.DataFrame(rows)
    observed_breakouts = sum(r['intraday_breakout_seen'] is not None for r in rows)
    comparable_breakouts = sum(r['intraday_breakout_seen'] is not None and r['close_above_resistance'] is not None for r in rows)
    summary = {'session':plan['session'],'mode':'synthetic_comparison' if plan.get('synthetic') else 'observation_comparison',
        'observation_requested':observation,'observation_status':observed['status'],'targets':len(rows),
        'observation_run_status':observed['source_status'],
        'recorded_targets':sum(r['observation_status']=='recorded' for r in rows),
        'breakout_observed_targets':observed_breakouts,
        'breakouts_seen':sum(r['intraday_breakout_seen'] is True for r in rows) if observed_breakouts else None,
        'breakouts_held_at_close':sum(r['breakout_outcome']=='held_at_close' for r in rows) if comparable_breakouts else None,
        'partial_session_targets':sum(r['partial_session'] is True for r in rows),'rejected_price_bars':len(rejected),
        'comparison_scope':'recorded observations, not proof of full-session signal absence',
        'limitations':['Frozen prior-close RS; no intraday volume confirmation or trade profitability evaluation.',
            'Partial recordings and missing quotes cannot prove no signal during the full trading day.',
            'Daily high/low does not establish the time of a breakout or stop contact.'],
        'source_hashes':{str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest()
                         for p in [plan_path,prices_path,*([peer_returns_path] if peer_returns_path else []),*observed['sources']]}}
    root = Path(output);root.mkdir(parents=True,exist_ok=False)
    result.to_csv(root/'comparison.csv',index=False,encoding='utf-8-sig')
    (root/'comparison.json').write_text(json.dumps({'summary':summary,'results':rows},ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return summary
