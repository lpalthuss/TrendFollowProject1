"""Mansfield relative strength on completed weekly bars, expressed with a x100 scale."""
import numpy as np
import pandas as pd


MARKET_INDICES = {'KOSPI': 'KOSPI', 'KOSDAQ': 'KOSDAQ'}


def mansfield_benchmark_symbol(item, rules):
    if rules.mansfield_benchmark == 'universe':
        return str(item['benchmark'])
    market = str(item.get('market', '')).upper()
    if market not in MARKET_INDICES:
        raise ValueError('지수 기준 Mansfield 계산에는 universe의 market(KOSPI/KOSDAQ)이 필요합니다.')
    return MARKET_INDICES[market]


def mansfield_weekly(stock_close: pd.Series, benchmark_close: pd.Series, weeks: int = 52):
    if not isinstance(weeks, int) or isinstance(weeks, bool) or weeks < 1:
        raise ValueError('mansfield_weeks는 양의 정수여야 합니다.')
    benchmark = benchmark_close.sort_index()
    stock = stock_close.sort_index().reindex(benchmark.index)
    ratio = (stock/benchmark.where(benchmark > 0)).replace([np.inf, -np.inf], np.nan)
    weekly = ratio.resample('W-FRI').last()
    # The final benchmark session of a week must have a matching stock close.
    # Do not silently substitute Thursday when Friday's stock close is missing.
    has_last_session = ratio.notna().resample('W-FRI').last().eq(True)
    weekly = weekly.where(has_last_session)
    average = weekly.rolling(weeks, min_periods=weeks).mean()
    value = (weekly/average-1)*100
    return pd.DataFrame({'relative_price': weekly, 'relative_price_sma': average,
                         'mansfield_rs': value, 'mansfield_change_1w': value.diff()})


def decision_rule_settings(rules):
    """Indicator-only additions must not invalidate existing percentile paper accounts."""
    from dataclasses import asdict
    values = asdict(rules)
    if not rules.market_units_enabled:
        from .market_units import MARKET_UNIT_SETTINGS
        for key in MARKET_UNIT_SETTINGS:
            values.pop(key,None)
    if values.get('entry_method','breakout')=='breakout':
        from .setup import SETUP_SETTINGS
        for key in SETUP_SETTINGS:
            values.pop(key,None)
    if values.get('rs_method', 'percentile') == 'percentile':
        for key in ['rs_method', 'mansfield_weeks', 'min_mansfield', 'mansfield_benchmark']:
            values.pop(key, None)
    elif values.get('mansfield_benchmark') == 'universe':
        # Compatibility with Mansfield accounts created before benchmark selection existed.
        values.pop('mansfield_benchmark', None)
    return values
