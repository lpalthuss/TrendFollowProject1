from trendfollow.intraday_hourly import HourlyQuotes


def test_hourly_quote_marks_old_sample_unusable_and_keeps_market_separate():
    hourly = HourlyQuotes("2026-11-13", ["005930"])
    hourly.tick(dict(symbol="005930", timestamp="2026-11-13T09:59:30+09:00",
                     market="krx", price=100, cumulative_volume=1000,
                     previous_close=90), "2026-11-13T09:59:31+09:00")
    hourly.decision(dict(kind="signal_state", symbol="005930",
                         timestamp="2026-11-13T09:59:35+09:00", market="krx",
                         breakout_present=True, volume_pass=True, entry_confirmed=True))
    row = hourly.snapshot("2026-11-13T10:00:00+09:00", True)[0]
    assert row["valid"] and row["entry_confirmed"] is True
    hourly.tick(dict(symbol="005930", timestamp="2026-11-13T09:59:40+09:00",
                     market="krx", price=100, cumulative_volume=1100,
                     previous_close=90), "2026-11-13T09:59:41+09:00")
    assert hourly.snapshot("2026-11-13T10:00:00+09:00", True)[0]["entry_confirmed"] is True
    hourly.tick(dict(symbol="005930", timestamp="2026-11-13T10:01:00+09:00",
                     market="krx", price=101, cumulative_volume=1200,
                     previous_close=90), "2026-11-13T10:01:01+09:00")
    rows = hourly.snapshot("2026-11-13T10:05:00+09:00", True)
    assert rows[0]["valid"] and not rows[1]["valid"]
    hourly.tick(dict(symbol="005930", timestamp="2026-11-13T16:10:00+09:00",
                     market="nxt_after", price=102, cumulative_volume=1400,
                     previous_close=90), "2026-11-13T16:10:01+09:00")
    assert hourly.snapshot("2026-11-13T16:10:05+09:00", True)[-1]["market"] == "nxt_after"
