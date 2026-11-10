"""Intraday responsibilities for the local operations UI."""

from datetime import datetime
import json
import re
import subprocess
import sys
import uuid


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
                    targets=list(plan["targets"]),
                    subscriptions=len(plan["subscriptions"]),
                )
            )
        return result

    def run_path(self, key):
        if not re.fullmatch("[a-f0-9]{16}", key):
            raise ValueError("관측 기록을 찾을 수 없습니다.")
        return self.context.home / "live" / key

    def start_watch(self, payload):
        key = payload.get("plan_id")
        plans = {p["id"]: p for p in self.context.plans()}
        if key not in plans:
            raise ValueError("생성한 감시 계획을 선택하세요.")
        if payload.get("workspace") and plans[key]["workspace"] != payload["workspace"]:
            raise ValueError("감시 계획과 선택 계좌가 다릅니다.")
        now = self.context.now()
        if (
            plans[key]["session"] != str(now.date())
            or now.weekday() >= 5
            or not "09:00" <= now.strftime("%H:%M") <= "15:30"
        ):
            raise ValueError(
                "감시 계획의 실제 정규장 시간에만 관측을 시작할 수 있습니다."
            )
        seconds = int(payload.get("seconds", 300))
        if not 1 <= seconds <= 23400:
            raise ValueError("관측 시간은 1~23,400초로 지정하세요.")
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
        runs = []
        root = self.context.home / "live"
        for p in (
            sorted(
                root.glob("*/summary.json"),
                key=lambda p: p.stat().st_mtime_ns,
                reverse=True,
            )[:10]
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
                            self.context.now()
                            - datetime.fromisoformat(event["timestamp"])
                        ).total_seconds()
                    except (ValueError, KeyError, TypeError):
                        age = None
                    event["ui_current"] = (
                        summary.get("connection_state") == "active"
                        and age is not None
                        and -2 <= age <= 60
                    )
                runs.append(dict(id=p.parent.name, summary=summary, events=events))
            except (OSError, ValueError):
                continue
        return dict(
            control=control,
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
