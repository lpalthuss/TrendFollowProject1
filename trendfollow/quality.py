"""Source comparison with explicit tolerances; agreement is not proof of correctness."""
import pandas as pd

from .strategy import validate_prices


def compare_sources(primary, reference, price_tolerance=.001, volume_tolerance=.05):
    a, b = validate_prices(primary), validate_prices(reference)
    merged = a.merge(b, on=['date', 'symbol'], suffixes=('_primary', '_reference'),
                     how='outer', indicator=True)
    both = merged['_merge'] == 'both'
    merged['price_match'] = both
    summary = {'primary_rows': len(a), 'reference_rows': len(b),
               'missing_sessions': int((~both).sum()), 'price_tolerance': price_tolerance,
               'volume_tolerance': volume_tolerance, 'fields': {}}
    for col in ['open', 'high', 'low', 'close', 'volume']:
        delta = (merged[col+'_primary']-merged[col+'_reference']).abs()
        relative = delta/merged[col+'_reference'].abs().clip(lower=1)
        tolerance = volume_tolerance if col == 'volume' else price_tolerance
        passed = both & (relative <= tolerance)
        merged[col+'_relative_difference'] = relative
        if col == 'volume':
            merged['volume_match'] = passed
        else:
            merged['price_match'] &= passed
        summary['fields'][col] = {'above_tolerance_rows': int((both & ~passed).sum()),
                                 'max_difference_pct': float(relative.max()*100) if both.any() else None}
    summary['price_mismatch_rows'] = int((~merged.price_match).sum())
    summary['volume_mismatch_rows'] = int((~merged.volume_match).sum())
    return summary, merged
