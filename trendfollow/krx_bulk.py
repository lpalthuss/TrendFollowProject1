"""KRX per-session bulk OHLCV for gaps in an existing research series."""

from datetime import datetime
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import dotenv_values

from .price_history import MissingDailyBars, PriceHistory
from .research_store import publish_price_csv, recover_research, write_json


FIELDS = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
INDEX_CODES = {"KOSPI": "1001", "KOSDAQ": "2001"}


def configure_krx_credentials(env_file):
    """Load only KRX credentials; OS environment overrides the local .env file."""
    values = dotenv_values(Path(env_file), interpolate=False)
    credentials = {key: os.environ.get(key) or values.get(key) for key in ("KRX_ID", "KRX_PW")}
    if not all(credentials.values()):
        raise ValueError("pykrx KRX 조회에는 .env 또는 환경변수에 KRX_ID와 KRX_PW가 필요합니다.")
    os.environ.update(credentials)


def fetch_session(day, symbols, include_market_indices=False):
    date = day.replace("-", "")
    try:
        # pykrx prints the login ID even during import; do not put it in UI logs.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from pykrx import stock
            stock_rows = _rows(stock.get_market_ohlcv_by_ticker(date, "ALL"), day, symbols)
            etf_rows = _rows(stock.get_etf_ohlcv_by_ticker(date), day, symbols)
            index_rows = []
            if include_market_indices:
                for market in ("KOSPI", "KOSDAQ"):
                    index_rows += _rows(stock.get_index_ohlcv_by_ticker(date, market), day,
                                        symbols, {INDEX_CODES[market]: market})
    except ImportError:
        raise ValueError('KRX 일괄 조회에는 python -m pip install -e ".[krx]"가 필요합니다.') from None
    return {row["symbol"]: row for row in stock_rows + etf_rows + index_rows}


def _rows(frame, session, symbols, index_codes=None):
    """Normalize only requested KRX tickers; keep raw KRX OHLCV conventions."""
    if frame is None or frame.empty:
        return []
    if not set(FIELDS).issubset(frame.columns):
        raise ValueError(f"{session} KRX 일괄 응답의 OHLCV 필드가 부족합니다.")
    index_codes = index_codes or {}
    rows = []
    for ticker, values in frame.iterrows():
        symbol = index_codes.get(str(ticker), str(ticker).zfill(6))
        if symbol not in symbols:
            continue
        row = {"date": session, "symbol": symbol}
        for original, field in FIELDS.items():
            value = pd.to_numeric(values[original], errors="coerce")
            if pd.isna(value) or value < 0:
                raise ValueError(f"{session} {symbol} KRX {original} 값이 유효하지 않습니다.")
            row[field] = float(value)
        rows.append(row)
    return rows


def benchmark_sessions(symbol, start, end):
    try:
        from pykrx import stock
    except ImportError:
        raise ValueError('KRX 일봉 조회에는 python -m pip install -e ".[krx]"가 필요합니다.') from None
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        frame = stock.get_market_ohlcv_by_date(start.replace("-", ""), end.replace("-", ""), symbol)
    return pd.DatetimeIndex(frame.index).strftime("%Y-%m-%d").tolist()


