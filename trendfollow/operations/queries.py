"""Queries responsibilities for the local operations UI."""

from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import re
import pandas as pd
from .common import read_csv, json_rows
from ..indicators import wilder_atr_series

SIGNAL_FIELDS = "symbol name sector industry_name benchmark date open high low close reference_price rs_percentile excess_return mansfield_rs allocation_score weekly_trend eligible_history volume_ratio avg_turnover atr stop_price stop_fraction signal signal_rs signal_mansfield signal_entry_path prior_52w_high high52_breakout setup_phase setup_ready setup_trigger setup_entry setup_invalidated setup_resistance setup_retracement setup_retracement_today setup_base_bars setup_volume_ratio setup_range_ratio sector_action sector_peer_count sector_up_fraction sector_excess_return sector_action_scope quantity planned_risk cash_required allocation_reason market_unit_regime market_unit_cap rs_exit_weak rs_exit_value".split()


@lru_cache(maxsize=8)
def _legacy_52w_highs(prices_path, _modified_ns, _size, as_of):
    """Read-only display values for signals saved before 52-week fields existed."""
    prices = pd.read_csv(
        prices_path, usecols=["date", "symbol", "high"], dtype={"symbol": str}
    )
    prices = prices[prices.date.astype(str).le(as_of)].sort_values(["symbol", "date"])
    highs = {}
    for symbol, rows in prices.groupby("symbol", sort=False):
        previous = rows.high.iloc[-253:-1]
        if (
            len(rows) >= 253
            and str(rows.date.iloc[-1])[:10] == as_of
            and previous.notna().all()
        ):
            highs[symbol] = float(previous.max())
    return highs


