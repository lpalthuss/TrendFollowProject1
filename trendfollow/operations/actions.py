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

    def prepare_watch_candidates(self, out, prices, frame, universe, members, symbols, as_of, rules, watch_mode):
        from ..backtest import features
        from ..market import acquire_research
        from ..relative_strength import mansfield_benchmark_symbol

        # Percentile RS needs the whole comparison universe. Mansfield is per symbol.
        if watch_mode == "symbols" and rules.rs_method == "mansfield":
            members = members[members.symbol.isin(symbols)]
        required = set(members.symbol) | set(members.benchmark)
        benchmarks = set(members.benchmark) | {
            mansfield_benchmark_symbol(row, rules) for row in members.to_dict("records")
        }
        required |= benchmarks
        if not frame.empty:
            frame = frame[frame.date.le(as_of) & frame.symbol.isin(required)]
        if frame.empty or not required.issubset(set(frame.loc[frame.date.eq(as_of), "symbol"])):
            collection_universe = universe
            if watch_mode == "symbols" and rules.rs_method == "mansfield":
                collection_universe = out / "universe.csv"
                members.to_csv(collection_universe, index=False)
            source = out / "daily"
            manifest = acquire_research(
                collection_universe, source, "2020-01-01", as_of,
                include_market_indices=rules.mansfield_benchmark == "market_index",
                required_session=as_of,
            )
            if not manifest["published"]:
                raise ValueError(f"{as_of} 확정 일봉을 모두 가져오지 못했습니다. 잠시 후 다시 시도하세요.")
            prices = source / "prices.csv"
            frame = read_csv(prices)

        calculated, _ = features(frame, members, rules)
        candidates = calculated[calculated.date.eq(pd.Timestamp(as_of))]
        if not set(symbols).issubset(set(candidates.symbol)):
            raise ValueError(f"{as_of} 감시 종목의 유효한 일봉이 없습니다.")
        signal = out / "candidates.csv"
        candidates.to_csv(signal, index=False, encoding="utf-8-sig")
        return signal, prices

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
        progress = lambda message, current=None, total=None, **stage: self.context.job_manager.report_progress(
            key, message, current, total, **stage
        )
        progress("입력 자료 확인 중")
        prices = self.context.path(w["data_dir"]) / "prices.csv"
        universe = self.context.path(w["universe"])
        config = self.context.path(w["config"])
        state_path = self.context.path(w["state"])
        rules, limits = self.context.configuration(w)
        if action == "market_analyze":
            from ..market_candidate_analysis import analyze_saved_selections

            result = analyze_saved_selections(self.context, w, as_of, key, progress)
        elif action == "analyze":
            from ..backtest import features
            from ..research_store import require_published_research

            state = self.context.state(w)
            if state and state["last_date"] != as_of:
                if w.get("mode") == "recalculation":
                    raise ValueError(
                        f"이 정정 재계산 계좌는 {state['last_date']}까지만 저장돼 있습니다. "
                        "다른 날짜의 후보는 원계좌에서 확인하세요."
                    )
                raise ValueError(
                    f"후보 재계산은 계좌 처리일 {state['last_date']}에만 가능합니다. "
                    "먼저 일별 모의 처리를 진행하세요."
                )
            require_published_research(prices)
            if not pd.read_csv(prices, usecols=["date"], dtype=str).date.eq(as_of).any():
                raise ValueError(
                    f"{as_of} 저장 가격이 없습니다. 일반 계좌에서 데이터 갱신 + 일별 처리를 먼저 실행하세요."
                )
            progress("후보 조건 계산 중")
            p, u, r, l = read_inputs(prices, universe, as_of, config)
            f, bad = features(p, u, r)
            today = f[f.date == pd.Timestamp(as_of)].copy()
            if today.empty:
                raise ValueError(
                    "기준일 가격이 없습니다. 데이터 갱신 상태를 확인하세요."
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
            progress("후보 결과 저장 중")
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

            progress("과거 입력 변경 내역 대조 중")
            with control_lock(state_path):
                result = report_revision(prices, universe, config, state_path, as_of)
        elif action == "revision_continue":
            from ..revisions import approve_revision
            from ..workflow import continue_revised_checkpoint
            from ..observation_control import control_lock

            progress("정정 내역 승인·계좌 이어 처리 중")
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
            catch_up_to = payload.get("catch_up_to")
            if catch_up_to and catch_up_to > as_of:
                progress(f"정정 반영 완료 · {catch_up_to}까지 남은 거래일 처리 중")
                result = daily_run(
                    universe,
                    self.context.path(w["data_dir"]),
                    state_path,
                    catch_up_to,
                    w["capital"],
                    config,
                    self.context.root / ".env",
                    "kiwoom",
                    catch_up=True,
                    progress=progress,
                    use_existing_prices=True,
                )
                result["revision_id"] = payload["report_id"]
        elif action == "revision_replay":
            progress("정정 자료로 비교 계좌 재계산 중")
            result = self.context.replay_revision(w, payload["report_id"], as_of)
        elif action == "paper":
            progress("저장 가격으로 모의계좌 처리 중")
            result = paper_checkpoint(
                prices, universe, as_of, state_path, w["capital"], config
            )
        elif action == "daily":
            progress("누락 거래일 확인 중")
            if rules.entry_method == "setup":
                from .intraday import industry_ready
                if as_of == str(self.context.now().date()) and not industry_ready(
                    pd.read_csv(universe,dtype={"symbol":str,"benchmark":str}),as_of
                ):
                    from ..industries import industry_sync
                    progress("오늘 산업 자료 수집 중")
                    industry_sync(
                        self.context.root / "data/market/universe.csv",
                        self.context.root / "data/market/master",
                        self.context.root / "data/industries",
                        refresh=True,
                    )
            from ..candidate_universe import read_policy

            policy = read_policy(self.context.root, w["id"])
            if policy and self.context.state(w) and as_of > policy["enabled_after"]:
                from exchange_calendars import get_calendar
                from ..candidate_universe import (
                    account_universe, acquisition_universe, available_selections,
                    backfill_added_history, capture_krx_selection, capture_selection,
                    historical_candidate_source, industry_membership_snapshot, latest_completed_session,
                )

                last = self.context.state(w)["last_date"]
                sessions = [day.date().isoformat() for day in get_calendar(
                    "XKRX", start=last, end=as_of
                ).sessions_in_range(last, as_of) if day.date().isoformat() > last]
                if not sessions or sessions[-1] != as_of:
                    raise ValueError("마지막 처리일 이후의 완료 거래일을 선택하세요.")
                available = set(available_selections(self.context.root))
                missing = [day for day in sessions if day not in available]
                latest = latest_completed_session(self.context.now())
                current_kis_day = latest if latest == self.context.now().date().isoformat() else None
                historical = [day for day in missing if day != current_kis_day]
                source = (historical_candidate_source(self.context.root, payload.get("candidate_source", "auto"))
                          if historical else None)
                for index, day in enumerate(missing, 1):
                    progress(f"{day} 시장 종목 목록 저장 중", index - 1, len(missing))
                    if day == current_kis_day:
                        capture_selection(self.context.root, day, self.context.now(), progress)
                    else:
                        capture_krx_selection(self.context.root, day, self.context.now(), progress,
                                              source=source)
                    available.add(day)
                if missing:
                    progress("미처리 거래일의 시장 종목 목록 준비 완료", len(missing), len(missing))
                if rules.entry_method == "setup":
                    for day in sessions:
                        snapshot = industry_membership_snapshot(self.context.root, day)
                        if snapshot.parent.name != day:
                            progress(f"{day} 산업 분류에 {snapshot.parent.name} 저장 구성 사용")
                completed = []
                for day in sessions:
                    def day_progress(message, current=None, total=None):
                        progress(f"{day} · {message}", len(completed), len(sessions),
                                 stage_current=current, stage_total=total)

                    day_progress("후보군·보유 종목 준비 중")
                    state = self.context.state(self.context.workspace(w["id"]))
                    current = self.context.workspace(w["id"])
                    decision = account_universe(self.context, current, day, state)
                    acquisition = acquisition_universe(self.context, current, decision)
                    backfill_added_history(self.context.path(current["data_dir"]), decision, day, day_progress)

                    result = daily_run(
                        current["universe"], self.context.path(current["data_dir"]), state_path,
                        day, current["capital"], config, self.context.root / ".env", "kiwoom",
                        catch_up=True, reconstruct_missing_peers=True, progress=day_progress,
                        price_source=payload.get("price_source", "naver"),
                        universe_by_session={day: decision},
                        fingerprint_universe_path=current["universe"],
                        acquisition_universe_path=acquisition,
                    )
                    completed.append(day)
                    progress(f"{day} · 종가 처리 완료", len(completed), len(sessions))
                result["processed_dates"] = completed
                result["candidate_universe_mode"] = "daily"
            else:
                result = daily_run(
                    universe,
                    self.context.path(w["data_dir"]),
                    state_path,
                    as_of,
                    w["capital"],
                    config,
                    self.context.root / ".env",
                    "kiwoom",
                    catch_up=True,
                    reconstruct_missing_peers=True,
                    progress=progress,
                    price_source=payload.get("price_source", "naver"),
                )
        elif action == "industry":
            from ..industries import industry_sync

            progress("산업 자료 수집·저장 중")
            result = industry_sync(
                self.context.root / "data/market/universe.csv",
                self.context.root / "data/market/master",
                self.context.root / "data/industries",
                refresh=True,
            )
        elif action == "candidate_universe":
            from ..candidate_universe import capture_selection, capture_krx_missing

            if payload["candidate_source"] in {"krx_open_api", "pykrx"}:
                result = capture_krx_missing(self.context.root, self.context.now(), progress,
                                             from_as_of=payload.get("from_as_of"),
                                             source=payload["candidate_source"])
            else:
                result = capture_selection(self.context.root, as_of, self.context.now(), progress)
        elif action == "candidate_policy":
            from ..candidate_universe import enable_daily_account

            result = enable_daily_account(self.context, w, progress)
        elif action == "watch_auto":
            from ..krx_sessions import observation_market

            now = self.context.now()
            if observation_market(now) is None:
                raise ValueError("정규장 또는 NXT 애프터마켓 관측 시간에만 시작할 수 있습니다.")
            progress("감시 기준 자료와 계획 준비 중")
            prepared = self.run(key, w, {**payload, "action": "prepare_intraday"}, as_of)
            progress("KIS 장중 관측 시작 중")
            finish = now.replace(hour=20, minute=0, second=0, microsecond=0)
            seconds = max(1, min(39600, int((finish - self.context.now()).total_seconds())))
            started = self.context.start_watch(dict(workspace=w["id"],
                                                    plan_id=prepared["plan_id"], seconds=seconds))
            result = {**prepared, **started}
        elif action == "prepare_intraday":
            from ..intraday import prepare_plan
            from ..research_store import require_published_research
            from .intraday import industry_ready

            progress("감시 종목·기준 종가 확인 중")
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
            if rules.entry_method != "setup" or rules.market_units_enabled:
                raise ValueError("시장 유닛이 비활성화된 파동 셋업 계좌를 선택하세요.")
            watch_mode = payload.get("watch_mode", "industry")
            if watch_mode not in ("industry", "symbols"):
                raise ValueError("감시 범위를 선택하세요.")
            members = pd.read_csv(universe, dtype={"symbol": str, "benchmark": str}, keep_default_na=False)
            if not set(symbols).issubset(set(members.symbol)):
                raise ValueError("선택 계좌의 유니버스에 포함된 종목 코드를 입력하세요.")
            if watch_mode == "industry" and not industry_ready(members, as_of):
                raise ValueError(f"{as_of} 산업 구성 자료가 없습니다. 입력 종목만으로 감시 계획을 생성할 수 있습니다.")
            state = self.context.state(w)
            paper_state = state_path if state and state["last_date"] == as_of else None
            held = list(state["positions"]) if paper_state and watch_mode == "industry" else []
            targets = sorted(set(symbols + held))
            signal = self.context.latest_signals(w, as_of)
            require_published_research(prices)
            frame = read_csv(prices)
            out = self.context.home / "intraday" / key
            out.mkdir(parents=True)
            plan = out / "plan.json"
            if (signal is None or frame.empty
                    or not set(targets).issubset(set(frame.loc[frame.date.eq(as_of), "symbol"]))):
                progress("감시 후보·가격 자료 준비 중")
                signal, prices = self.prepare_watch_candidates(
                    out, prices, frame, universe, members, targets, as_of, rules, watch_mode
                )
            references = []
            for ref in (self.context.home / "volume").glob("reference-*.json"):
                data = json.loads(ref.read_text(encoding="utf-8"))
                if data["as_of"] <= as_of and not data.get("synthetic"):
                    references.append((data["as_of"], ref.stat().st_mtime_ns, ref))
            reference = max(references)[2] if references else None
            progress("감시 계획 생성 중")
            result = prepare_plan(
                signal,
                prices,
                universe,
                payload["session"],
                symbols,
                config,
                plan,
                paper_state=paper_state,
                volume_profile=reference,
                watch_mode=watch_mode,
                ui_workspace_id=w["id"],
            )
            result = dict(
                plan_id=key,
                subscriptions=len(result["subscriptions"]),
                session=result["session"],
                volume_reference="attached" if reference else "unavailable",
            )
        elif action == "volume_import":
            from ..intraday_volume import import_runs

            progress("관측 기록의 거래량 집계 중")
            result = import_runs(
                [self.context.run_path(payload["run_id"])],
                self.context.home / "volume/samples.csv",
            )
        elif action == "volume_profile":
            from ..intraday_volume import build_reference

            progress("동시간 거래량 기준 계산 중")
            result = build_reference(
                self.context.home / "volume/samples.csv",
                as_of,
                self.context.home / f"volume/reference-{key}.json",
            )
        else:
            from ..intraday_close import close_comparison

            progress("장중 관측과 종가 대조 중")
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
