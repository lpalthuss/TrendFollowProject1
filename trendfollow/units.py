"""Deterministic replay of manually verified trade events, without automatic sizing edits."""
from datetime import datetime


def replay(events: list[dict], max_units: int = 5) -> int:
    if max_units < 1:
        raise ValueError('max_units must be positive')
    units, seen, states = 1, set(), {}
    previous = None
    for event in events:
        eid, trade, kind, raw_timestamp = (event[k] for k in ['event_id', 'trade_id', 'kind', 'timestamp'])
        timestamp = datetime.fromisoformat(raw_timestamp)
        if not eid or not trade:
            raise ValueError('event_id,trade_id는 비어 있을 수 없습니다.')
        if eid in seen:
            continue
        if previous is not None and timestamp < previous:
            raise ValueError('이벤트는 시간순이어야 합니다. 동시 이벤트는 파일 순서를 따릅니다.')
        previous = timestamp
        seen.add(eid)
        state = states.get(trade)
        if kind not in {'success_3r', 'loss_closed', 'neutral_closed'}:
            raise ValueError('알 수 없는 이벤트입니다.')
        if state == 'closed':
            raise ValueError('청산한 거래에 이벤트를 추가할 수 없습니다.')
        if kind == 'success_3r':
            if state != 'success':
                units = min(max_units, units+1)
                states[trade] = 'success'
        else:
            # Policy: one outcome per trade. A prior +3R success is never counted as a loss again.
            if kind == 'loss_closed' and state != 'success':
                units = max(1, units-1)
            states[trade] = 'closed'
    return units
