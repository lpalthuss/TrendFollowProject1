"""Interval decisions with an immediate stop path and selectable quote journals."""
from datetime import timedelta
import math

from .intraday import finite, stamp


def validate_sampling(seconds, storage, max_age):
    if isinstance(seconds,bool) or not math.isfinite(seconds) or not 0 < seconds <= max_age:
        raise ValueError('판단 간격은 0 초과, 시세 신선도 한도 이하여야 합니다.')
    if storage not in {'all','summary'}:
        raise ValueError('시세 저장은 all 또는 summary여야 합니다.')


def restore_snapshot(monitor, record):
    """Restore latest normalized samples, never a reconstruction of every trade."""
    now = stamp(record['received_at'])
    quotes = {}
    for symbol,raw in record['quotes'].items():
        if symbol not in monitor.plan['subscriptions'] or raw['symbol'] != symbol:
            raise ValueError('요약 시세에 계획 밖 종목이 있습니다.')
        event = stamp(raw['timestamp'])
        if str(event.date()) != monitor.plan['session'] or event > now+timedelta(seconds=2):
            raise ValueError('요약 시세의 거래일·시각이 유효하지 않습니다.')
        quote = dict(raw)
        for field in ['price','previous_close','day_low']:
            quote[field] = finite(quote[field],True)
        quote['cumulative_volume'] = finite(quote['cumulative_volume'])
        if quote['cumulative_volume'] < 0 or quote['day_low'] > quote['price']:
            raise ValueError('요약 시세의 저가·거래량이 유효하지 않습니다.')
        quotes[symbol] = quote
    for key in ['invalidated','basis_mismatch']:
        if not set(record[key]).issubset(monitor.plan['targets']):
            raise ValueError('요약 시세의 차단 대상이 유효하지 않습니다.')
    monitor.quotes = quotes
    monitor.invalidated = set(record['invalidated'])
    monitor.basis_mismatch = set(record['basis_mismatch'])
    monitor.volume_samples={}
    for symbol,sample in record.get('volume_samples',{}).items():
        at=stamp(sample['sample_timestamp'])
        cutoff=stamp(monitor.plan['session']+'T'+sample['minute']+'+09:00')
        if symbol not in monitor.plan['targets'] or cutoff>now or not 0<=(cutoff-at).total_seconds()<=60:
            raise ValueError('요약 거래량 표본 시각·종목이 유효하지 않습니다.')
        volume=finite(sample['cumulative_volume'])
        if volume<0:raise ValueError('요약 누적 거래량이 음수입니다.')
        monitor.volume_samples[symbol]=dict(sample)


class ObservationSession:
    """record/emit callbacks keep this deterministic for replay tests."""
    def __init__(self, monitor, received_at, record, emit, seconds=5, storage='summary'):
        validate_sampling(seconds,storage,monitor.plan['max_quote_age_seconds'])
        self.monitor, self.record_callback, self.emit = monitor, record, emit
        self.seconds, self.storage = seconds, storage
        self.received = stamp(received_at)
        self.due = self.received+timedelta(seconds=seconds)
        self.evaluations = self.records = self.ticks = 0
        self.closed = False
        self.record({'kind':'stream_settings','schema_version':2,'received_at':self.received.isoformat(),
                     'evaluation_seconds':seconds,'tick_storage':storage})

    def record(self, value):
        self.record_callback(value)
        self.records += 1

    def advance(self, received_at):
        now = stamp(received_at)
        if now < self.received:
            raise ValueError('장중 수신 시각은 시간순이어야 합니다.')
        self.received = now
        return now

    def reset_connection(self, received_at):
        now = self.advance(received_at)
        self.record({'kind':'connection_reset','received_at':now.isoformat(),'defer_evaluation':True})
        self.monitor.reset_connection()
        self.emit(self.monitor.evaluate_stops(now))
        # Gaps invalidate visible aggregate states immediately, not after 5 seconds.
        self.emit(self.monitor.evaluate(now))
        self.evaluations += 1
        self.due = now+timedelta(seconds=self.seconds)

    def tick(self, tick, received_at):
        now = self.advance(received_at)
        events = self.monitor.update(tick,received_at,evaluate=False)
        self.ticks += 1
        if self.storage == 'all' or events:
            self.record({'kind':'tick' if self.storage=='all' else 'critical_tick',
                         'received_at':now.isoformat(),'tick':dict(tick),'defer_evaluation':True})
        self.emit(events)
        if now >= self.due: self.evaluate(now)

    def clock(self, received_at):
        now = self.advance(received_at)
        events = self.monitor.evaluate_stops(now)
        if self.storage == 'all' or events:
            self.record({'kind':'fast_clock','received_at':now.isoformat()})
        self.emit(events)
        if now >= self.due: self.evaluate(now)

    def evaluate(self, now):
        record = {'kind':'evaluation','received_at':now.isoformat()}
        if self.storage == 'summary':
            record.update(kind='quote_snapshot',quotes={k:dict(v) for k,v in self.monitor.quotes.items()},
                          invalidated=sorted(self.monitor.invalidated),basis_mismatch=sorted(self.monitor.basis_mismatch),
                          volume_samples={k:dict(v) for k,v in self.monitor.volume_samples.items()})
        self.record(record)
        self.emit(self.monitor.evaluate(now))
        self.evaluations += 1
        self.due = now+timedelta(seconds=self.seconds)

    def finish(self, received_at):
        if self.closed: return
        now = self.advance(received_at)
        self.clock(received_at)
        # Flush partial final interval on normal exit, Ctrl+C and connection failure.
        if self.due != now+timedelta(seconds=self.seconds): self.evaluate(now)
        self.closed = True
