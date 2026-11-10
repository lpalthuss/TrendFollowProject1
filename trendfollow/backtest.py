"""Event-driven long-only research simulation. Uses adjusted vendor prices, not broker fills."""

from dataclasses import asdict, replace
import copy
import math

import numpy as np
import pandas as pd

from .risk import Account, Limits, allocate
from .config import Rules
from .relative_strength import mansfield_benchmark_symbol
from .indicators import price_indicators, required_history
from .signals import apply_entry_signals, apply_signal_policies
from .setup import sector_features, setup_features


def clean_research_prices(prices):
    p = prices.copy()
    if not {"symbol", "date", "open", "high", "low", "close", "volume"}.issubset(p):
        raise ValueError("일봉 필드가 누락되었습니다.")
    p["symbol"] = p.symbol.astype(str)
    p["date"] = pd.to_datetime(p.date, format="%Y-%m-%d", errors="raise")
    if p.duplicated(["symbol", "date"]).any():
        raise ValueError("중복 일봉은 자동 제거하지 않습니다.")
    cols = ["open", "high", "low", "close", "volume"]
    p[cols] = p[cols].apply(pd.to_numeric, errors="raise")
    bad = (
        ~np.isfinite(p[cols]).all(axis=1)
        | (p[cols[:-1]] <= 0).any(axis=1)
        | (p.volume < 0)
        | (p.high < p[["open", "close", "low"]].max(axis=1))
        | (p.low > p[["open", "close", "high"]].min(axis=1))
        | p.date.isna()
    )
    return p[~bad].sort_values(["symbol", "date"]).reset_index(drop=True), p[bad].copy()


def features(prices, universe, rules=Rules(), exit_days=20):
    """Vectorized daily features; no full-sample rank or unfinished-week values."""
    rules.validate()
    p, rejected = clean_research_prices(prices)
    frames = {s: f.set_index("date") for s, f in p.groupby("symbol")}
    required_mrs = {
        mansfield_benchmark_symbol(item, rules) for item in universe.to_dict("records")
    }
    if rules.mansfield_benchmark == "market_index" and required_mrs - set(frames):
        raise ValueError(
            "Mansfield 지수 일봉이 없습니다: "
            + ",".join(sorted(required_mrs - set(frames)))
            + ". index-data로 수집하세요."
        )
    output = []
    needed = required_history(rules)
    for item in universe.to_dict("records"):
        f, b = frames.get(str(item["symbol"])), frames.get(str(item["benchmark"]))
        if f is None or b is None:
            continue
        f = f.copy()
        mrs_benchmark = mansfield_benchmark_symbol(item, rules)
        computed = price_indicators(f, b, frames.get(mrs_benchmark), rules)
        for name in [
            "atr",
            "return_126d",
            "excess_return",
            "mansfield_observation_date",
            "mansfield_rs",
            "mansfield_change_1w",
        ]:
            f[name] = computed[name]
        f["mansfield_benchmark"] = mrs_benchmark
        f["rs_method"] = rules.rs_method
        for name in [
            "weekly_trend",
            "prior_breakout_high",
            "volume_ratio",
            "avg_turnover",
            "stop_price",
        ]:
            f[name] = computed[name]
        f["trailing_channel"] = f.low.rolling(exit_days).min()
        f["entry_method"] = rules.entry_method
        if rules.entry_method == "setup":
            detected = setup_features(f, rules, atr_values=f.atr, calendar=b.index)
            f = f.join(detected)
        # Continuity in benchmark sessions, not a generic weekday calendar.
        session_numbers = pd.Series(np.arange(len(b)), index=b.index).reindex(f.index)
        consecutive = (
            session_numbers.diff().eq(1).rolling(needed - 1).sum().eq(needed - 1)
        )
        f["eligible_history"] = consecutive & f.excess_return.notna()
        f["reference_price"] = f.close
        for k, v in item.items():
            f[k] = v
        output.append(f.reset_index())
    if not output:
        raise ValueError("계산 가능한 유니버스·벤치마크 데이터가 없습니다.")
    result = pd.concat(output, ignore_index=True)
    from .initial_stop import planning_stops

    result = planning_stops(result, rules)
    if rules.market_units_enabled:
        from .market_units import market_unit_features

        result = result.merge(
            market_unit_features(p, universe, rules),
            on=["date", "symbol"],
            how="left",
            validate="one_to_one",
        )
    if rules.entry_method == "setup":
        result = result.merge(
            sector_features(p, universe, rules),
            on=["date", "symbol"],
            how="left",
            validate="one_to_one",
        )
    result["rs_percentile"] = (
        result.return_126d.where(result.eligible_history)
        .groupby(result.date)
        .rank(pct=True)
        * 100
    )
    result = apply_entry_signals(result, rules)
    result = apply_signal_policies(result, rules)
    return result.sort_values(["date", "symbol"]).reset_index(drop=True), rejected


