"""Intraday responsibilities for the local operations UI."""

from datetime import datetime
import json
import re
import subprocess
import sys
import uuid

from ..krx_sessions import default_watch_session, observation_market, previous_close_date, require_previous_close
from .common import read_csv


def industry_ready(universe, as_of):
    if "industry_peer_history" not in universe:
        return False
    from ..industries import load_peer_history

    try:
        return bool(load_peer_history(universe).date.eq(as_of).any())
    except FileNotFoundError:
        return False


def is_regular_session(now):
    return observation_market(now) is not None


class IntradayService:
    def __init__(self, context):
        self.context = context
        self.processes = {}

    def plans(self):
        result = []
        root = self.context.home / "intraday"
        for p in root.glob("*/plan.json") if root.exists() else []:
            plan = json.loads(p.read_text(encoding="utf-8"))
            result.append(
                dict(
                    id=p.parent.name,
                    workspace=plan.get("ui_workspace_id"),
                    session=plan["session"],
                    as_of=plan["as_of"],
                    targets=list(plan["targets"]),
                    selected_symbols=plan.get("selected_symbols", list(plan["targets"])),
                    subscriptions=len(plan["subscriptions"]),
                    watch_mode=plan.get("watch_mode", "industry"),
                )
            )
        return result

    def run_path(self, key):
        if not re.fullmatch("[a-f0-9]{16}", key):
            raise ValueError("관측 기록을 찾을 수 없습니다.")
        return self.context.home / "live" / key

    def plan_basis(self, workspace, session):
        as_of = previous_close_date(session)
        self.context.completed_date(as_of)
        w = self.context.workspace(workspace)
        state = self.context.state(w)
        return dict(
            as_of=as_of,
            candidates_ready=self.context.latest_signals(w, as_of) is not None,
            account_as_of=state["last_date"] if state else None,
            industry_ready=industry_ready(read_csv(self.context.path(w["universe"])), as_of),
        )

    def subscription_preview(self, workspace, as_of, symbols, watch_mode="industry", session=None):
        from ..intraday import watch_scope

        if not symbols or not all(re.fullmatch(r"[0-9A-Z]{6}", s) for s in symbols):
            raise ValueError("감시할 종목 코드를 확인하세요.")
        w = self.context.workspace(workspace)
        if session is not None:
            require_previous_close(session, as_of)
        signal = self.context.latest_signals(w, as_of)
        if signal is None:
            raise ValueError(f"{as_of} 종가 후보 자료를 먼저 준비하세요.")
        state = self.context.state(w)
        scope = watch_scope(
            signal,
            self.context.path(w["universe"]),
            symbols,
            self.context.path(w["state"]) if state and state["last_date"] == as_of else None,
            watch_mode,
        )
        if scope["as_of"] != as_of:
            raise ValueError("후보 기준일이 변경되었습니다. 화면을 새로고침하세요.")
        return dict(
            count=len(scope["subscriptions"]),
            industries=sorted(
                scope["industries"].values(),
                key=lambda industry: (-industry["members"], industry["name"]),
            ),
        )

    def start_watch(self, payload):
        key = payload.get("plan_id")
        plans = {p["id"]: p for p in self.context.plans()}
        if key not in plans:
            raise ValueError("생성한 감시 계획을 선택하세요.")
        if payload.get("workspace") and plans[key]["workspace"] != payload["workspace"]:
            raise ValueError("감시 계획과 선택 계좌가 다릅니다.")
        now = self.context.now()
        if plans[key]["session"] != str(now.date()) or not is_regular_session(now):
            raise ValueError(
                "감시 계획의 정규장 또는 NXT 애프터마켓 거래 시간에만 관측을 시작할 수 있습니다."
            )
        require_previous_close(plans[key]["session"], plans[key]["as_of"])
        seconds = int(payload.get("seconds", 39600))
        if not 1 <= seconds <= 39600:
            raise ValueError("관측 시간은 1~39,600초로 지정하세요.")
        from ..observation_control import control_status

        if control_status(self.context.root / "outputs/intraday/control.json")[
            "active_or_unconfirmed"
        ]:
            raise ValueError("이미 관측이 실행 중입니다.")
        run = uuid.uuid4().hex[:16]
        out = self.context.run_path(run)
        log = self.context.home / "live-logs" / f"{run}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "trendfollow",
            "intraday-live",
            "--observation",
            "on",
            "--plan",
            str(self.context.home / "intraday" / key / "plan.json"),
            "--out",
            str(out),
            "--seconds",
            str(seconds),
        ]
        with log.open("wb") as handle:
            process = subprocess.Popen(
                command,
                cwd=self.context.root,
                stdout=handle,
                stderr=handle,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                ),
            )
        self.context.processes[run] = process
        return dict(run_id=run, status="starting", orders_sent=0)

    def intraday(self, workspace=None):
        from ..observation_control import control_status

        control = control_status(self.context.root / "outputs/intraday/control.json")
        now = self.context.now()
        runs = []
        root = self.context.home / "live"
        for p in (
            sorted(
                root.glob("*/summary.json"),
                key=lambda p: p.stat().st_mtime_ns,
                reverse=True,
            )
            if root.exists()
            else []
        ):
            try:
                summary = json.loads(p.read_text(encoding="utf-8"))
                events = []
                plan = json.loads((p.parent / "plan.json").read_text(encoding="utf-8"))
                if workspace and plan.get("ui_workspace_id") != workspace:
                    continue
                file = p.parent / "events.jsonl"
                if file.exists():
                    with file.open("rb") as handle:
                        size = handle.seek(0, 2)
                        handle.seek(max(0, size - 65536))
                        lines = (
                            handle.read().decode("utf-8", errors="replace").splitlines()
                        )
                    for line in lines[-50:]:
                        try:
                            events.append(json.loads(line))
                        except ValueError:
                            continue
                for event in events:
                    try:
                        age = (
                            now - datetime.fromisoformat(event["timestamp"])
                        ).total_seconds()
                    except (ValueError, KeyError, TypeError):
                        age = None
                    event["ui_current"] = (
                        summary.get("connection_state") == "active"
                        and (
                            "subscriptions_requested" not in summary
                            or summary.get("subscription_acks") == summary["subscriptions_requested"]
                            or event.get("kind") == "holding_stop_state"
                        )
                        and age is not None
                        and -2 <= age <= 60
                    )
                runs.append(dict(id=p.parent.name, as_of=plan.get("as_of"),
                                 session=plan.get("session"), targets=list(plan.get("targets", {})),
                                 watch_mode=plan.get("watch_mode", "industry"),
                                 selected_symbols=plan.get("selected_symbols", list(plan["targets"])),
                                 levels={symbol: {key: target.get(key) for key in
                                          ("resistance", "setup_resistance", "high52_resistance", "setup_phase")}
                                         for symbol, target in plan.get("targets", {}).items()},
                                 summary=summary, events=events))
                if len(runs) >= 10:
                    break
            except (OSError, ValueError):
                continue
        default_session = default_watch_session(now)
        regular_session = is_regular_session(now) and default_session == now.date().isoformat()
        return dict(
            control=control,
            today=now.date().isoformat(),
            regular_session=regular_session,
            default_session=default_session,
            today_as_of=previous_close_date(default_session) if regular_session else None,
            plans=[
                p
                for p in self.context.plans()
                if workspace is None or p["workspace"] == workspace
            ],
            runs=runs,
        )

    def stop_watch(self):
        from ..observation_control import set_observation

        return set_observation(
            False, self.context.root / "outputs/intraday/control.json", wait_seconds=1
        )
