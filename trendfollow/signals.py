"""Entry conditions and post-signal policies shared by every calculation path."""

import pandas as pd


def entry_tests(frame, rules):
    tests = {
        "weak_weekly_trend": frame.weekly_trend.eq(True),
        "weak_volume": frame.volume_ratio >= rules.volume_multiple,
        "low_liquidity": frame.avg_turnover >= rules.min_turnover,
        "invalid_stop": (frame.stop_price > 0)
        & (frame.stop_price < frame.reference_price),
    }
    if rules.entry_method == "breakout":
        tests["no_breakout"] = frame.reference_price > frame.prior_breakout_high
    elif rules.setup_52w_enabled:
        setup_route = (
            frame.setup_ready.eq(True)
            & frame.setup_trigger.eq(True)
            & frame.setup_invalidated.eq(False)
            & frame.setup_volume_ratio.le(rules.setup_volume_contraction)
            & frame.setup_range_ratio.le(rules.setup_range_contraction)
        )
        tests["no_setup_or_52w_breakout"] = setup_route | frame.high52_breakout.eq(True)
        tests["weak_sector_action"] = (
            pd.Series(True, index=frame.index)
            if not rules.sector_require_action
            else frame.sector_action.eq(True)
        )
    else:
        tests.update(
            setup_not_ready=frame.setup_ready.eq(True),
            no_setup_trigger=frame.setup_trigger.eq(True),
            unhealthy_pullback=frame.setup_invalidated.eq(False),
            setup_volume_not_contracted=frame.setup_volume_ratio
            <= rules.setup_volume_contraction,
            setup_range_not_contracted=frame.setup_range_ratio
            <= rules.setup_range_contraction,
            weak_sector_action=(
                pd.Series(True, index=frame.index)
                if not rules.sector_require_action
                else frame.sector_action.eq(True)
            ),
        )
    return tests


def apply_entry_signals(frame, rules, include_reasons=False):
    f = frame.copy()
    tests = entry_tests(f, rules)
    common = pd.Series(True, index=f.index)
    for passed in tests.values():
        common &= passed
    if "eligible_history" in f:
        common &= f.eligible_history.eq(True)
    if "reasons" in f:
        common &= f.reasons.eq("")
    f["signal_rs"] = common & (f.rs_percentile >= rules.min_rs) & (f.excess_return > 0)
    f["signal_mansfield"] = common & (f.mansfield_rs > rules.min_mansfield)
    f["signal"] = f.signal_rs if rules.rs_method == "percentile" else f.signal_mansfield
    f["signal_entry_path"] = ""
    if rules.entry_method == "setup":
        setup_pass = f.signal & f.setup_entry.eq(True)
        f.loc[setup_pass, "signal_entry_path"] = "셋업 돌파"
        if rules.setup_52w_enabled:
            high52_pass = f.signal & f.high52_breakout.eq(True)
            f.loc[high52_pass, "signal_entry_path"] = "52주 신고가"
            f.loc[setup_pass & high52_pass, "signal_entry_path"] = "셋업 돌파 + 52주 신고가"
    else:
        f.loc[f.signal, "signal_entry_path"] = f"{rules.breakout_days}일 고가 돌파"
    f["allocation_score"] = (
        f.rs_percentile if rules.rs_method == "percentile" else f.mansfield_rs
    )
    if include_reasons:
        for i, row in f.iterrows():
            if row.reasons:
                continue
            reasons = [name for name, passed in tests.items() if not passed.loc[i]]
            if rules.rs_method == "percentile":
                if not row.rs_percentile >= rules.min_rs:
                    reasons.append("weak_rs")
                if not row.excess_return > 0:
                    reasons.append("benchmark_underperformance")
            elif pd.isna(row.mansfield_rs):
                reasons.append("insufficient_mansfield_history")
            elif not row.mansfield_rs > rules.min_mansfield:
                reasons.append("weak_mansfield")
            f.at[i, "reasons"] = ",".join(reasons)
    return f


def apply_signal_policies(frame, rules, as_of=None):
    from .rs_exit import exit_columns
    from .stop_limit import apply_stop_limit
    from .pyramiding import review_columns

    return review_columns(
        apply_stop_limit(exit_columns(frame, rules, as_of), rules), rules
    )
