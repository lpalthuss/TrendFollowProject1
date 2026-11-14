import json

import pandas as pd

from trendfollow.demo import generate
from trendfollow.workflow import paper_checkpoint


def test_existing_account_continues_with_a_dated_universe_without_replaying_past(tmp_path):
    demo = tmp_path / "demo"
    generate(demo)
    prices = demo / "prices.csv"
    rounded = pd.read_csv(prices, dtype={"symbol": str})
    rounded[["open", "high", "low", "close"]] = rounded[["open", "high", "low", "close"]].round(2)
    rounded.to_csv(prices, index=False)
    baseline = demo / "universe.csv"
    state = tmp_path / "account" / "state.json"
    initial = paper_checkpoint(prices, baseline, "2026-11-06", state)
    assert initial["status"] == "processed"
    prior_signals = (state.parent / "2026-11-06" / "signals.csv").read_bytes()
    baseline_bytes = baseline.read_bytes()

    old_prices = pd.read_csv(prices, dtype={"symbol": str})
    next_day = old_prices.groupby("symbol").tail(1).copy()
    next_day["date"] = "2026-11-09"
    new_stock = pd.concat([old_prices, next_day])
    new_stock = new_stock[new_stock.symbol.eq("DEMO02")].copy()
    new_stock["symbol"] = "DEMO05"
    pd.concat([old_prices, next_day, new_stock]).sort_values(["symbol", "date"]).to_csv(prices, index=False)

    active = pd.read_csv(baseline, dtype={"symbol": str, "benchmark": str})
    active = active[~active.symbol.eq("DEMO01")].copy()
    added = active[active.symbol.eq("DEMO02")].copy()
    added["symbol"] = "DEMO05"
    added["name"] = "새 종목"
    active = pd.concat([active, added], ignore_index=True)
    active["candidate_active"] = True
    dated = tmp_path / "2026-11-09-universe.csv"
    active.to_csv(dated, index=False)

    result = paper_checkpoint(prices, dated, "2026-11-09", state,
                              fingerprint_universe_path=baseline)
    assert result["status"] == "processed"
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["last_date"] == "2026-11-09"
    assert saved["decision_universe_path"] == str(dated.resolve())
    new_signals = pd.read_csv(state.parent / "2026-11-09" / "signals.csv", dtype={"symbol": str})
    assert "DEMO05" in set(new_signals.symbol) and "DEMO01" not in set(new_signals.symbol)
    assert (state.parent / "2026-11-06" / "signals.csv").read_bytes() == prior_signals
    assert baseline.read_bytes() == baseline_bytes
