"""Causal market risk caps; separate from trade-outcome units."""
import pandas as pd


MARKET_UNIT_SETTINGS = ('market_units_enabled', 'market_units_benchmark',
                        'market_units_ma_days', 'market_units_strong_cap', 'market_units_weak_cap')


def market_symbol(item, rules):
    if rules.market_units_benchmark == 'universe':
        return str(item['benchmark'])
    market = item.get('market')
    if market not in {'KOSPI', 'KOSDAQ'}:
        raise ValueError('시장 유닛의 지수 기준에는 market(KOSPI/KOSDAQ)이 필요합니다.')
    return market


def market_unit_features(prices, universe, rules):
    """Use signal-day close only; missing current sessions are never forward-filled."""
    frames = {str(s): f.set_index('date').sort_index() for s, f in prices.groupby('symbol')}
    cache, output = {}, []
    for item in universe.to_dict('records'):
        symbol = market_symbol(item, rules)
        calendar_symbol = str(item['benchmark'])
        key = (symbol, calendar_symbol)
        if symbol not in frames or calendar_symbol not in frames:
            raise ValueError('시장 유닛 비교 시세가 없습니다: '+symbol)
        if key not in cache:
            close = frames[symbol].close.reindex(frames[calendar_symbol].index)
            average = close.rolling(rules.market_units_ma_days).mean()
            ready = close.notna() & average.notna()
            strong = ready & (close > average)
            cap = pd.Series(0, index=close.index)
            cap.loc[ready] = rules.market_units_weak_cap
            cap.loc[strong] = rules.market_units_strong_cap
            regime = pd.Series('unavailable', index=close.index)
            regime.loc[ready] = 'weak'
            regime.loc[strong] = 'strong'
            cache[key] = pd.DataFrame({'market_unit_benchmark': symbol,
                'market_unit_close': close, 'market_unit_ma': average,
                'market_unit_regime': regime, 'market_unit_cap': cap})
        f = cache[key].copy()
        f['symbol'] = str(item['symbol'])
        output.append(f.reset_index())
    return pd.concat(output, ignore_index=True)