class OperationsQueries:
    def __init__(self, context):
        self.context = context

    def pulse(self, key, as_of=None):
        from ..candidate_universe import policy_path, selection_home
        from ..market_candidate_analysis import analysis_home

        self.context.refresh_jobs()
        w = self.context.workspace(key)
        paths = [
            self.context.path(w["state"]),
            self.context.path(w["config"]),
            self.context.path(w["universe"]),
            self.context.root / "data/industries/peer-returns.csv",
            selection_home(self.context.root),
            analysis_home(self.context.root, w["id"]),
            policy_path(self.context.root, w["id"]),
            self.context.registry,
        ]
        signal = self.context.latest_signals(w)
        if signal:
            paths.append(signal)
        revision = "|".join(f"{p}:{p.stat().st_mtime_ns}" for p in paths if p.exists())
        with self.context.lock:
            jobs = list(self.context.jobs.values())[-25:]
        return dict(
            revision=revision,
            jobs=jobs,
            guidance=self.context.guidance(key, as_of),
            recovery=self.context.revision_view(key, preview=False),
        )

    def latest_signals(self, w, as_of=None):
        if as_of and re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            from ..market_candidate_analysis import latest_analysis

            market = latest_analysis(self.context.root, w["id"], as_of)
            if market:
                return market / "signals.csv"
        state = self.context.state(w)
        candidates = []
        if as_of and re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            candidates.append(self.context.path(w["state"]).parent / as_of / "signals.csv")
        elif state:
            candidates.append(
                self.context.path(w["state"]).parent
                / state["last_date"]
                / "signals.csv"
            )
        directory = self.context.home / "analyses" / w["id"]
        if directory.exists():
            candidates.extend(directory.glob("*/signals.csv"))
        available = [p for p in candidates if p.exists()]
        if as_of:
            available = [p for p in available if p.parent.name == as_of or (
                p.parent.parent == directory and
                pd.read_csv(p, usecols=["date"], nrows=1).date.astype(str).str[:10].eq(as_of).any()
            )]
        return max(available, key=lambda p: p.stat().st_mtime_ns) if available else None

    def candidate_dates(self, key):
        from ..candidate_universe import available_selections, latest_completed_session
        from ..krx_sessions import calendar

        w = self.context.workspace(key)
        root = self.context.path(w["state"]).parent
        processed = [p.name for p in root.glob("????-??-??") if (p / "signals.csv").is_file()]
        analysis_root = self.context.home / "analyses" / w["id"]
        for signal in analysis_root.glob("*/signals.csv") if analysis_root.exists() else []:
            header = pd.read_csv(signal, usecols=["date"], nrows=1)
            if not header.empty:
                processed.append(str(header.date.iloc[0])[:10])
        account_dates = sorted(set(processed), reverse=True)
        from ..market_candidate_analysis import analysis_home

        market_root = analysis_home(self.context.root, w["id"])
        if market_root.exists():
            market_dates = [p.name for p in market_root.iterdir() if p.is_dir()
                            and any((run / "signals.csv").is_file() for run in p.iterdir() if run.is_dir())]
            processed.extend(market_dates)
        else:
            market_dates = []
        selections = available_selections(self.context.root)
        now = self.context.now()
        completed = latest_completed_session(now)
        exchange = calendar(int(completed[:4]))
        reference_dates = [completed]
        for _ in range(4):
            reference_dates.append(exchange.previous_session(reference_dates[-1]).date().isoformat())
        return dict(signal_dates=sorted(set(processed), reverse=True),
                    account_analysis_dates=account_dates,
                    market_analysis_dates=sorted(set(market_dates), reverse=True),
                    selection_dates=selections,
                    reference_dates=reference_dates,
                    candidate_sources=["kis", "krx_open_api", "pykrx"],
                    capture_as_of=completed,
                    capture_available=now.date().isoformat() == completed and completed not in selections)

    def candidate_selection(self, as_of):
        from ..candidate_universe import display_selection

        return display_selection(self.context.root, as_of)

    def guidance(self, key, as_of=None):
        w = self.context.workspace(key)
        rules, _ = self.context.configuration(w)
        state = self.context.state(w)
        now = self.context.now()
        recommended = pd.Timestamp(now.date())
        if now.hour * 60 + now.minute < 970:
            recommended -= pd.Timedelta(days=1)
        while recommended.weekday() >= 5:
            recommended -= pd.Timedelta(days=1)
        target = as_of or str(recommended.date())
        try:
            day = pd.Timestamp(datetime.strptime(target, "%Y-%m-%d").date())
        except (ValueError, TypeError):
            raise ValueError("기준일을 YYYY-MM-DD로 입력하세요.")
        needs_industry = rules.entry_method == "setup"
        dates = []
        u = read_csv(self.context.path(w["universe"]))
        if needs_industry and "industry_peer_history" in u:
            files = set(u.industry_peer_history.dropna().astype(str)) - {""}
            if len(files) == 1:
                peer = self.context.path(next(iter(files)))
                if peer.exists():
                    dates = sorted(
                        pd.read_csv(peer, usecols=["date"])
                        .date.astype(str)
                        .unique()
                        .tolist()
                    )
        blocked = None
        if day.date() > now.date():
            blocked = "미래 날짜는 처리할 수 없습니다. 완료된 거래일을 선택하세요."
        elif day.date() == now.date() and now.hour * 60 + now.minute < 970:
            blocked = "당일 종가 처리는 한국시간 16:10 이후 가능합니다."
        elif day.weekday() >= 5:
            blocked = "주말입니다. 완료된 거래일을 선택하세요."
        elif state and target < state["last_date"]:
            blocked = "계좌 처리일보다 과거로 되돌릴 수 없습니다."
        return dict(
            today=str(now.date()),
            now=now.isoformat(),
            recommended_as_of=str(recommended.date()),
            as_of=target,
            requires_industry=needs_industry,
            industry_ready=not needs_industry or target in dates,
            industry_latest=dates[-1] if dates else None,
            processed=bool(state and state["last_date"] == target),
            blocked=blocked,
        )

    def revision_view(
        self, key, report_id=None, offset=0, symbol="", source="", date="", preview=True
    ):
        from ..revisions import read_policy, report_path, sha

        w = self.context.workspace(key)
        state_path = self.context.path(w["state"])
        reports = sorted(
            (state_path.parent / "revisions").glob("*/report.json"),
            key=lambda p: p.stat().st_mtime_ns,
        )
        if not reports:
            return dict(report=None, policy=read_policy(state_path), rows=[], total=0)
        file = (
            report_path(state_path, report_id) / "report.json"
            if report_id
            else reports[-1]
        )
        report = json.loads(file.read_text(encoding="utf-8"))
        current = self.context.state(w)
        report["applied"] = (file.parent / "applied.json").exists() or any(
            x.get("report_id") == report["id"]
            for x in (current or {}).get("revision_lineage", [])
        )
        report["stale"] = not report["applied"] and (
            not state_path.exists() or sha(state_path) != report["state_sha256"]
        )
        rows = []
        total = report["row_changes"]
        if preview:
            frame = read_csv(file.parent / "changes.csv")
            if symbol:
                frame = frame[
                    frame.symbol.str.contains(symbol, case=False, regex=False, na=False)
                ]
            if source:
                frame = frame[frame.source.eq(source)]
            if date:
                frame = frame[frame.date.eq(date)]
            total = len(frame)
            rows = json_rows(frame.iloc[max(0, offset) : max(0, offset) + 100])
        return dict(
            report=report, policy=read_policy(state_path), rows=rows, total=total
        )

    def snapshot(self, key, market_as_of=None):
        from ..candidate_universe import read_policy
        w = self.context.workspace(key)
        rules, limits = self.context.configuration(w)
        state = self.context.state(w)
        signal = self.context.latest_signals(w, market_as_of)
        frame = read_csv(signal) if signal else pd.DataFrame()
        fields = [c for c in SIGNAL_FIELDS if c in frame]
        rows = json_rows(frame[fields])
        universe = read_csv(self.context.path(w["universe"]))
        industry_names = {
            str(r["symbol"]): r["industry_name"]
            for r in json_rows(universe)
            if r.get("industry_name")
        }
        for row in rows:
            row["industry_name"] = row.get("industry_name") or industry_names.get(
                row.get("symbol"), row.get("sector")
            )
            row["rs_pass"] = (
                (
                    row.get("rs_percentile") is not None
                    and row["rs_percentile"] >= rules.min_rs
                    and row.get("excess_return") is not None
                    and row["excess_return"] > 0
                )
                if rules.rs_method == "percentile"
                else (
                    row.get("mansfield_rs") is not None
                    and row["mansfield_rs"] > rules.min_mansfield
                )
            )
            if (
                rules.rs_method == "percentile"
                and (
                    row.get("rs_percentile") is None or row.get("excess_return") is None
                )
                or rules.rs_method == "mansfield"
                and row.get("mansfield_rs") is None
            ):
                row["rs_pass"] = None
            if rules.entry_method != "setup":
                row["sector_action"] = None
        logs = {}
        if state:
            day = self.context.path(w["state"]).parent / state["last_date"]
            for name in [
                "orders",
                "trades",
                "events",
                "campaign-risk",
                "pyramiding-review",
            ]:
                logs[name] = json_rows(read_csv(day / f"{name}.csv").tail(150))
        positions = []
        if state:
            for symbol, p in state["positions"].items():
                positions.append(
                    dict(
                        symbol=symbol,
                        **{
                            k: v
                            for k, v in p.items()
                            if k
                            in {
                                "sector",
                                "quantity",
                                "entry_price",
                                "average_entry_price",
                                "last_price",
                                "stop_price",
                                "initial_risk",
                                "target_3r",
                                "addition_count",
                                "success",
                                "entry_date",
                                "rs_exit_pending",
                                "exit_pending",
                            }
                        },
                    )
                )
        analysis_meta = {}
        if signal and (signal.parent / "analysis.json").exists():
            analysis_meta = json.loads(
                (signal.parent / "analysis.json").read_text(encoding="utf-8")
            )
        status = "not_started" if state is None else "ready"
        if state:
            from ..industries import decision_universe_records
            from ..relative_strength import decision_rule_settings

            u = pd.read_csv(
                self.context.path(w["universe"]),
                dtype={"symbol": str, "benchmark": str},
                keep_default_na=False,
            )
            value = dict(
                universe=decision_universe_records(u),
                rules=decision_rule_settings(rules),
                limits=asdict(limits),
                capital=w["capital"],
            )
            if hashlib.sha256(
                json.dumps(value, sort_keys=True).encode()
            ).hexdigest() != state.get("config_fingerprint"):
                status = "configuration_mismatch"
        if (
            analysis_meta.get("config_sha256")
            and analysis_meta["config_sha256"]
            != hashlib.sha256(self.context.path(w["config"]).read_bytes()).hexdigest()
        ):
            status = "configuration_mismatch"
        if status != "configuration_mismatch" and rows and "prior_52w_high" not in fields:
            prices_path = self.context.path(w["data_dir"]) / "prices.csv"
            if prices_path.is_file():
                day = str(frame.date.iloc[0])[:10]
                stat = prices_path.stat()
                highs = _legacy_52w_highs(
                    str(prices_path), stat.st_mtime_ns, stat.st_size, day
                )
                for row in rows:
                    high = highs.get(row.get("symbol"))
                    if high is not None and row.get("reference_price") is not None:
                        row["prior_52w_high"] = high
                        row["high52_breakout"] = row["reference_price"] > high
        as_of = (
            str(frame.date.iloc[0])[:10]
            if not frame.empty and "date" in frame
            else state["last_date"] if state else None
        )
        return dict(
            workspace=w,
            candidate_policy=read_policy(self.context.root, w["id"]),
            rules=asdict(rules),
            limits=asdict(limits),
            status=status,
            as_of=as_of,
            account_as_of=state["last_date"] if state else None,
            cash=state["cash"] if state else w["capital"],
            units=state["units"] if state else 1,
            positions=positions,
            pending=state.get("pending", []) if state else [],
            rows=[] if status == "configuration_mismatch" else rows,
            logs=logs,
            analysis=analysis_meta,
            source="completed_close",
            files_ready=all(
                self.context.path(w[k]).is_file() for k in ["config", "universe"]
            )
            and (self.context.path(w["data_dir"]) / "prices.csv").is_file(),
        )

    def completed_date(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("기준일을 YYYY-MM-DD로 입력하세요.")
        day = pd.Timestamp(value)
        now = self.context.now()
        if day.date() > now.date() or (
            day.date() == now.date() and now.hour * 60 + now.minute < 970
        ):
            raise ValueError(
                "완료된 장마감 기준일만 처리하세요. 당일은 16:10 이후 허용합니다."
            )
        return value

    def prices(self, key, symbol, as_of=None):
        w = self.context.workspace(key)
        source = (
            self.context.workspace(w["source_workspace"])
            if w.get("mode") == "recalculation" and w.get("source_workspace")
            else w
        )
        if not re.fullmatch("[0-9A-Z]{6}", symbol):
            raise ValueError("종목코드를 확인하세요.")
        result = []
        for chunk in pd.read_csv(
            self.context.path(source["data_dir"]) / "prices.csv",
            dtype={"symbol": str},
            chunksize=100000,
        ):
            rows = chunk[chunk.symbol.eq(symbol)]
            if not rows.empty:
                result.extend(json_rows(rows))
        extra = self.context.root / "outputs" / "ui" / "market-analyses" / "extra-prices.csv"
        if extra.is_file():
            for chunk in pd.read_csv(extra, dtype={"symbol": str}, chunksize=100000):
                rows = chunk[chunk.symbol.eq(symbol)]
                if not rows.empty:
                    existing = {item["date"] for item in result}
                    result.extend(item for item in json_rows(rows) if item["date"] not in existing)
        state = self.context.state(w)
        date = as_of or (state["last_date"] if state else max((r["date"] for r in result), default="1970-01-01"))
        signal = self.context.latest_signals(w) if not as_of else None
        if signal:
            header = pd.read_csv(signal, usecols=["date"], nrows=1)
            if not header.empty:
                date = str(header.date.iloc[0])[:10]
        valid = [
            r
            for r in sorted(result, key=lambda item: item["date"])
            if r["date"] <= date
            and all(r.get(k, 0) > 0 for k in ["open", "high", "low", "close"])
        ]
        if not valid:
            return []
        period = self.context.configuration(w)[0].atr_days
        atr = wilder_atr_series(pd.DataFrame(valid), period)
        chart_start = (pd.Timestamp(date) - pd.DateOffset(months=15)).date().isoformat()
        chart = []
        for index, row in enumerate(valid):
            if row["date"] < chart_start:
                continue
            daily_atr = atr.iloc[index]
            previous_close = float(valid[index - 1]["close"]) if index else None
            multiples_available = previous_close is not None and pd.notna(daily_atr) and daily_atr > 0
            chart.append({
                **row,
                "atr": None if pd.isna(daily_atr) else float(daily_atr),
                "atr_up_multiple": max(0.0, float(row["high"]) - previous_close) / daily_atr if multiples_available else None,
                "atr_down_multiple": max(0.0, previous_close - float(row["low"])) / daily_atr if multiples_available else None,
            })
        return chart
