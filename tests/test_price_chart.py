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
