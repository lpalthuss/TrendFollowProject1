"""Mansfield relative strength on completed weekly bars, expressed with a x100 scale."""
import numpy as np
import pandas as pd
import math


MARKET_INDICES = {'KOSPI': 'KOSPI', 'KOSDAQ': 'KOSDAQ'}


def multi_period_daily_rs(stock_close: pd.Series, kospi_close: pd.Series):
    """Documented 60/120/250-session relative strength, for display only."""
    aligned = pd.concat([stock_close.rename('stock'), kospi_close.rename('kospi')], axis=1, join='inner')
    aligned = aligned[(aligned.stock > 0) & (aligned.kospi > 0)].replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    relative = aligned.stock / aligned.kospi
    values = []
    dates = []
    windows = []
    for period, required in [(60, 60), (120, 90), (250, 187)]:
        if len(relative) < required:
            continue
        average = relative.rolling(period, min_periods=math.floor(period * .75)).mean()
        rs = ((relative / average) - 1) * 100
        valid = rs.replace([np.inf, -np.inf], np.nan).dropna()
        if valid.empty:
            continue
        values.append(float(valid.iloc[-1]))
        dates.append(valid.index[-1])
        windows.append(str(period))
    if not values:
        return dict(multi_rs=None, multi_rs_raw=None, multi_rs_windows='', multi_rs_as_of=None)
    raw = round(sum(round(value, 2) for value in values) / len(values), 2)
    normalized = round(100 / (1 + math.exp(-raw / 12)), 2)
    return dict(multi_rs=normalized, multi_rs_raw=raw,
                multi_rs_windows='/'.join(windows), multi_rs_as_of=min(dates))


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
    if not rules.pyramiding_enabled:
        values.pop('pyramiding_enabled',None);values.pop('pyramiding_max_additions',None)
    if not rules.pyramiding_review_enabled:
        from .pyramiding import SETTINGS
        for key in SETTINGS:values.pop(key,None)
    if rules.initial_stop_method=='atr' and rules.initial_stop_reference=='signal_close':
        from .initial_stop import SETTINGS
        for key in SETTINGS:values.pop(key,None)
    if rules.stop_limit_enabled:values.pop('stop_limit_enabled',None)
    if not rules.rs_exit_enabled:
        from .rs_exit import RS_EXIT_SETTINGS
        for key in RS_EXIT_SETTINGS:values.pop(key,None)
    if not rules.market_units_enabled:
        from .market_units import MARKET_UNIT_SETTINGS
        for key in MARKET_UNIT_SETTINGS:
            values.pop(key,None)
    if values.get('entry_method','breakout')=='breakout':
        from .setup import SETUP_SETTINGS
        for key in SETUP_SETTINGS:
            values.pop(key,None)
    if not rules.setup_52w_enabled:
        values.pop('setup_52w_enabled',None)
    if values.get('rs_method', 'percentile') == 'percentile':
        for key in ['rs_method', 'mansfield_weeks', 'min_mansfield', 'mansfield_benchmark']:
            values.pop(key, None)
    elif values.get('mansfield_benchmark') == 'universe':
        # Compatibility with Mansfield accounts created before benchmark selection existed.
        values.pop('mansfield_benchmark', None)
    return values
