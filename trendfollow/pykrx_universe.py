"""Historical market candidate snapshots from pykrx's dated KRX stock data."""

from contextlib import redirect_stderr, redirect_stdout
import io
import math
from pathlib import Path
import re

import pandas as pd
import requests

from .krx_bulk import configure_krx_credentials


BENCHMARKS = {"KOSPI": "069500", "KOSDAQ": "229200"}
PREFERRED = re.compile(r"(?:[0-9]우|우)(?:B|C|D)?$")
SPAC = re.compile(r"스팩|SPAC|기업인수목적", re.IGNORECASE)
REQUIRED_COLUMNS = {"시가", "거래량", "거래대금"}


def fetch_historical_universe(root, day, threshold, progress=None):
    """Use dated membership and trading; names come from pykrx's ticker cache."""
    configure_krx_credentials(Path(root) / ".env")
    try:
        # pykrx may print the login ID on import or on failed requests.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from pykrx import stock
    except ImportError:
        raise ValueError('pykrx 후보군 조회에는 python -m pip install -e ".[krx]"가 필요합니다.') from None
    members, excluded = [], []
    date = day.replace("-", "")
    for market, benchmark in BENCHMARKS.items():
        if progress:
            progress(f"{day} {market} pykrx 종목 목록·거래정보 조회 중")
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                tickers = stock.get_market_ticker_list(date, market)
                bars = stock.get_market_ohlcv_by_ticker(date, market)
        except requests.RequestException:
            raise ValueError(f"{day} {market} pykrx KRX 연결에 실패했습니다. 네트워크와 KRX 로그인을 확인하세요.") from None
        except Exception:
            raise ValueError(f"{day} {market} pykrx 조회에 실패했습니다. KRX 로그인과 서비스 상태를 확인하세요.") from None
        if (not tickers or bars is None or bars.empty or not REQUIRED_COLUMNS.issubset(bars.columns)
                or len(tickers) < 100 or len(bars) < 100 or not bars.index.is_unique):
            raise ValueError(f"{day} {market} pykrx 종목·거래 응답이 비어 있거나 불완전합니다. 저장하지 않았습니다.")
        bars = bars.copy()
        bars.index = bars.index.map(str)
        listed = {str(ticker).strip() for ticker in tickers}
        trading = {str(ticker).strip() for ticker in bars.index}
        if len(listed & trading) < len(listed) // 2:
            raise ValueError(f"{day} {market} pykrx 종목 목록과 거래 응답이 일치하지 않습니다. 저장하지 않았습니다.")
        for ticker in sorted(listed):
            if not re.fullmatch(r"[0-9A-Z]{6}", ticker):
                continue
            bar = bars.loc[ticker] if ticker in trading else None
            turnover = pd.to_numeric(bar["거래대금"], errors="coerce") if bar is not None else 0
            volume = pd.to_numeric(bar["거래량"], errors="coerce") if bar is not None else 0
            open_price = pd.to_numeric(bar["시가"], errors="coerce") if bar is not None else 0
            reasons = []
            if (bar is None or not pd.notna(turnover) or not pd.notna(volume) or not pd.notna(open_price)
                    or not all(math.isfinite(float(value)) for value in (turnover, volume, open_price))):
                reasons.append("missing_trading_data")
                turnover = 0
            elif volume <= 0 or open_price <= 0:
                reasons.append("no_regular_trading")
            if turnover < threshold:
                reasons.append("daily_turnover_below_threshold")
            # Name lookup uses pykrx's cached listed/delisted ticker table, not a dated name snapshot.
            if not reasons:
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        name = str(stock.get_market_ticker_name(ticker) or "").strip()
                except Exception:
                    raise ValueError(f"{day} pykrx 종목명 조회에 실패했습니다. 저장하지 않았습니다.") from None
                if not name:
                    reasons.append("missing_name")
                if PREFERRED.search(name):
                    reasons.append("preferred_or_other_class")
                if SPAC.search(name):
                    reasons.append("spac_name")
            else:
                name = ticker
            row = dict(symbol=ticker, name=name, market=market,
                       sector=f"UNCLASSIFIED:{ticker}", benchmark=benchmark,
                       recent_turnover_proxy=float(turnover))
            (excluded if reasons else members).append(
                dict(row, reasons=",".join(reasons)) if reasons else row
            )
    if not members:
        raise ValueError(f"{day} pykrx 후보군이 비어 있습니다. 저장하지 않았습니다.")
    frame = pd.DataFrame(members)
    if frame.symbol.duplicated().any():
        raise ValueError(f"{day} pykrx 응답에 중복 종목이 있습니다. 저장하지 않았습니다.")
    return frame, pd.DataFrame(excluded)
