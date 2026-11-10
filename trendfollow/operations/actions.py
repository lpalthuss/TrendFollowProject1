"""Actions responsibilities for the local operations UI."""

from dataclasses import asdict, replace
import hashlib
import json
import re
import pandas as pd
from .common import read_csv
from ..risk import Account, allocate
from ..workflow import read_inputs, paper_checkpoint, daily_run, save_json


class ActionRunner:
    def __init__(self, context):
        self.context = context

    def replay_revision(self, w, report_id, as_of):
        from ..revisions import (
            report_path,
            load_snapshot,
            snapshot_path,
            read_json,
            sha,
        )

        state_path = self.context.path(w["state"])
        home = report_path(state_path, report_id)
        report = read_json(home / "report.json")
        if report["as_of"] != as_of:
            raise ValueError("정정 보고서와 재계산 종료일이 다릅니다.")
        if report["config_sha256"] != sha(self.context.path(w["config"])) or report[
            "universe_sha256"
        ] != sha(self.context.path(w["universe"])):
            raise ValueError(
                "보고서 이후 설정·유니버스가 바뀌었습니다. 다시 대조하세요."
            )
        p, peers, _ = load_snapshot(snapshot_path(state_path, report["new_snapshot"]))
        rules, limits = self.context.configuration(w)
        row = self.context.create_workspace(
            dict(
                name=w["name"][:45] + " · 정정 재계산",
                capital=w["capital"],
                rules=asdict(rules),
                limits=asdict(limits),
            )
        )
        root = self.context.path(row["state"]).parent
        data = root / "recalculation-inputs"
        data.mkdir()
        p.to_csv(data / "prices.csv", index=False)
        u = read_csv(self.context.path(w["universe"]))
        if peers is not None:
            peers.to_csv(data / "peers.csv", index=False)
            u["industry_peer_history"] = str((data / "peers.csv").resolve())
        u.to_csv(data / "universe.csv", index=False)
        row.update(
            data_dir=str(data.relative_to(self.context.root)),
            universe=str((data / "universe.csv").relative_to(self.context.root)),
            mode="recalculation",
            source_workspace=w["id"],
            revision_id=report_id,
        )
        self.context.update_workspace(row)
        days = sorted(
            x.name
            for x in state_path.parent.iterdir()
            if x.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", x.name)
        )
        start = days[0] if days else report["account_as_of"]
        paper_checkpoint(
            data / "prices.csv",
            data / "universe.csv",
            start,
            self.context.path(row["state"]),
            w["capital"],
            self.context.path(row["config"]),
        )
        result = paper_checkpoint(
            data / "prices.csv",
            data / "universe.csv",
            as_of,
            self.context.path(row["state"]),
            w["capital"],
            self.context.path(row["config"]),
        )
        result.update(
            workspace_id=row["id"],
            comparison_start=start,
            source_workspace=w["id"],
            mode="corrected_data_replay",
            warning="정정 자료로 과거 판단을 다시 계산한 연구 계좌입니다. 원계좌의 당시 운용 결과와 구분하세요.",
        )
        save_json(root / "recalculation.json", result)
        return result

    def run(self, key, w, payload, as_of):
        action = payload["action"]
        prices = self.context.path(w["data_dir"]) / "prices.csv"
        universe = self.context.path(w["universe"])
        config = self.context.path(w["config"])
        state_path = self.context.path(w["state"])
        rules, limits = self.context.configuration(w)
        if action == "analyze":
            from ..backtest import features
            from ..research_store import require_published_research

            require_published_research(prices)
            p, u, r, l = read_inputs(prices, universe, as_of, config)
            f, bad = features(p, u, r)
            today = f[f.date == pd.Timestamp(as_of)].copy()
            if today.empty:
                raise ValueError(
                    "기준일 가격이 없습니다. 데이터 갱신 상태를 확인하세요."
                )
            state = self.context.state(w)
            if state and state["last_date"] != as_of:
                raise ValueError(
                    "후보 수량 계산은 계좌 기준일과 같은 날짜로 실행하세요. 먼저 일별 모의 처리를 진행하세요."
                )
            holdings = (
                [
                    dict(
                        symbol=s,
                        sector=x["sector"],
                        quantity=x["quantity"],
                        current_price=x["last_price"],
                        stop_price=x["stop_price"],
                    )
                    for s, x in state["positions"].items()
                ]
                if state
                else []
            )
            cash = state["cash"] if state else w["capital"]
            equity = cash + sum(x["quantity"] * x["current_price"] for x in holdings)
            from dataclasses import replace

            result = allocate(
                today,
                Account(equity, cash, holdings),
                replace(l, units=state["units"] if state else 1),
            )
            out = self.context.home / "analyses" / w["id"] / key
            out.mkdir(parents=True)
            result.to_csv(out / "signals.csv", index=False, encoding="utf-8-sig")
            info = dict(
                as_of=as_of,
                mode="planning_only",
                quarantined_rows=len(bad),
                generated_at=self.context.now().isoformat(),
                signals=int(result.signal.sum()),
                allocated=int((result.quantity > 0).sum()),
                config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
            )
            save_json(out / "analysis.json", info)
            result = info
        elif action == "revision_check":
            from ..revisions import report_revision
            from ..observation_control import control_lock

            with control_lock(state_path):
                result = report_revision(prices, universe, config, state_path, as_of)
        elif action == "revision_continue":
            from ..revisions import approve_revision
            from ..workflow import continue_revised_checkpoint
            from ..observation_control import control_lock

            with control_lock(state_path):
                approve_revision(
                    state_path,
                    payload["report_id"],
                    prices,
                    universe,
                    config,
                    as_of,
                    price_reviewed=payload.get("price_reviewed", False),
                )
            result = continue_revised_checkpoint(
                prices,
                universe,
                as_of,
                state_path,
                w["capital"],
                config,
                payload["report_id"],
                self.context.root / ".env",
            )
        elif action == "revision_replay":
            result = self.context.replay_revision(w, payload["report_id"], as_of)
        elif action == "paper":
            result = paper_checkpoint(
                prices, universe, as_of, state_path, w["capital"], config
            )
        elif action == "daily":
            result = daily_run(
                universe,
                self.context.path(w["data_dir"]),
                state_path,
                as_of,
                w["capital"],
                config,
                self.context.root / ".env",
                "kiwoom",
            )
        elif action == "industry":
            from ..industries import industry_sync

            result = industry_sync(
                self.context.root / "data/market/universe.csv",
                self.context.root / "data/market/master",
                self.context.root / "data/industries",
                refresh=True,
            )
        elif action == "prepare_intraday":
            from ..intraday import prepare_plan

            signal = self.context.latest_signals(w)
            if signal is None:
                raise ValueError("먼저 기준일 후보를 분석하세요.")
            f = read_csv(signal)
            if f.empty or str(f.date.iloc[0])[:10] != as_of:
                raise ValueError("분석 기준일과 감시 준비 기준일이 다릅니다.")
            symbols = payload.get("symbols", [])
            if (
                not isinstance(symbols, list)
                or not symbols
                or not all(
                    isinstance(s, str) and re.fullmatch("[0-9A-Z]{6}", s)
                    for s in symbols
                )
            ):
                raise ValueError("감시할 종목을 선택하세요.")
            out = self.context.home / "intraday" / key
            out.mkdir(parents=True)
            plan = out / "plan.json"
            references = []
            for ref in (self.context.home / "volume").glob("reference-*.json"):
                data = json.loads(ref.read_text(encoding="utf-8"))
                if data["as_of"] <= as_of and not data.get("synthetic"):
                    references.append((data["as_of"], ref.stat().st_mtime_ns, ref))
            reference = max(references)[2] if references else None
            result = prepare_plan(
                signal,
                prices,
                universe,
                payload["session"],
                symbols,
                config,
                plan,
                paper_state=state_path if state_path.exists() else None,
                volume_profile=reference,
            )
            result["ui_workspace_id"] = w["id"]
            save_json(plan, result)
            result = dict(
                plan_id=key,
                subscriptions=len(result["subscriptions"]),
                session=result["session"],
                volume_reference="attached" if reference else "unavailable",
            )
        elif action == "volume_import":
            from ..intraday_volume import import_runs

            result = import_runs(
                [self.context.run_path(payload["run_id"])],
                self.context.home / "volume/samples.csv",
            )
        elif action == "volume_profile":
            from ..intraday_volume import build_reference

            result = build_reference(
                self.context.home / "volume/samples.csv",
                as_of,
                self.context.home / f"volume/reference-{key}.json",
            )
        else:
            from ..intraday_close import close_comparison

            run = self.context.run_path(payload["run_id"])
            out = self.context.home / "close-comparisons" / key
            plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
            if plan["session"] != as_of:
                raise ValueError("대조 기준일을 관측 거래일과 맞추세요.")
            result = close_comparison(
                run / "plan.json",
                prices,
                self.context.root / "data/industries/peer-returns.csv",
                out,
                run_dir=run,
            )
        return result

    def revision_policy(self, payload):
        from ..revisions import set_policy

        w = self.context.workspace(payload["workspace"])
        return set_policy(self.context.path(w["state"]), payload["policy"])