def acquire_missing_sessions(universe_path, output_dir, sessions, include_market_indices=False, progress=None, gaps=None):
    """Append missing sessions atomically. Never revise an existing session implicitly."""
    try:
        from pykrx import stock
    except ImportError:
        raise ValueError('KRX 일괄 조회에는 python -m pip install -e ".[krx]"가 필요합니다.') from None
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    recover_research(root)
    prices_path = root / "prices.csv"
    if not prices_path.is_file():
        raise ValueError("pykrx 일괄 조회는 저장된 과거 일봉을 바탕으로 누락 거래일만 채웁니다. 먼저 기존 일봉을 준비하세요.")
    universe = pd.read_csv(universe_path, dtype={"symbol": str, "benchmark": str})
    symbols = set(universe.symbol) | set(universe.benchmark)
    if include_market_indices:
        symbols.update(INDEX_CODES)
    sessions = sorted(set(sessions))
    if not sessions:
        raise ValueError("추가할 거래일이 없습니다.")
    if any(not isinstance(day, str) or pd.Timestamp(day).strftime("%Y-%m-%d") != day for day in sessions):
        raise ValueError("KRX 조회 거래일 형식이 올바르지 않습니다.")
    required = {day: symbols.copy() for day in sessions}
    for symbol, days in (gaps or {}).items():
        for day in days:
            required.setdefault(day, set()).add(symbol)
    history = PriceHistory(prices_path, required)
    old = history.window
    missing = [day for day in sorted(required) if not required[day].issubset(set(old.loc[old.date.eq(day), "symbol"]))]
    if not missing:
        return {"status": "already_present", "source": "existing", "sessions": [], "errors": []}
    rows = []
    comparisons = []
    overlap_checked = 0
    unresolved = []
    for number, day in enumerate(missing, 1):
        if progress:
            progress(f"KRX 일괄 조회 중 · {day}", number - 1, len(missing))
        try:
            by_symbol = fetch_session(day, required[day], include_market_indices)
        except Exception as error:
            needed = required[day] - set(old.loc[old.date.eq(day), "symbol"])
            raise MissingDailyBars(f"{day} KRX 일괄 조회 실패: {error}",
                                   unresolved + [(symbol, day) for symbol in needed]) from error
        prior_rows = old.loc[old.date.eq(day)]
        for previous in prior_rows.to_dict("records"):
            current = by_symbol.get(previous["symbol"])
            if current is None:
                continue
            overlap_checked += 1
            close_delta = abs(float(previous["close"]) - current["close"]) / max(current["close"], 1)
            volume_delta = abs(float(previous["volume"]) - current["volume"]) / max(current["volume"], 1)
            if close_delta > .001 or volume_delta > .05:
                comparisons.append({"date": day, "symbol": previous["symbol"],
                                    "close_difference_ratio": close_delta,
                                    "volume_difference_ratio": volume_delta})
        absent = required[day] - set(by_symbol) - set(old.loc[old.date.eq(day), "symbol"])
        unresolved.extend((symbol, day) for symbol in absent)
        rows.extend(by_symbol.values())
        if progress:
            progress(f"KRX 일괄 조회 중 · {day}", number, len(missing))
    if unresolved:
        symbol, day = unresolved[0]
        raise MissingDailyBars(f"{day} {symbol} 등 KRX 일괄 응답에 일봉 {len(unresolved)}건이 없습니다.", unresolved)
    boundary = [day for day in old.date.unique() if day < missing[0]]
    if boundary:
        prior_day = max(boundary)
        if progress:
            progress(f"기존 자료와 KRX 기준 가격 대조 중 · {prior_day}")
        try:
            prior_market = fetch_session(prior_day, symbols, include_market_indices)
        except Exception as error:
            raise ValueError(f"{prior_day} KRX 기준 가격 대조 실패: {error}") from error
        for previous in old.loc[old.date.eq(prior_day)].to_dict("records"):
            current = prior_market.get(previous["symbol"])
            if current is None:
                continue
            overlap_checked += 1
            close_delta = abs(float(previous["close"]) - current["close"]) / max(current["close"], 1)
            volume_delta = abs(float(previous["volume"]) - current["volume"]) / max(current["volume"], 1)
            if close_delta > .001 or volume_delta > .05:
                comparisons.append({"date": prior_day, "symbol": previous["symbol"],
                                    "close_difference_ratio": close_delta,
                                    "volume_difference_ratio": volume_delta})
    fresh = pd.DataFrame(rows)
    # Existing bars retain their original source and values. KRX only fills gaps.
    present = pd.MultiIndex.from_frame(old[["date", "symbol"]])
    fresh = fresh[~pd.MultiIndex.from_frame(fresh[["date", "symbol"]]).isin(present)]
    prior = {}
    prior_path = root / "manifest.json"
    if prior_path.exists():
        import json
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    attempt_dir = root / "acquisition-attempts"
    attempt_dir.mkdir(exist_ok=True)
    attempt = attempt_dir / (datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12] + ".json")
    manifest = {**prior, "source": "mixed existing research and KRX pykrx bulk",
                "price_sources": [*prior.get("price_sources", []),
                                  {"source": "KRX pykrx bulk", "sessions": missing, "collected_at": stamp,
                                   "price_basis": "KRX daily OHLCV; adjustment may differ from prior source"}],
                "source_comparison": {"overlap_checked": overlap_checked, "difference_count": len(comparisons),
                    "differences": comparisons[:100]},
                "requested_symbols": len(symbols), "collected_symbols": len(history.symbols.union(fresh.symbol)),
                "rows": history.count + len(fresh), "errors": [], "published": True, "status": "published",
                "attempt_report": str(attempt)}
    publish_price_csv(root, lambda handle: history.write_merged(handle, fresh), manifest,
                      on_publish=lambda: write_json(attempt, manifest))
    return manifest
