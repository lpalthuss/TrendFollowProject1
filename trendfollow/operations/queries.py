"""Queries responsibilities for the local operations UI."""

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import re
import pandas as pd
from .common import read_csv, json_rows

SIGNAL_FIELDS = "symbol name sector benchmark date reference_price rs_percentile excess_return mansfield_rs allocation_score weekly_trend eligible_history volume_ratio avg_turnover atr stop_price stop_fraction signal signal_rs signal_mansfield setup_phase setup_ready setup_trigger setup_entry setup_invalidated setup_resistance setup_retracement setup_base_bars setup_volume_ratio setup_range_ratio sector_action sector_peer_count sector_up_fraction sector_excess_return sector_action_scope quantity planned_risk cash_required allocation_reason market_unit_regime market_unit_cap rs_exit_weak rs_exit_value".split()


class OperationsQueries:
    def __init__(self, context):
        self.context = context

    def pulse(self, key, as_of=None):
        self.context.refresh_jobs()
        w = self.context.workspace(key)
        paths = [
            self.context.path(w["state"]),
            self.context.path(w["config"]),
            self.context.path(w["universe"]),
            self.context.root / "data/industries/peer-returns.csv",
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
        state = self.context.state(w)
        candidates = []
        if state:
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

    def snapshot(self, key):
        w = self.context.workspace(key)
        rules, limits = self.context.configuration(w)
        state = self.context.state(w)
        signal = self.context.latest_signals(w)
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
            row["industry_name"] = industry_names.get(
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
        as_of = (
            str(frame.date.iloc[0])[:10]
            if not frame.empty and "date" in frame
            else state["last_date"] if state else None
        )
        return dict(
            workspace=w,
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

    def prices(self, key, symbol):
        w = self.context.workspace(key)
        if not re.fullmatch("[0-9A-Z]{6}", symbol):
            raise ValueError("종목코드를 확인하세요.")
        result = []
        for chunk in pd.read_csv(
            self.context.path(w["data_dir"]) / "prices.csv",
            dtype={"symbol": str},
            chunksize=100000,
        ):
            rows = chunk[chunk.symbol.eq(symbol)]
            if not rows.empty:
                result.extend(json_rows(rows))
        state = self.context.state(w)
        date = state["last_date"] if state else "9999-12-31"
        signal = self.context.latest_signals(w)
        if signal:
            header = pd.read_csv(signal, usecols=["date"], nrows=1)
            if not header.empty:
                date = str(header.date.iloc[0])[:10]
        return [
            r
            for r in result
            if r["date"] <= date
            and all(r.get(k, 0) > 0 for k in ["open", "high", "low", "close"])
        ][-180:]
