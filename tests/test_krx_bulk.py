import json
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from trendfollow.krx_bulk import acquire_missing_sessions, configure_krx_credentials, fetch_session
from trendfollow.workflow import daily_run


def frame(prices):
    return pd.DataFrame.from_dict(
        {ticker: {"시가": price, "고가": price, "저가": price,
                  "종가": price, "거래량": 1000} for ticker, price in prices.items()},
        orient="index",
    )


def test_krx_bulk_adds_gap_without_revising_prior_bars(tmp_path, monkeypatch):
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol,benchmark\n005930,069500\n", encoding="utf-8")
    root = tmp_path / "history"
    root.mkdir()
    pd.DataFrame([dict(date="2026-11-10", symbol=symbol, open=price,
                       high=price, low=price, close=price, volume=1000)
                  for symbol, price in [("005930", 100), ("069500", 200)]]).to_csv(root / "prices.csv", index=False)
    (root / "manifest.json").write_text(json.dumps({"source": "NAVER public chart"}), encoding="utf-8")
    calls = []
    stock = SimpleNamespace(
        get_market_ohlcv_by_ticker=lambda date, market: (calls.append((date, market)) or frame({"005930": 110})),
        get_etf_ohlcv_by_ticker=lambda date: frame({"069500": 210}),
    )
    monkeypatch.setitem(sys.modules, "pykrx", SimpleNamespace(stock=stock))
    progress = []
    manifest = acquire_missing_sessions(universe, root, ["2026-11-11"], progress=lambda *args: progress.append(args))
    saved = pd.read_csv(root / "prices.csv", dtype={"symbol": str})
    assert len(saved) == 4
    assert saved.loc[saved.date.eq("2026-11-10") & saved.symbol.eq("005930"), "close"].iloc[0] == 100
    assert saved.loc[saved.date.eq("2026-11-11") & saved.symbol.eq("005930"), "close"].iloc[0] == 110
    assert manifest["price_sources"][-1]["sessions"] == ["2026-11-11"]
    assert calls == [("20261111", "ALL"), ("20261110", "ALL")]
    assert any(entry[1:] == (1, 1) for entry in progress)
    assert manifest["source_comparison"]["overlap_checked"] == 2


def test_krx_bulk_missing_symbol_keeps_prior_file(tmp_path, monkeypatch):
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol,benchmark\n005930,069500\n", encoding="utf-8")
    root = tmp_path / "history"
    root.mkdir()
    prices = root / "prices.csv"
    prices.write_text("date,symbol,open,high,low,close,volume\n2026-11-10,005930,100,100,100,100,1\n", encoding="utf-8")
    before = prices.read_bytes()
    stock = SimpleNamespace(
        get_market_ohlcv_by_ticker=lambda *_: frame({"005930": 110}),
        get_etf_ohlcv_by_ticker=lambda *_: frame({}),
    )
    monkeypatch.setitem(sys.modules, "pykrx", SimpleNamespace(stock=stock))
    with pytest.raises(ValueError, match="069500"):
        acquire_missing_sessions(universe, root, ["2026-11-11"])
    assert prices.read_bytes() == before


def test_daily_krx_choice_fails_early_without_login(tmp_path, monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    with pytest.raises(ValueError, match="KRX_ID.*KRX_PW"):
        daily_run(tmp_path / "universe.csv", tmp_path / "history",
                  tmp_path / "state.json", "2026-11-13",
                  env_file=tmp_path / "missing.env", price_source="pykrx")


def test_krx_credentials_load_from_env_file_with_process_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KRX_ID=file-user\nKRX_PW=file-password\n", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setenv("KRX_ID", "process-user")
        patch.setenv("KRX_PW", "")
        configure_krx_credentials(env_file)
        assert __import__("os").environ["KRX_ID"] == "process-user"
        assert __import__("os").environ["KRX_PW"] == "file-password"


def test_krx_login_output_is_not_written_to_job_log(monkeypatch, capsys):
    def noisy_fetch(*_):
        print("login ID: private-user")
        return frame({"005930": 100})
    stock = SimpleNamespace(get_market_ohlcv_by_ticker=noisy_fetch,
                            get_etf_ohlcv_by_ticker=lambda *_: frame({}))
    monkeypatch.setitem(sys.modules, "pykrx", SimpleNamespace(stock=stock))
    assert "005930" in fetch_session("2026-11-13", {"005930"})
    assert "private-user" not in capsys.readouterr().out
