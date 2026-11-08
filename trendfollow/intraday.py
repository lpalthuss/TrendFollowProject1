"""Read-only, provisional intraday observations. Never mutates paper accounts."""
from datetime import datetime, time
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd

from .industries import load_peer_history
from .strategy import Rules

KST = ZoneInfo('Asia/Seoul')
MAX_SUBSCRIPTIONS = 40


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('장중 시각에는 시간대가 필요합니다.')
    return result.astimezone(KST)


def finite(value, positive=False):
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError('장중 수치가 유효하지 않습니다.')
    return number


def prepare_plan(signals_path, prices_path, universe_path, session, symbols,
                 config_path, output, paper_state=None, volume_profile=None):
    """Freeze prior-close setup/RS filters; include every eligible industry peer."""
    config = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    rules = Rules(**config.get('rules', {})); rules.validate()
    if rules.entry_method != 'setup':
        raise ValueError('장중 준비는 셋업 설정을 사용하세요.')
    # Market units and live RS are separate future extensions, never silently ignored.
    if rules.market_units_enabled:
        raise ValueError('장중 시장 유닛 적용은 아직 지원하지 않습니다. 비활성 비교 설정을 사용하세요.')
    signals = pd.read_csv(signals_path, dtype={'symbol': str, 'benchmark': str})
    universe = pd.read_csv(universe_path, dtype={'symbol': str, 'benchmark': str}, keep_default_na=False)
    if signals.empty or signals.symbol.duplicated().any() or signals.date.nunique() != 1:
        raise ValueError('한 거래일의 중복 없는 전체 신호 파일이 필요합니다.')
    as_of = str(signals.date.iloc[0])
    day = datetime.strptime(session, '%Y-%m-%d').date()
    if day <= datetime.strptime(as_of, '%Y-%m-%d').date() or day.weekday() >= 5:
        raise ValueError('감시일은 기준일 이후 평일이어야 합니다. 실제 휴장 여부는 별도 확인하세요.')
    peers = load_peer_history(universe)
    peers = peers[peers.date == pd.Timestamp(as_of)].copy()
    if peers.empty:
        raise ValueError('기준일의 산업 구성 자료가 없습니다.')
    # A membership row remains in the expected coverage denominator even if its
    # prior-day quote was ineligible. Today may trade; missing ticks remain unknown.
    if not {'benchmark','market'}.issubset(peers):
        raise ValueError('산업 동료 자료에 시장·벤치마크 정보가 필요합니다.')
    peers['benchmark'] = peers.benchmark.astype(str).str.zfill(6)
    prices = pd.read_csv(prices_path, dtype={'symbol': str})
    prices['date'] = pd.to_datetime(prices.date)
    prices = prices[prices.date <= pd.Timestamp(as_of)]
    positions = {}
    if paper_state:
        state = json.loads(Path(paper_state).read_text(encoding='utf-8'))
        if state['last_date'] != as_of:
            raise ValueError('보유 계좌와 신호 파일의 기준일이 다릅니다.')
        positions = state['positions']
    chosen = set(symbols) | set(positions)
    if not chosen or not chosen.issubset(set(signals.symbol)):
        raise ValueError('감시 종목은 신호 파일에 포함된 종목이어야 합니다.')
    targets, subscriptions, members = {}, set(), {}
    for symbol in sorted(chosen):
        row = signals.set_index('symbol').loc[symbol]
        if row.rs_method != rules.rs_method or row.entry_method != rules.entry_method:
            raise ValueError('신호 파일과 선택 설정의 방식이 다릅니다.')
        item = universe[universe.symbol == symbol]
        if len(item) != 1 or item.sector.iloc[0] != row.sector:
            raise ValueError('신호 파일과 산업 유니버스가 다릅니다.')
        sector = str(row.sector)
        if sector.startswith('UNCLASSIFIED:'):
            raise ValueError('산업 미분류 종목은 장중 산업 감시를 구성할 수 없습니다.')
        group = peers[peers.sector == sector]
        if group.empty:
            raise ValueError('감시 산업의 구성원이 없습니다.')
        for peer in group.to_dict('records'):
            members[peer['symbol']] = {'sector': sector, 'benchmark': str(peer['benchmark'])}
            subscriptions.update([peer['symbol'], str(peer['benchmark'])])
        subscriptions.update([symbol, str(row.benchmark)])
        f = prices[prices.symbol == symbol].sort_values('date')
        if f.empty or str(f.date.iloc[-1].date()) != as_of:
            raise ValueError('감시 종목의 기준일 가격이 없습니다.')
        reasons = []
        for field in ['weekly_trend','eligible_history']:
            if not bool(row[field]): reasons.append(field)
        if row.avg_turnover < rules.min_turnover: reasons.append('low_liquidity')
        if not 0 < row.stop_price < row.reference_price or rules.stop_limit_enabled and (row.reference_price-row.stop_price)/row.reference_price > rules.max_stop_fraction:
            reasons.append('invalid_stop')
        if rules.rs_method == 'percentile':
            if not row.rs_percentile >= rules.min_rs or not row.excess_return > 0: reasons.append('weak_prior_rs')
        elif not row.mansfield_rs > rules.min_mansfield:
            reasons.append('weak_prior_mansfield')
        resistance, floor = None, None
        if row.setup_phase != 'base' or pd.isna(row.setup_peak_date) or pd.isna(row.setup_resistance):
            reasons.append('no_unconsumed_setup')
        else:
            resistance = finite(row.setup_resistance, True)
            floor = resistance-rules.setup_max_retracement*(resistance-finite(row.setup_wave_low, True))
            # Tomorrow's base includes ALL of today's completed bar. The stored
            # baseline signal's contraction statistics exclude its own trigger day.
            base = f[f.date > pd.Timestamp(row.setup_peak_date)]
            previous = f.close.shift()
            tr = pd.concat([f.high-f.low,(f.high-previous).abs(),(f.low-previous).abs()],axis=1).max(axis=1)/f.close
            if len(base) < 4 or base.low.min() < floor:
                reasons.append('unhealthy_or_short_base')
            else:
                middle = len(base)//2
                volumes = base.volume
                ranges = tr.loc[base.index]
                vr = volumes.iloc[middle:].mean()/volumes.iloc[:middle].mean() if volumes.iloc[:middle].mean() > 0 else math.inf
                rr = ranges.iloc[middle:].mean()/ranges.iloc[:middle].mean() if ranges.iloc[:middle].mean() > 0 else math.inf
                if vr > rules.setup_volume_contraction: reasons.append('base_volume_not_contracted')
                if rr > rules.setup_range_contraction: reasons.append('base_range_not_contracted')
        targets[symbol] = {'name': str(row['name']), 'sector': sector, 'benchmark': str(row.benchmark),
            'previous_close': finite(f.close.iloc[-1], True), 'resistance': resistance,
            'healthy_floor': floor, 'holding_stop': finite(positions[symbol]['stop_price'],True) if symbol in positions else None,
            'prior_filters_pass': not reasons, 'prior_filter_reasons': reasons,
            'peer_symbols': sorted(set(group.symbol)-{symbol})}
    if len(subscriptions) > MAX_SUBSCRIPTIONS:
        raise ValueError(f'전체 산업 동료 포함 구독 {len(subscriptions)}개가 KIS 한도 40개를 넘습니다. 더 작은 산업을 선택하거나 수집 방식을 변경하세요.')
    input_paths = [signals_path,prices_path,universe_path,config_path,
                   next(iter(set(universe.industry_peer_history.astype(str))-{''}))]
    if paper_state: input_paths.append(paper_state)
    plan = {'version': 1, 'as_of': as_of, 'session': session, 'exchange': 'KRX',
        'mode': 'observation_only', 'config': str(config_path),
        'rs_method': rules.rs_method, 'rs_timing': 'prior_completed_close_frozen',
        'volume_confirmation': 'unavailable_same_time_history',
        'sector_min_peers': rules.sector_min_peers, 'sector_min_breadth': rules.sector_min_breadth,
        'sector_require_action': rules.sector_require_action, 'min_peer_coverage': 1.0,
        'max_quote_age_seconds': 60, 'targets': targets, 'members': members,
        'subscriptions': sorted(subscriptions),
        'source_hashes': {str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
                          for path in input_paths}}
    if volume_profile:
        from .intraday_volume import validate_reference
        profile=json.loads(Path(volume_profile).read_text(encoding='utf-8'))
        validate_reference(profile,as_of,False)
        profile['symbols']={s:profile['symbols'][s] for s in targets if s in profile['symbols']}
        plan['volume_reference']=profile
        plan['volume_confirmation']='same_time_history'
        plan['source_hashes'][str(volume_profile)]=hashlib.sha256(Path(volume_profile).read_bytes()).hexdigest()
    validate_plan(plan)
    output = Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists(): raise ValueError('기존 감시 계획은 덮어쓰지 않습니다. 새 출력 경로를 사용하세요.')
    output.write_text(json.dumps(plan,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return plan


def validate_plan(plan):
    if 'volume_reference' in plan:
        from .intraday_volume import validate_reference
        validate_reference(plan['volume_reference'],plan['as_of'],bool(plan.get('synthetic')))
    if plan.get('version') != 1 or plan.get('mode') != 'observation_only' or plan.get('exchange') != 'KRX':
        raise ValueError('지원하지 않는 장중 계획입니다.')
    day = datetime.strptime(plan['session'],'%Y-%m-%d').date()
    if day <= datetime.strptime(plan['as_of'],'%Y-%m-%d').date():
        raise ValueError('감시일은 기준일 이후여야 합니다.')
    subscriptions = plan['subscriptions']
    if not 0 < len(subscriptions) <= MAX_SUBSCRIPTIONS or len(set(subscriptions)) != len(subscriptions):
        raise ValueError('구독 개수·중복을 확인하세요. KIS 한도는 40개입니다.')
    if any(not re.fullmatch(r'[0-9A-Z]{6}',s) for s in subscriptions):
        raise ValueError('감시 구독은 KRX 종목코드 6자리여야 합니다.')
    if not plan['targets'] or not 0 < plan['min_peer_coverage'] <= 1 or plan['max_quote_age_seconds'] <= 0:
        raise ValueError('장중 감시 범위·신선도 조건이 유효하지 않습니다.')
    if plan['sector_min_peers'] < 1 or not 0 < plan['sector_min_breadth'] <= 1:
        raise ValueError('산업 동반 상승 조건이 유효하지 않습니다.')
    required = set(plan['targets'])
    for symbol,target in plan['targets'].items():
        required.add(target['benchmark'])
        finite(target['previous_close'],True)
        for field in ['resistance','healthy_floor','holding_stop']:
            if target[field] is not None: finite(target[field],True)
        for peer in target['peer_symbols']:
            if peer == symbol or peer not in plan['members'] or plan['members'][peer]['sector'] != target['sector']:
                raise ValueError('산업 동료 구성이 유효하지 않습니다.')
            required.update([peer,plan['members'][peer]['benchmark']])
    if not required.issubset(set(subscriptions)):
        raise ValueError('필수 산업·비교 종목 구독이 누락됐습니다.')


class IntradayMonitor:
    def __init__(self, plan):
        validate_plan(plan)
        self.plan, self.quotes, self.last, self.invalidated = plan, {}, {}, set()
        self.basis_mismatch = set()
        self.last_stops = {}
        self.volume_samples = {}

    def reset_connection(self):
        self.quotes.clear()  # Never reuse quotes across a connection gap.
        self.volume_samples.clear()

    def update(self, tick, received_at, evaluate=True):
        now = stamp(received_at); event = stamp(tick['timestamp'])
        symbol = tick['symbol']
        if symbol not in self.plan['subscriptions']:
            raise ValueError('계획에 없는 장중 종목입니다.')
        if str(event.date()) != self.plan['session'] or str(now.date()) != self.plan['session']:
            raise ValueError('감시일과 수신 거래일이 다릅니다.')
        age = (now-event).total_seconds()
        if age < -2 or age > self.plan['max_quote_age_seconds']:
            return self.evaluate(now) if evaluate else self.evaluate_stops(now,symbol)
        if not time(9) <= event.time().replace(tzinfo=None) <= time(15,30):
            return []
        normalized = dict(tick)
        for key in ['price','previous_close','day_low']:
            normalized[key] = finite(tick[key],True)
        normalized['cumulative_volume'] = finite(tick['cumulative_volume'])
        if normalized['cumulative_volume'] < 0 or normalized['day_low'] > normalized['price']:
            raise ValueError('장중 거래량·저가가 유효하지 않습니다.')
        normalized['timestamp'] = event.isoformat()
        old = self.quotes.get(symbol)
        if old and (event < stamp(old['timestamp']) or normalized['cumulative_volume'] < old['cumulative_volume']):
            return self.evaluate(now) if evaluate else self.evaluate_stops(now,symbol)
        if old:
            normalized['day_low'] = min(normalized['day_low'],old['day_low'])
        target = self.plan['targets'].get(symbol)
        if target and abs(normalized['previous_close']/target['previous_close']-1) > .001:
            # Adjusted daily setup levels must agree with the live raw-price basis.
            self.quotes.pop(symbol,None)
            self.basis_mismatch.add(symbol)
            return self.evaluate(now) if evaluate else self.evaluate_stops(now,symbol)
        from .intraday_volume import capture_tick_boundary
        capture_tick_boundary(self,symbol,normalized,old,now)
        self.quotes[symbol] = normalized
        if target and target['healthy_floor'] is not None and normalized['day_low'] < target['healthy_floor']:
            self.invalidated.add(symbol)
        return self.evaluate(now) if evaluate else self.evaluate_stops(now,symbol)

    def evaluate_stops(self, now, symbol=None):
        """Cheap immediate held-position check; no industry aggregation."""
        if isinstance(now,str): now = stamp(now)
        events = []
        targets = [symbol] if symbol in self.plan['targets'] else ([] if symbol else self.plan['targets'])
        for target_symbol in targets:
            target = self.plan['targets'][target_symbol]
            if target['holding_stop'] is None: continue
            quote = self.quotes.get(target_symbol)
            fresh = (quote is not None and target_symbol not in self.basis_mismatch and
                     0 <= (now-stamp(quote['timestamp'])).total_seconds() <= self.plan['max_quote_age_seconds'])
            breached = bool(quote['day_low'] <= target['holding_stop']) if fresh else None
            if target_symbol not in self.last_stops or self.last_stops[target_symbol] != breached:
                events.append({'kind':'holding_stop_state','timestamp':now.isoformat(),'symbol':target_symbol,
                    'holding_stop_breached':breached,'holding_stop':target['holding_stop'],
                    'day_low':quote['day_low'] if fresh else None,'mode':'observation_only'})
                self.last_stops[target_symbol] = breached
        return events

    def evaluate(self, now):
        if isinstance(now,str): now = stamp(now)
        events = []
        def fresh(symbol):
            quote = self.quotes.get(symbol)
            if quote and 0 <= (now-stamp(quote['timestamp'])).total_seconds() <= self.plan['max_quote_age_seconds']:
                return quote
        def day_return(symbol):
            quote = fresh(symbol)
            return None if quote is None else quote['price']/quote['previous_close']-1
        for symbol,target in self.plan['targets'].items():
            peer_returns, excess = [], []
            for peer in target['peer_symbols']:
                value = day_return(peer)
                benchmark = day_return(self.plan['members'][peer]['benchmark'])
                if value is not None and benchmark is not None:
                    peer_returns.append(value); excess.append(value-benchmark)
            count = len(peer_returns); expected = len(target['peer_symbols'])
            coverage = count/expected if expected else 0
            available = count >= self.plan['sector_min_peers'] and coverage >= self.plan['min_peer_coverage']
            breadth = sum(v > 0 for v in peer_returns)/count if count else None
            mean_excess = sum(excess)/count if count else None
            action = bool(breadth >= self.plan['sector_min_breadth'] and mean_excess > 0) if available else None
            quote = fresh(symbol)
            valid = quote is not None and symbol not in self.invalidated and symbol not in self.basis_mismatch
            breakout = bool(quote['price'] > target['resistance']) if valid and target['resistance'] is not None else None
            stop = (bool(quote['day_low'] <= target['holding_stop'])
                    if quote and target['holding_stop'] is not None and symbol not in self.basis_mismatch else None)
            watch = (bool(breakout and target['prior_filters_pass'] and (action or not self.plan['sector_require_action']))
                     if valid and (available or not self.plan['sector_require_action']) else None)
            from .intraday_volume import volume_check
            volume=volume_check(self,symbol,now,quote)
            confirmed=bool(watch is True and volume['volume_pass'] is True)
            state = {'breakout_present': breakout, 'holding_stop_breached': stop,
                'industry_action': action, 'entry_watch': watch, 'setup_invalidated': symbol in self.invalidated,
                'price_basis_mismatch':symbol in self.basis_mismatch,
                'volume_pass':volume['volume_pass'],'volume_status':volume['volume_status'],
                'entry_confirmed':confirmed}
            if state != self.last.get(symbol):
                events.append({'timestamp':now.isoformat(),'symbol':symbol,'kind':'signal_state',
                    **state,'peer_count':count,'expected_peer_count':expected,'peer_coverage':coverage,
                    'peer_up_fraction':breadth,'peer_excess_return':mean_excess,
                    'price':quote['price'] if quote else None,
                    'volume_vs_previous_same_time':quote.get('volume_vs_previous_same_time') if quote else None,
                    **volume,'volume_confirmation':self.plan['volume_confirmation'],
                    'rs_timing':self.plan['rs_timing'],'mode':'observation_only'})
                self.last[symbol] = state
        return events


def replay_ticks(plan_path, ticks_path, output):
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    monitor = IntradayMonitor(plan)
    root = Path(output)
    root.mkdir(parents=True,exist_ok=False)
    count = event_count = tick_count = snapshot_count = 0
    previous = None
    with Path(ticks_path).open(encoding='utf-8') as source, (root/'events.jsonl').open('w',encoding='utf-8') as sink:
        for line in source:
            record = json.loads(line)
            received = stamp(record['received_at'])
            if previous is not None and received < previous:
                raise ValueError('재생 입력은 수신 시각 순서여야 합니다.')
            previous = received
            if record.get('kind') == 'connection_reset':
                monitor.reset_connection()
                values = ((monitor.evaluate_stops(received) if record.get('defer_evaluation') else [])
                          +monitor.evaluate(received))
            elif record.get('kind') == 'stream_settings':
                from .intraday_sampling import validate_sampling
                if record.get('schema_version') != 2:
                    raise ValueError('지원하지 않는 장중 기록 버전입니다.')
                validate_sampling(record['evaluation_seconds'],record['tick_storage'],plan['max_quote_age_seconds'])
                values = []
            elif record.get('kind') == 'quote_snapshot':
                snapshot_count += 1
                from .intraday_sampling import restore_snapshot
                restore_snapshot(monitor,record)
                values = monitor.evaluate(received)
            elif record.get('kind') == 'evaluation':
                values = monitor.evaluate(received)
            elif record.get('kind') == 'fast_clock':
                values = monitor.evaluate_stops(received)
            elif record.get('kind') == 'clock':
                values = monitor.evaluate(received)
            else:
                tick_count += 1
                values = monitor.update(record['tick'],record['received_at'],evaluate=not record.get('defer_evaluation',False))
            for event in values:
                sink.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n'); event_count += 1
            count += 1
    (root/'plan.json').write_bytes(Path(plan_path).read_bytes())
    summary = {'mode':'replay_observation_only','ticks_source':str(Path(ticks_path).resolve()),'ticks':count,'journal_records':count,
               'tick_records':tick_count,'quote_snapshots':snapshot_count,'events':event_count,'orders_sent':0}
    (root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary
