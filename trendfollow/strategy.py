"""Explicit baseline hypotheses, not a reproduction of the video's full method."""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from .relative_strength import mansfield_benchmark_symbol, mansfield_weekly
from .setup import sector_features, setup_features


@dataclass(frozen=True)
class Rules:
    rs_days: int = 126
    breakout_days: int = 55
    weekly_ma: int = 30
    weekly_slope: int = 4
    min_rs: float = 70
    volume_multiple: float = 1.5
    min_turnover: float = 1_000_000_000
    atr_days: int = 14
    stop_atr: float = 2
    max_stop_fraction: float = .15
    stop_limit_enabled: bool = True
    rs_method: str = 'percentile'
    mansfield_weeks: int = 52
    min_mansfield: float = 0
    mansfield_benchmark: str = 'universe'
    entry_method: str = 'breakout'
    setup_reversal_atr: float = 2
    setup_min_rise_atr: float = 4
    setup_max_retracement: float = .5
    setup_volume_contraction: float = .8
    setup_range_contraction: float = .8
    sector_require_action: bool = True
    sector_min_peers: int = 3
    sector_min_breadth: float = .6
    market_units_enabled: bool = False
    market_units_benchmark: str = 'universe'
    market_units_ma_days: int = 60
    market_units_strong_cap: int = 5
    market_units_weak_cap: int = 1
    rs_exit_enabled: bool = False
    rs_exit_percentile: float = 50
    rs_exit_mansfield: float = 0
    rs_exit_confirmations: int = 2

    def validate(self):
        if not isinstance(self.stop_limit_enabled,bool):raise ValueError('stop_limit_enabled는 true/false여야 합니다.')
        if not isinstance(self.rs_exit_enabled,bool):raise ValueError('rs_exit_enabled는 true/false여야 합니다.')
        if not math.isfinite(self.rs_exit_percentile) or not 0<=self.rs_exit_percentile<=100:
            raise ValueError('RS 청산 순위 기준은 0~100이어야 합니다.')
        if not math.isfinite(self.rs_exit_mansfield):raise ValueError('Mansfield 청산 기준은 유한한 수여야 합니다.')
        if isinstance(self.rs_exit_confirmations,bool) or not isinstance(self.rs_exit_confirmations,int) or self.rs_exit_confirmations<1:
            raise ValueError('RS 약화 확인 횟수는 양의 정수여야 합니다.')
        if not isinstance(self.market_units_enabled, bool):
            raise ValueError('market_units_enabled는 true/false여야 합니다.')
        if self.market_units_benchmark not in {'universe','market_index'}:
            raise ValueError('시장 유닛 기준은 universe 또는 market_index여야 합니다.')
        for name in ['market_units_ma_days','market_units_strong_cap','market_units_weak_cap']:
            value = getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value < (1 if name=='market_units_ma_days' else 0):
                raise ValueError('시장 유닛 기간은 양의 정수, 상한은 0 이상의 정수여야 합니다.')
        if self.market_units_weak_cap > self.market_units_strong_cap:
            raise ValueError('약세 유닛 상한은 강세 상한보다 클 수 없습니다.')
        for name in ['rs_days', 'breakout_days', 'weekly_ma', 'weekly_slope', 'atr_days', 'mansfield_weeks']:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f'{name}은 양의 정수여야 합니다.')
        if self.rs_method not in {'percentile', 'mansfield'}:
            raise ValueError('rs_method는 percentile 또는 mansfield여야 합니다.')
        if self.entry_method not in {'breakout','setup'}:
            raise ValueError('entry_method는 breakout 또는 setup이어야 합니다.')
        if not isinstance(self.sector_require_action,bool):
            raise ValueError('sector_require_action은 true/false여야 합니다.')
        if not isinstance(self.sector_min_peers,int) or isinstance(self.sector_min_peers,bool) or self.sector_min_peers<1:
            raise ValueError('sector_min_peers는 양의 정수여야 합니다.')
        for name in ['setup_max_retracement','setup_volume_contraction','setup_range_contraction','sector_min_breadth']:
            value=getattr(self,name)
            if not math.isfinite(value) or not 0<value<=1:
                raise ValueError(f'{name}은 0 초과 1 이하여야 합니다.')
        if self.mansfield_benchmark not in {'universe', 'market_index'}:
            raise ValueError('mansfield_benchmark는 universe 또는 market_index여야 합니다.')
        if not math.isfinite(self.min_mansfield):
            raise ValueError('min_mansfield는 유한한 숫자여야 합니다.')
        if not 0 <= self.min_rs <= 100:
            raise ValueError('min_rs는 0~100이어야 합니다.')
        if not 0 < self.max_stop_fraction < 1:
            raise ValueError('max_stop_fraction은 0~1 사이여야 합니다.')
        for name in ['volume_multiple', 'min_turnover', 'stop_atr','setup_reversal_atr','setup_min_rise_atr']:
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name}은 유한한 양수여야 합니다.')


def validate_prices(prices: pd.DataFrame) -> pd.DataFrame:
    required = {'date', 'symbol', 'open', 'high', 'low', 'close', 'volume'}
    if not required.issubset(prices.columns) or prices.empty:
        raise ValueError('가격 CSV에는 date,symbol,open,high,low,close,volume이 필요합니다.')
    p = prices.copy()
    if p['symbol'].isna().any() or (p['symbol'].astype(str).str.strip() == '').any():
        raise ValueError('종목코드는 비어 있을 수 없습니다.')
    p['symbol'] = p['symbol'].astype(str)
    p['date'] = pd.to_datetime(p['date'], format='%Y-%m-%d', errors='raise')
    if p['date'].isna().any():
        raise ValueError('거래일이 누락되었습니다.')
    if p['date'].dt.tz is not None or not (p['date'] == p['date'].dt.normalize()).all():
        raise ValueError('date는 시간·시간대 없는 거래일이어야 합니다.')
    if p.duplicated(['symbol', 'date']).any():
        raise ValueError('동일 종목·거래일의 중복 행이 있습니다.')
    cols = ['open', 'high', 'low', 'close', 'volume']
    p[cols] = p[cols].apply(pd.to_numeric, errors='raise')
    if not np.isfinite(p[cols].to_numpy()).all():
        raise ValueError('가격·거래량에 결측 또는 무한대가 있습니다.')
    if (p[['open', 'high', 'low', 'close']] <= 0).any().any() or (p.volume < 0).any():
        raise ValueError('가격은 양수, 거래량은 0 이상이어야 합니다. 거래정지 행을 확인하세요.')
    if ((p.high < p[['open', 'close', 'low']].max(axis=1)) |
            (p.low > p[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError('OHLC 가격 범위가 일치하지 않습니다.')
    return p.sort_values(['symbol', 'date']).reset_index(drop=True)


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> float:
    previous = frame.close.shift()
    tr = pd.concat([frame.high - frame.low, (frame.high - previous).abs(),
                    (frame.low - previous).abs()], axis=1).max(axis=1)
    if len(tr) < period:
        return math.nan
    atr = float(tr.iloc[:period].mean())
    for value in tr.iloc[period:]:
        atr = ((period - 1) * atr + float(value)) / period
    return atr


def screen(prices: pd.DataFrame, universe: pd.DataFrame, as_of: str,
           rules: Rules = Rules()) -> pd.DataFrame:
    rules.validate()
    p = validate_prices(prices)
    asof = pd.Timestamp(as_of)
    if asof.tz is not None or asof != asof.normalize():
        raise ValueError('as-of는 YYYY-MM-DD 거래일이어야 합니다.')
    # Truncate before all calculations, including resampling and ranking.
    p = p[p.date <= asof]
    if not {'symbol', 'name', 'sector', 'benchmark'}.issubset(universe.columns):
        raise ValueError('universe에는 symbol,name,sector,benchmark가 필요합니다.')
    if universe.empty or universe.symbol.duplicated().any() or universe.isna().any().any():
        raise ValueError('universe가 비어 있거나 중복·결측이 있습니다.')
    frames = {str(s): f.set_index('date') for s, f in p.groupby('symbol')}
    required_mrs = {mansfield_benchmark_symbol(item, rules) for item in universe.to_dict('records')}
    if rules.mansfield_benchmark == 'market_index' and required_mrs-set(frames):
        raise ValueError('Mansfield 지수 일봉이 없습니다: '+','.join(sorted(required_mrs-set(frames)))+'. index-data로 수집하세요.')
    rows = []
    sectors=None
    if rules.entry_method=='setup':
        sf=sector_features(p,universe,rules)
        sectors=sf[sf.date==asof].set_index('symbol')
    for item in universe.to_dict('records'):
        symbol, benchmark = str(item['symbol']), str(item['benchmark'])
        mrs_benchmark = mansfield_benchmark_symbol(item, rules)
        f, b = frames.get(symbol), frames.get(benchmark)
        row = dict(item, signal=False, signal_rs=False, signal_mansfield=False,
                   rs_method=rules.rs_method, mansfield_rs=math.nan, mansfield_change_1w=math.nan,
                   mansfield_observation_date=None,
                   mansfield_benchmark=mrs_benchmark,
                   entry_method=rules.entry_method,
                   reasons='', quantity=0)
        if f is None or b is None or f.empty or b.empty:
            rows.append(dict(row, reasons='missing_prices_or_benchmark')); continue
        if f.index[-1] != asof or b.index[-1] != asof:
            rows.append(dict(row, reasons='stale_prices_or_benchmark')); continue
        # Benchmark defines expected sessions: no forward filling suspended/missing stocks.
        calendar = b.index[b.index <= asof]
        needed = max(rules.rs_days + 1, (rules.weekly_ma + rules.weekly_slope + 2) * 5,
                     rules.breakout_days + 1 if rules.entry_method=='breakout' else 1, rules.atr_days, 21)
        if len(f) < needed or len(b) < rules.rs_days + 1:
            rows.append(dict(row, reasons='insufficient_history')); continue
        if not calendar[-needed:].equals(f.index[-needed:]):
            rows.append(dict(row, reasons='missing_or_misaligned_sessions')); continue
        close = float(f.close.iloc[-1])
        ret = close / float(f.close.iloc[-rules.rs_days - 1]) - 1
        bret = float(b.close.iloc[-1] / b.close.iloc[-rules.rs_days - 1] - 1)
        weekly = f.close.resample('W-FRI').last()
        weekly = weekly[weekly.index <= asof].dropna()
        ma = weekly.rolling(rules.weekly_ma).mean()
        trend = (len(ma) > rules.weekly_slope and pd.notna(ma.iloc[-rules.weekly_slope - 1])
                 and weekly.iloc[-1] > ma.iloc[-1] > ma.iloc[-rules.weekly_slope - 1])
        high = float(f.high.iloc[-rules.breakout_days - 1:-1].max())
        average_volume = float(f.volume.iloc[-21:-1].mean())
        volume_ratio = float(f.volume.iloc[-1] / average_volume) if average_volume > 0 else 0
        turnover = float((f.close * f.volume).iloc[-21:-1].mean())
        atr = wilder_atr(f, rules.atr_days)
        stop = close - rules.stop_atr * atr
        if rules.entry_method=='setup':
            row.update(setup_features(f,rules,calendar=b.index).iloc[-1].to_dict())
            if symbol in sectors.index:
                row.update(sectors.loc[symbol].drop(labels=['date']).to_dict())
            else:
                row.update(sector_peer_count=0,sector_up_fraction=math.nan,
                           sector_excess_return=math.nan,sector_action=False)
        mb = frames.get(mrs_benchmark)
        if mb is None or mb.empty or mb.index[-1] != asof:
            mrs = pd.DataFrame(columns=['mansfield_rs','mansfield_change_1w'],index=pd.DatetimeIndex([]))
        else:
            mrs = mansfield_weekly(f.close, mb.close.reindex(b.index), rules.mansfield_weeks)
        completed = mrs[mrs.index <= asof]
        if not completed.empty:
            row.update(mansfield_rs=float(completed.mansfield_rs.iloc[-1]),
                       mansfield_change_1w=float(completed.mansfield_change_1w.iloc[-1]),
                       mansfield_observation_date=completed.index[-1])
        row.update(reference_price=close, stop_price=stop, atr=atr, atr_pct=100*atr/close,
                   return_126d=ret, excess_return=ret-bret, weekly_trend=bool(trend),
                   prior_breakout_high=high, breakout=bool(close > high),
                   volume_ratio=volume_ratio, avg_turnover=turnover)
        rows.append(row)
    result = pd.DataFrame(rows)
    if rules.market_units_enabled:
        from .market_units import market_unit_features
        market = market_unit_features(p,universe,rules)
        result = result.merge(market[market.date==asof].drop(columns='date'),on='symbol',how='left',validate='one_to_one')
    if 'return_126d' not in result:
        result['rs_percentile'] = np.nan
        result['allocation_score'] = np.nan
        from .rs_exit import exit_columns
        from .stop_limit import apply_stop_limit
        return apply_stop_limit(exit_columns(result,rules,as_of),rules)
    # Rank only in the explicitly supplied analysis universe; not a market-wide RS claim.
    result['rs_percentile'] = result.return_126d.rank(method='average', pct=True) * 100
    for i, row in result.iterrows():
        if row.reasons:
            continue
        tests = {'weak_weekly_trend': row.weekly_trend,
                 'weak_volume': row.volume_ratio >= rules.volume_multiple,
                 'low_liquidity': row.avg_turnover >= rules.min_turnover,
                 'invalid_stop': 0 < row.stop_price < row.reference_price}
        if rules.entry_method=='breakout':
            tests['no_breakout']=row.breakout
        else:
            tests.update(setup_not_ready=bool(row.setup_ready),no_setup_trigger=bool(row.setup_trigger),
                unhealthy_pullback=not row.setup_invalidated,
                setup_volume_not_contracted=row.setup_volume_ratio<=rules.setup_volume_contraction,
                setup_range_not_contracted=row.setup_range_ratio<=rules.setup_range_contraction,
                weak_sector_action=not rules.sector_require_action or bool(row.sector_action))
        common = [name for name, passed in tests.items() if not passed]
        rs_reasons = ([] if row.rs_percentile >= rules.min_rs else ['weak_rs'])
        if not row.excess_return > 0:
            rs_reasons.append('benchmark_underperformance')
        mrs_reasons = (['insufficient_mansfield_history'] if pd.isna(row.mansfield_rs) else
                       ([] if row.mansfield_rs > rules.min_mansfield else ['weak_mansfield']))
        result.at[i, 'signal_rs'] = not (common+rs_reasons)
        result.at[i, 'signal_mansfield'] = not (common+mrs_reasons)
        reasons = common+(rs_reasons if rules.rs_method == 'percentile' else mrs_reasons)
        result.at[i, 'signal'] = not reasons
        result.at[i, 'reasons'] = ','.join(reasons)
    result['allocation_score'] = result.rs_percentile if rules.rs_method == 'percentile' else result.mansfield_rs
    from .rs_exit import exit_columns
    result=exit_columns(result,rules,as_of)
    from .stop_limit import apply_stop_limit
    result=apply_stop_limit(result,rules)
    return result.sort_values(['allocation_score', 'symbol'], ascending=[False, True], na_position='last')