def simulate(
    prices,
    signals,
    universe,
    start,
    end,
    initial_cash=10_000_000,
    mode="fixed",
    limits=Limits(),
    max_entry_gap=0.05,
    initial_state=None,
):
    """Execute prior-close signals at next session open, then process intraday stops.

    Costs are split equally between entry and exit; slippage is separate. Mark holdings
    at actual known prices. No forced end liquidation, no same-close entries, no leverage.
    """
    limits.validate()
    if (
        mode not in {"fixed", "progressive"}
        or not math.isfinite(initial_cash)
        or initial_cash <= 0
    ):
        raise ValueError("mode 또는 초기 자금이 유효하지 않습니다.")
    p, _ = clean_research_prices(prices)
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    if first > last:
        raise ValueError("시작일은 종료일 이후일 수 없습니다.")
    benchmarks = set(universe.benchmark.astype(str))
    sessions = sorted(
        p[
            (p.symbol.isin(benchmarks)) & (p.date >= first) & (p.date <= last)
        ].date.unique()
    )
    if not sessions:
        raise ValueError("모의 실행할 거래일이 없습니다.")
    bars = {
        pd.Timestamp(d): f.set_index("symbol").to_dict("index")
        for d, f in p.groupby("date")
    }
    daily_signals = {pd.Timestamp(d): f for d, f in signals.groupby("date")}
    state = initial_state or {}
    cash = float(state.get("cash", initial_cash))
    units = int(state.get("units", limits.max_units if mode == "fixed" else 1))
    positions = copy.deepcopy(state.get("positions", {}))
    pending = list(state.get("pending", []))
    curve, trades, orders, events = [], [], [], []
    fee = limits.cost_rate / 2

    def sell(symbol, day, raw_price, reason):
        nonlocal cash, units
        pos = positions.pop(symbol)
        fill = raw_price * (1 - limits.exit_slippage)
        proceeds = pos["quantity"] * fill * (1 - fee)
        cash += proceeds
        pnl = proceeds - pos["entry_total"]
        trade = dict(
            symbol=symbol,
            entry_date=pos["entry_date"],
            exit_date=str(day.date()),
            quantity=pos["quantity"],
            entry_price=pos["entry_price"],
            exit_price=fill,
            pnl=pnl,
            r_multiple=pnl / pos["initial_risk"],
            exit_reason=reason,
            success_3r=pos["success"],
        )
        if "initial_stop_price" in pos:
            trade.update(
                {
                    k: pos[k]
                    for k in [
                        "initial_stop_price",
                        "initial_stop_method",
                        "initial_stop_reference",
                        "initial_stop_branch",
                        "signal_atr",
                        "initial_risk",
                    ]
                }
            )
        if "lots" in pos:
            trade.update(
                initial_quantity=pos["initial_quantity"],
                average_entry_price=pos["average_entry_price"],
                addition_count=pos["addition_count"],
                total_committed_risk=pos["total_committed_risk"],
                r_multiple_total_committed_risk=pnl / pos["total_committed_risk"],
            )
        rs_decision = pos.get("rs_exit_pending")
        if rs_decision:
            trade.update(
                rs_exit_signal_date=rs_decision["signal_date"],
                rs_exit_observation_date=rs_decision["observation_date"],
                rs_exit_indicator=rs_decision["indicator_value"],
                rs_exit_threshold=rs_decision["threshold"],
            )
            orders.append(
                dict(
                    date=str(day.date()),
                    symbol=symbol,
                    side="sell",
                    status="filled",
                    quantity=pos["quantity"],
                    fill_price=fill,
                    reason=reason,
                    signal_date=rs_decision["signal_date"],
                )
            )
            rs_exited_today.add(symbol)
        trades.append(trade)
        if mode == "progressive" and pnl < 0 and not pos["success"]:
            units = max(1, units - 1)
            events.append(
                dict(
                    date=str(day.date()), symbol=symbol, kind="loss_closed", units=units
                )
            )

    for raw_day in sessions:
        day = pd.Timestamp(raw_day)
        today = bars.get(day, {})
        rs_exited_today = set()
        # Overnight stop gaps and missing-data exits use the next available open.
        for symbol in sorted(list(positions)):
            pos = positions[symbol]
            bar = today.get(symbol)
            if bar is None or bar["volume"] <= 0:
                if pos.get("rs_exit_pending"):
                    orders.append(
                        dict(
                            date=str(day.date()),
                            symbol=symbol,
                            side="sell",
                            status="deferred",
                            reason="missing_or_untradable_open",
                            signal_date=pos["rs_exit_pending"]["signal_date"],
                        )
                    )
                continue
            if (
                bar["open"] <= pos["stop_price"]
                or pos.get("exit_pending", False)
                or pos.get("rs_exit_pending")
            ):
                if bar["high"] == bar["low"]:
                    pos["exit_pending"] = True
                    if pos.get("rs_exit_pending"):
                        orders.append(
                            dict(
                                date=str(day.date()),
                                symbol=symbol,
                                side="sell",
                                status="deferred",
                                reason="locked_single_price_bar",
                                signal_date=pos["rs_exit_pending"]["signal_date"],
                            )
                        )
                    continue
                reason = (
                    "gap_stop"
                    if bar["open"] <= pos["stop_price"]
                    else (
                        pos["rs_exit_pending"]["reason"]
                        if pos.get("rs_exit_pending")
                        else "missing_data_exit"
                    )
                )
                sell(symbol, day, float(bar["open"]), reason)
        # Missing next-session bars cancel the intended entry rather than defer indefinitely.
        candidates = []
        for pending_row in pending:
            symbol = pending_row["symbol"]
            bar = today.get(symbol)
            adding = pending_row.get("order_intent") == "add"
            reason = None
            if symbol in rs_exited_today:
                reason = "rs_exit_same_session_reentry_blocked"
            elif adding and symbol not in positions:
                reason = "position_closed_before_addition"
            elif adding and not pending_row.get("pyramiding_enabled", False):
                reason = "pyramiding_disabled"
            elif adding and (
                positions[symbol].get("exit_pending")
                or positions[symbol].get("rs_exit_pending")
            ):
                reason = "addition_exit_pending"
            elif symbol in positions and not adding:
                reason = "already_held"
            elif bar is None or bar["volume"] <= 0:
                reason = "missing_or_untradable_open"
            elif bar["high"] == bar["low"]:
                reason = "locked_single_price_bar"
            elif bar["open"] > pending_row["reference_price"] * (1 + max_entry_gap):
                reason = "entry_gap_limit"
            elif bar["open"] <= max(
                pending_row["stop_price"], pending_row.get("planned_common_stop", 0)
            ):
                reason = "open_below_stop"
            if reason:
                record = dict(
                    date=str(day.date()),
                    symbol=symbol,
                    status="rejected",
                    reason=reason,
                )
                if adding:
                    record.update(order_intent="add", signal_date=pending_row["date"])
                orders.append(record)
            else:
                candidates.append(
                    dict(pending_row, reference_price=float(bar["open"]), signal=True)
                )
        if candidates:
            account_positions = []
            for symbol, pos in positions.items():
                price = (
                    float(today[symbol]["open"])
                    if symbol in today
                    else pos["last_price"]
                )
                record = dict(
                    symbol=symbol,
                    sector=pos["sector"],
                    quantity=pos["quantity"],
                    current_price=price,
                    stop_price=pos["stop_price"],
                )
                if any(
                    c.get("pyramiding_enabled", False)
                    or c.get("campaign_risk_enabled", False)
                    for c in candidates
                ):
                    record = dict(pos, **record)
                    record["price_stale"] = (
                        symbol not in today or today[symbol]["volume"] <= 0
                    )
                account_positions.append(record)
            equity = cash + sum(
                x["quantity"] * x["current_price"] for x in account_positions
            )
            allocation = allocate(
                pd.DataFrame(candidates),
                Account(equity, cash, account_positions),
                replace(limits, units=limits.max_units if mode == "fixed" else units),
            )
            for row in allocation.to_dict("records"):
                quantity = int(row["quantity"])
                symbol = row["symbol"]
                if not quantity:
                    record = dict(
                        date=str(day.date()),
                        symbol=symbol,
                        status="rejected",
                        reason=row["allocation_reason"],
                    )
                    if row.get("order_intent") == "add":
                        record.update(order_intent="add", signal_date=row["date"])
                    orders.append(record)
                    continue
                fill = row["reference_price"] * (1 + limits.entry_slippage)
                entry_total = quantity * fill * (1 + fee)
                if entry_total > cash + 0.001:
                    raise ValueError("현금 배분 불일치")
                cash -= entry_total
                initial_risk = entry_total - quantity * row["stop_price"] * (
                    1 - limits.exit_slippage
                ) * (1 - fee)
                if row.get("order_intent") == "add":
                    from .pyramiding_execution import fill_addition

                    fill_addition(
                        positions[symbol],
                        row,
                        day,
                        quantity,
                        fill,
                        entry_total,
                        initial_risk,
                    )
                    events.append(
                        dict(
                            kind="campaign_lot_opened",
                            symbol=symbol,
                            **positions[symbol]["lots"][-1],
                        )
                    )
                    orders.append(
                        dict(
                            date=str(day.date()),
                            symbol=symbol,
                            side="buy",
                            order_intent="add",
                            status="filled",
                            quantity=quantity,
                            fill_price=fill,
                            signal_date=row["date"],
                            setup_id=positions[symbol]["pyramiding_filled_setups"][-1],
                            addition_number=positions[symbol]["addition_count"],
                            common_stop_before=row["common_stop_before"],
                            common_stop_after=row["stop_price"],
                            planned_risk=row["planned_risk"],
                            combined_position_risk=row["combined_position_risk"],
                            portfolio_risk_after=row["portfolio_risk_after"],
                            position_risk_limit=row["position_risk_limit"],
                            portfolio_risk_limit=row["portfolio_risk_limit"],
                            equity_at_allocation=row["equity_at_allocation"],
                            effective_units=row["effective_units"],
                        )
                    )
                    continue
                positions[symbol] = dict(
                    sector=row["sector"],
                    quantity=quantity,
                    entry_date=str(day.date()),
                    entry_price=fill,
                    entry_total=entry_total,
                    initial_risk=initial_risk,
                    stop_price=float(row["stop_price"]),
                    target_3r=fill + 3 * initial_risk / quantity,
                    last_price=fill,
                    success=False,
                    exit_pending=False,
                )
                if row.get("initial_stop_reference") == "entry_fill":
                    positions[symbol].update(
                        initial_stop_price=float(row["stop_price"]),
                        initial_stop_method=row["initial_stop_method"],
                        initial_stop_branch=row["initial_stop_branch"],
                        initial_stop_reference=row["initial_stop_reference"],
                        signal_atr=float(row["atr"]),
                    )
                if row.get("pyramiding_review_enabled", False):
                    from .pyramiding import setup_identity

                    positions[symbol].update(
                        pyramiding_review_enabled=True,
                        entry_setup_id=setup_identity(row),
                        pyramiding_review_seen=[],
                    )
                if row.get("pyramiding_enabled", False):
                    from .pyramiding_execution import lot_record

                    positions[symbol].update(
                        pyramiding_enabled=True,
                        addition_count=0,
                        initial_quantity=quantity,
                        average_entry_price=fill,
                        total_committed_risk=initial_risk,
                        pyramiding_filled_setups=[],
                        lots=[
                            lot_record(
                                row, day, quantity, fill, entry_total, initial_risk, 0
                            )
                        ],
                    )
                    events.append(
                        dict(
                            kind="campaign_lot_opened",
                            symbol=symbol,
                            **positions[symbol]["lots"][0],
                        )
                    )
                orders.append(
                    dict(
                        date=str(day.date()),
                        symbol=symbol,
                        status="filled",
                        quantity=quantity,
                        fill_price=fill,
                        signal_date=row["date"],
                        effective_units=row["effective_units"],
                        market_unit_regime=row.get("market_unit_regime", "disabled"),
                        initial_stop_price=float(row["stop_price"]),
                        initial_stop_method=row.get(
                            "initial_stop_method", "legacy_atr"
                        ),
                        initial_stop_reference=row.get(
                            "initial_stop_reference", "signal_close"
                        ),
                        initial_stop_branch=row.get("initial_stop_branch", "atr"),
                    )
                )
        pending = []
        # A same-day low below the stop wins over a same-day high above the 3R threshold.
        for symbol in sorted(list(positions)):
            pos = positions[symbol]
            bar = today.get(symbol)
            if bar is None or bar["volume"] <= 0:
                pos["exit_pending"] = True
                continue
            if bar["high"] == bar["low"]:
                # Price-limit queue is unknown: conservatively do not assume execution.
                if bar["low"] <= pos["stop_price"]:
                    pos["exit_pending"] = True
            elif bar["low"] <= pos["stop_price"]:
                sell(symbol, day, float(pos["stop_price"]), "intraday_stop")
                continue
            pos["last_price"] = float(bar["close"])
            if not pos["success"] and bar["close"] >= pos["target_3r"]:
                pos["success"] = True
                if pos.get("pyramiding_review_enabled", False):
                    pos["success_3r_date"] = str(day.date())
                if mode == "progressive":
                    units = min(limits.max_units, units + 1)
                    events.append(
                        dict(
                            date=str(day.date()),
                            symbol=symbol,
                            kind="success_3r",
                            units=units,
                        )
                    )
                pos["stop_price"] = max(pos["stop_price"], pos["entry_price"])
        todays_features = daily_signals.get(day)
        addition_plans = []
        if todays_features is not None:
            for row in todays_features.to_dict("records"):
                symbol = row["symbol"]
                if symbol in positions:
                    from .rs_exit import update_position

                    decision = update_position(positions[symbol], row, day)
                    if decision:
                        events.append(
                            dict(
                                date=str(day.date()),
                                symbol=symbol,
                                kind="rs_exit_scheduled",
                                **decision,
                            )
                        )
                if symbol in positions and pd.notna(row["trailing_channel"]):
                    positions[symbol]["stop_price"] = max(
                        positions[symbol]["stop_price"], float(row["trailing_channel"])
                    )
                if symbol in positions and row.get("pyramiding_review_enabled", False):
                    from .pyramiding import evaluate_addition

                    review = evaluate_addition(positions[symbol], row, day, limits)
                    if (
                        row.get("pyramiding_enabled", False)
                        and review["eligible_for_risk_review"]
                    ):
                        addition_plans.append(
                            dict(
                                row,
                                order_intent="add",
                                planned_common_stop=review["proposed_stop_price"],
                            )
                        )
                        review.update(
                            risk_aggregation_status="scheduled_for_next_open",
                            orders_created=1,
                        )
                    events.append(dict(kind="pyramiding_review", **review))
            score = (
                "allocation_score"
                if "allocation_score" in todays_features
                else "rs_percentile"
            )
            selected = todays_features[todays_features.signal].sort_values(
                [score, "symbol"], ascending=[False, True]
            )
            if "pyramiding_review_enabled" in selected:
                selected = selected[
                    ~(
                        selected.pyramiding_review_enabled.eq(True)
                        & selected.symbol.isin(positions)
                    )
                ]
            pending = json_records(selected)
            if addition_plans:
                pending.extend(json_records(pd.DataFrame(addition_plans)))
        observed = set(todays_features.symbol) if todays_features is not None else set()
        for symbol, pos in positions.items():
            if pos.get("pyramiding_review_enabled", False) and symbol not in observed:
                from .pyramiding import evaluate_addition

                review = evaluate_addition(
                    pos, dict(symbol=symbol, date=day), day, limits
                )
                review["review_reasons"] = (
                    "missing_signal_row," + review["review_reasons"]
                )
                events.append(dict(kind="pyramiding_review", **review))
        marked = sum(pos["quantity"] * pos["last_price"] for pos in positions.values())
        if any(pos.get("pyramiding_enabled", False) for pos in positions.values()):
            from .pyramiding_execution import risk_events

            events.extend(risk_events(positions, today, cash + marked, day, limits))
        curve.append(
            dict(
                date=str(day.date()),
                equity=cash + marked,
                cash=cash,
                positions=len(positions),
                units=units,
                stale_positions=sum(
                    pos.get("exit_pending", False) for pos in positions.values()
                ),
            )
        )
    final_state = {
        "cash": cash,
        "positions": positions,
        "units": units,
        "pending": pending,
        "last_date": str(pd.Timestamp(sessions[-1]).date()),
        "initial_cash": initial_cash,
        "mode": mode,
    }
    return (
        pd.DataFrame(curve),
        pd.DataFrame(trades),
        pd.DataFrame(orders),
        pd.DataFrame(events),
        final_state,
    )


