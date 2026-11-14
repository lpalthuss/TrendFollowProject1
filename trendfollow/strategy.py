"""Explicit baseline hypotheses, not a reproduction of the video's full method."""

from .config import Rules
import math

import numpy as np
import pandas as pd

from .relative_strength import mansfield_benchmark_symbol
from .indicators import price_indicators, required_history, wilder_atr_series
from .signals import apply_entry_signals, apply_signal_policies
from .setup import sector_features, setup_features


def validate_prices(prices: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    if not required.issubset(prices.columns) or prices.empty:
        raise ValueError(
            "가격 CSV에는 date,symbol,open,high,low,close,volume이 필요합니다."
        )
    p = prices.copy()
    if p["symbol"].isna().any() or (p["symbol"].astype(str).str.strip() == "").any():
        raise ValueError("종목코드는 비어 있을 수 없습니다.")
    p["symbol"] = p["symbol"].astype(str)
    p["date"] = pd.to_datetime(p["date"], format="%Y-%m-%d", errors="raise")
    if p["date"].isna().any():
        raise ValueError("거래일이 누락되었습니다.")
    if p["date"].dt.tz is not None or not (p["date"] == p["date"].dt.normalize()).all():
        raise ValueError("date는 시간·시간대 없는 거래일이어야 합니다.")
    if p.duplicated(["symbol", "date"]).any():
        raise ValueError("동일 종목·거래일의 중복 행이 있습니다.")
    cols = ["open", "high", "low", "close", "volume"]
    p[cols] = p[cols].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(p[cols].to_numpy()).all():
        raise ValueError("가격·거래량에 결측 또는 무한대가 있습니다.")
    if (p[["open", "high", "low", "close"]] <= 0).any().any() or (p.volume < 0).any():
        raise ValueError(
            "가격은 양수, 거래량은 0 이상이어야 합니다. 거래정지 행을 확인하세요."
        )
    if (
        (p.high < p[["open", "close", "low"]].max(axis=1))
        | (p.low > p[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError("OHLC 가격 범위가 일치하지 않습니다.")
    return p.sort_values(["symbol", "date"]).reset_index(drop=True)


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> float:
    if len(frame) < period:
        return math.nan
    return float(wilder_atr_series(frame, period).iloc[-1])


def screen(
    prices: pd.DataFrame, universe: pd.DataFrame, as_of: str, rules: Rules = Rules()
) -> pd.DataFrame:
    rules.validate()
    p = validate_prices(prices)
    asof = pd.Timestamp(as_of)
    if asof.tz is not None or asof != asof.normalize():
        raise ValueError("as-of는 YYYY-MM-DD 거래일이어야 합니다.")
    # Truncate before all calculations, including resampling and ranking.
    p = p[p.date <= asof]
    if not {"symbol", "name", "sector", "benchmark"}.issubset(universe.columns):
        raise ValueError("universe에는 symbol,name,sector,benchmark가 필요합니다.")
    if (
        universe.empty
        or universe.symbol.duplicated().any()
        or universe.isna().any().any()
    ):
        raise ValueError("universe가 비어 있거나 중복·결측이 있습니다.")
    frames = {str(s): f.set_index("date") for s, f in p.groupby("symbol")}
    required_mrs = {
        mansfield_benchmark_symbol(item, rules) for item in universe.to_dict("records")
    }
    if rules.mansfield_benchmark == "market_index" and required_mrs - set(frames):
        raise ValueError(
            "Mansfield 지수 일봉이 없습니다: "
            + ",".join(sorted(required_mrs - set(frames)))
            + ". index-data로 수집하세요."
        )
    rows = []
    sectors = None
    if rules.entry_method == "setup":
        sf = sector_features(p, universe, rules)
        sectors = sf[sf.date == asof].set_index("symbol")
    for item in universe.to_dict("records"):
        symbol, benchmark = str(item["symbol"]), str(item["benchmark"])
        mrs_benchmark = mansfield_benchmark_symbol(item, rules)
        f, b = frames.get(symbol), frames.get(benchmark)
        row = dict(
            item,
            signal=False,
            signal_rs=False,
            signal_mansfield=False,
            rs_method=rules.rs_method,
            mansfield_rs=math.nan,
            mansfield_change_1w=math.nan,
            mansfield_observation_date=None,
            mansfield_benchmark=mrs_benchmark,
            entry_method=rules.entry_method,
            reasons="",
            quantity=0,
        )
        if f is None or b is None or f.empty or b.empty:
            rows.append(dict(row, reasons="missing_prices_or_benchmark"))
            continue
        if f.index[-1] != asof or b.index[-1] != asof:
            rows.append(dict(row, reasons="stale_prices_or_benchmark"))
            continue
        # Benchmark defines expected sessions: no forward filling suspended/missing stocks.
        calendar = b.index[b.index <= asof]
        needed = required_history(rules)
        if len(f) < needed or len(b) < rules.rs_days + 1:
            rows.append(dict(row, reasons="insufficient_history"))
            continue
        if not calendar[-needed:].equals(f.index[-needed:]):
            rows.append(dict(row, reasons="missing_or_misaligned_sessions"))
            continue
        computed = price_indicators(f, b, frames.get(mrs_benchmark), rules)
        latest = computed.iloc[-1]
        close = float(f.close.iloc[-1])
        ret = float(latest.return_126d)
        trend = bool(latest.weekly_trend)
        high = float(latest.prior_breakout_high)
        high_52w = float(latest.prior_52w_high)
        # Preserve the snapshot API's zero-volume presentation for an all-zero base.
        volume_ratio = (
            float(latest.volume_ratio) if f.volume.iloc[-21:-1].mean() > 0 else 0
        )
        turnover = float(latest.avg_turnover)
        atr = float(latest.atr)
        stop = float(latest.stop_price)
        if rules.entry_method == "setup":
            row.update(setup_features(f, rules, calendar=b.index).iloc[-1].to_dict())
            if symbol in sectors.index:
                row.update(sectors.loc[symbol].drop(labels=["date"]).to_dict())
            else:
                row.update(
                    sector_peer_count=0,
                    sector_up_fraction=math.nan,
                    sector_excess_return=math.nan,
                    sector_action=False,
                )
        mb = frames.get(mrs_benchmark)
        if (
            mb is not None
            and not mb.empty
            and mb.index[-1] == asof
            and pd.notna(latest.mansfield_observation_date)
        ):
            row.update(
                mansfield_rs=float(latest.mansfield_rs),
                mansfield_change_1w=float(latest.mansfield_change_1w),
                mansfield_observation_date=latest.mansfield_observation_date,
            )
        row.update(
            reference_price=close,
            stop_price=stop,
            atr=atr,
            atr_pct=100 * atr / close,
            return_126d=ret,
            excess_return=float(latest.excess_return),
            weekly_trend=bool(trend),
            prior_breakout_high=high,
            breakout=bool(close > high),
            prior_52w_high=high_52w,
            high52_breakout=None if math.isnan(high_52w) else bool(close > high_52w),
            volume_ratio=volume_ratio,
            avg_turnover=turnover,
        )
        rows.append(row)
    result = pd.DataFrame(rows)
    from .initial_stop import planning_stops

    result = planning_stops(result, rules)
    if rules.market_units_enabled:
        from .market_units import market_unit_features

        market = market_unit_features(p, universe, rules)
        result = result.merge(
            market[market.date == asof].drop(columns="date"),
            on="symbol",
            how="left",
            validate="one_to_one",
        )
    if "return_126d" not in result:
        result["rs_percentile"] = np.nan
        result["allocation_score"] = np.nan
        return apply_signal_policies(result, rules, as_of)
    # Rank only in the explicitly supplied analysis universe; not a market-wide RS claim.
    result["rs_percentile"] = result.return_126d.rank(method="average", pct=True) * 100
    result = apply_entry_signals(result, rules, include_reasons=True)
    result = apply_signal_policies(result, rules, as_of)
    return result.sort_values(
        ["allocation_score", "symbol"], ascending=[False, True], na_position="last"
    )
