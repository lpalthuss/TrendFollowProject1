"""Self-contained Streamlit chart for saved daily OHLCV bars."""

from datetime import date
from functools import lru_cache
import json
import math
from pathlib import Path


_HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _assets():
    template = (_HERE / "price_chart.html").read_text(encoding="utf-8")
    library = (_HERE / "vendor" / "lightweight-charts-5.3.0.min.js").read_text(encoding="utf-8")
    return template, library


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def provisional_bar(history, quotes, today, period):
    """Join only comparable KRX daily bars for the chart preview."""
    previous = history[-1]
    indexed = quotes.set_index('date')
    if today not in indexed.index or previous['date'] not in indexed.index:
        raise ValueError('당일 봉 또는 직전 거래일 비교 가격이 아직 없습니다.')
    last = previous['close']
    if abs(indexed.loc[previous['date'], 'close'] / last - 1) > .001:
        raise ValueError('KIS와 저장 일봉의 직전 종가 기준이 다릅니다.')
    bar = indexed.loc[today].to_dict()
    o,h,l,c,v = (bar[k] for k in ('open','high','low','close','volume'))
    if not all(math.isfinite(n) for n in (o,h,l,c,v)) or not (0 < l <= min(o,c) <= max(o,c) <= h and v > 0):
        raise ValueError('유효한 당일 체결 자료가 없습니다.')
    atr = previous.get('atr')
    if atr and period > 0:
        atr = (atr*(period-1) + max(h-l, abs(h-last), abs(l-last))) / period
    volumes = [_number(r.get('volume')) for r in history[-20:]]
    base = sum(volumes)/20 if len(volumes)==20 and None not in volumes else None
    return dict(bar, date=today, provisional=True, atr=atr, volume_ratio=v/base if base else None,
                atr_up_multiple=max(0.,h-last)/atr if atr else None, atr_down_multiple=max(0.,last-l)/atr if atr else None)


def chart_periods(rows):
    """Weekly and monthly candles end at the last available session in each bucket."""
    result = {'day': rows, 'week': [], 'month': []}
    last_keys = {}
    for row in rows:
        day = date.fromisoformat(row['time'])
        for period, bucket in [('week', day.isocalendar()[:2]), ('month', (day.year, day.month))]:
            if last_keys.get(period) != bucket:
                result[period].append(dict(row, first=row['time']))
                last_keys[period] = bucket
                continue
            bar = result[period][-1]
            volume = bar['volume'] + row['volume'] if None not in (bar['volume'], row['volume']) else None
            bar.update(row, open=bar['open'], high=max(bar['high'], row['high']),
                       low=min(bar['low'], row['low']), volume=volume)
    for period, bars in result.items():
        closes = [bar['close'] for bar in bars]
        for i, bar in enumerate(bars):
            for n in (10, 20, 60, 120):
                bar[f'ma{n}'] = sum(closes[i+1-n:i+1]) / n if i+1 >= n else None
        result[period] = [bar for bar in bars if not bar.get('warmup')]
    return result


def chart_html(prices):
    """Render a local chart bundle; no browser CDN or live market request is needed."""
    rows = []
    for item in prices:
        day = str(item.get("date", ""))[:10]
        if len(day) != 10:
            continue
        row = {"time": day, "warmup": bool(item.get("warmup")), "provisional": item.get("provisional") is True}
        for field in ("open", "high", "low", "close", "volume", "volume_ratio", "atr",
                      "atr_up_multiple", "atr_down_multiple", "ma10", "ma20", "ma60", "ma120"):
            row[field] = _number(item.get(field))
        if all(row[field] is not None and row[field] > 0 for field in ("open", "high", "low", "close")):
            rows.append(row)
    rows.sort(key=lambda row: row["time"])
    payload = json.dumps(chart_periods(rows), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template, library = _assets()
    return template.replace("/*__LIGHTWEIGHT_CHARTS__*/", library).replace("/*__CHART_DATA__*/", payload)