def json_records(frame):
    import json

    f = frame.copy()
    for column in ["date", "mansfield_observation_date", "rs_exit_observation_date"]:
        if column in f:
            f[column] = pd.to_datetime(f[column]).dt.strftime("%Y-%m-%d")
    return json.loads(f.to_json(orient="records"))


def metrics(curve, trades, initial_cash):
    equity = curve.equity.astype(float)
    equity_with_initial = pd.Series([initial_cash, *equity.tolist()])
    elapsed = max(
        1,
        (pd.Timestamp(curve.date.iloc[-1]) - pd.Timestamp(curve.date.iloc[0])).days + 1,
    )
    final = float(equity.iloc[-1])
    pnl = trades.pnl if not trades.empty else pd.Series(dtype=float)
    wins = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    return {
        "initial_equity": initial_cash,
        "final_equity": final,
        "total_return": final / initial_cash - 1,
        "cagr": (final / initial_cash) ** (365.25 / elapsed) - 1,
        "max_drawdown": float(
            (equity_with_initial / equity_with_initial.cummax() - 1).min()
        ),
        "closed_trades": len(trades),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "profit_factor": wins / losses if losses else None,
        "realized_pnl": float(pnl.sum()),
        "unrealized_pnl": final - initial_cash - float(pnl.sum()),
        "mean_r": float(trades.r_multiple.mean()) if not trades.empty else None,
        "stale_position_days": int(curve.stale_positions.sum()),
    }
