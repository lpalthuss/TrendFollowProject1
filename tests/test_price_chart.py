from trendfollow.price_chart import chart_html


def test_chart_bundles_local_library_and_keeps_ohlcv_and_atr():
    html = chart_html([
        dict(date="2026-11-17", open=100, high=105, low=98, close=102,
             volume=1234, volume_ratio=1.7, atr=4.5, atr_up_multiple=1.2, atr_down_multiple=0.5),
    ])
    assert "LightweightCharts.createChart" in html
    assert '"time":"2026-11-17"' in html
    assert '"volume":1234.0' in html
    assert '"atr_up_multiple":1.2' in html
    assert '"volume_ratio":1.7' in html
    assert '직전 20일 평균 대비' in html
    assert 'data-months="15"' in html
    assert 'id="legend"' in html
    assert "cdn.jsdelivr.net" not in html


def test_chart_data_cannot_end_script_element():
    html = chart_html([
        dict(date="2026-11-17</script>", open=100, high=105, low=98, close=102),
    ])
    assert '"time":"2026-11-17"' in html
    assert "2026-11-17</script>" not in html



def test_provisional_bar_requires_comparable_previous_close():
    import pandas as pd
    import pytest
    from trendfollow.price_chart import provisional_bar

    history = [dict(date='2026-11-18', close=100, atr=4)]
    quotes = pd.DataFrame([dict(date='2026-11-18', close=100),
                           dict(date='2026-11-19', open=102, high=106, low=101, close=104, volume=310)])
    preview = provisional_bar(history, quotes, '2026-11-19', 14)
    assert preview['provisional'] and preview['volume'] == 310
    assert preview['atr_down_multiple'] == 0
    assert preview['atr'] == pytest.approx((4*13+6)/14)
    quotes.loc[0, 'close'] = 105
    with pytest.raises(ValueError, match='종가 기준'):
        provisional_bar(history, quotes, '2026-11-19', 14)

def test_period_candles_and_daily_moving_average_across_year_boundary():
    from trendfollow.price_chart import chart_periods

    rows = [dict(time=day, open=10+i, high=14+i, low=9+i, close=12+i, volume=5+i)
            for i, day in enumerate(['2024-12-30','2024-12-31','2025-01-02','2025-01-03',
                                     '2025-01-06','2025-01-07','2025-01-08','2025-01-09',
                                     '2025-01-10','2025-01-13'])]
    result = chart_periods(rows)
    week = result['week'][0]
    assert (week['first'], week['time']) == ('2024-12-30','2025-01-03')
    assert (week['open'], week['high'], week['low'], week['close'], week['volume']) == (10,17,9,15,26)
    assert len(result['month']) == 2
    assert result['month'][0].get('ma10') is None
    assert result['month'][-1]['ma10'] is None
    assert result['week'][-1]['ma10'] is None
    assert result['day'][-1]['ma10'] == 16.5

def test_period_averages_include_hidden_history_and_count_each_period_once():
    import pandas as pd
    from trendfollow.price_chart import chart_periods

    days = pd.bdate_range('2013-01-01', '2025-06-09')
    rows = [dict(time=d.date().isoformat(), warmup=i<len(days)-1, open=i+1, high=i+1,
                 low=i+1, close=i+1, volume=1) for i,d in enumerate(days)]
    result = chart_periods(rows)
    closes = pd.Series(range(1,len(days)+1), index=days)
    for period, prices in [('day', closes), ('week', closes.resample('W-FRI').last()),
                           ('month', closes.resample('ME').last())]:
        assert len(result[period]) == 1
        for n in (10,20,60,120):
            assert result[period][0][f'ma{n}'] == prices.tail(n).mean()
