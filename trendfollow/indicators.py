"""Shared price indicators used by snapshot screening and historical simulation."""

import numpy as np
import pandas as pd
from .relative_strength import mansfield_weekly


def required_history(rules):
    return max(
        rules.rs_days + 1,
        (rules.weekly_ma + rules.weekly_slope + 2) * 5,
        rules.breakout_days + 1 if rules.entry_method == "breakout" else 1,
        rules.atr_days,
        21,
    )


def wilder_atr_series(frame, period):
    previous = frame.close.shift()
    tr = pd.concat(
        [
            frame.high - frame.low,
            (frame.high - previous).abs(),
            (frame.low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    values = np.full(len(frame), np.nan)
    if len(frame) >= period:
        values[period - 1] = tr.iloc[:period].mean()
        for i in range(period, len(frame)):
            values[i] = (values[i - 1] * (period - 1) + tr.iloc[i]) / period
    return pd.Series(values, index=frame.index)


def price_indicators(frame, benchmark, mansfield_benchmark, rules):
    f = frame.copy()
    f["atr"] = wilder_atr_series(f, rules.atr_days)
    f["return_126d"] = f.close.pct_change(rules.rs_days, fill_method=None)
    f["excess_return"] = f.return_126d - benchmark.close.pct_change(
        rules.rs_days, fill_method=None
    ).reindex(f.index)
    weekly = f.close.resample("W-FRI").last().dropna()
    average = weekly.rolling(rules.weekly_ma).mean()
    trend = (weekly > average) & (average > average.shift(rules.weekly_slope))
    f["weekly_trend"] = trend.reindex(f.index, method="ffill").eq(True)
    f["prior_breakout_high"] = f.high.shift().rolling(rules.breakout_days).max()
    f["volume_ratio"] = f.volume / f.volume.shift().rolling(20).mean().replace(
        0, np.nan
    )
    f["avg_turnover"] = (f.close * f.volume).shift().rolling(20).mean()
    f["stop_price"] = f.close - rules.stop_atr * f.atr
    if mansfield_benchmark is None:
        f["mansfield_observation_date"] = pd.NaT
        f["mansfield_rs"] = np.nan
        f["mansfield_change_1w"] = np.nan
    else:
        weekly_mrs = mansfield_weekly(
            f.close,
            mansfield_benchmark.close.reindex(benchmark.index),
            rules.mansfield_weeks,
        )
        mrs = weekly_mrs.reindex(f.index, method="ffill")
        f["mansfield_observation_date"] = pd.Series(
            weekly_mrs.index, index=weekly_mrs.index
        ).reindex(f.index, method="ffill")
        f["mansfield_rs"] = mrs.mansfield_rs
        f["mansfield_change_1w"] = mrs.mansfield_change_1w
        available = (
            pd.Series(True, index=mansfield_benchmark.index).reindex(f.index).eq(True)
        )
        f.loc[~available, ["mansfield_rs", "mansfield_change_1w"]] = np.nan
    return f
