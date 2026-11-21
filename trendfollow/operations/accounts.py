"""Accounts responsibilities for the local operations UI."""

from dataclasses import asdict, replace
from datetime import datetime
import json
import math
import re
import uuid
from zoneinfo import ZoneInfo
from ..risk import Limits
from ..config import Rules, strategy_config, strategy_versions
from ..workflow import save_json
from ..observation_control import control_lock

BUILTINS = {
    "baseline": (
        "돌파 기준선",
        "examples/config.json",
        "outputs/paper/state.json",
        "data/market/universe.csv",
        "data/market/history",
    ),
    "setup-rs": (
        "셋업 · RS",
        "examples/config-setup.json",
        "outputs/paper-setup-rs/state.json",
        "data/industries/universe.csv",
        "data/market/history",
    ),
    "setup-mansfield": (
        "셋업 · Mansfield",
        "examples/config-setup-mansfield-index.json",
        "outputs/paper-setup-mansfield/state.json",
        "data/industries/universe.csv",
        "data/market/history-mansfield",
    ),
}


class WorkspaceStore:
    def __init__(self, context):
        self.context = context
        self.registry = context.home / "workspaces.json"

    def path(self, value):
        p = (self.context.root / value).resolve()
        if (
            not p.is_relative_to(self.context.root)
            or not p.relative_to(self.context.root).parts
            or p.relative_to(self.context.root).parts[0]
            not in {"data", "outputs", "examples"}
        ):
            raise ValueError("허용된 프로젝트 데이터 경로가 아닙니다.")
        return p

    def registry_rows(self):
        if not self.context.registry.exists():
            return []
        return json.loads(self.context.registry.read_text(encoding="utf-8"))

    def workspaces(self):
        rows = [
            dict(
                id=k,
                name=v[0],
                config=v[1],
                state=v[2],
                universe=v[3],
                data_dir=v[4],
                capital=10000000,
                builtin=True,
            )
            for k, v in BUILTINS.items()
        ]
        return rows + self.context.registry_rows()

    def workspace_summaries(self):
        rows = []
        for workspace in self.workspaces():
            row = dict(workspace)
            state = self.state(row)
            row["account_as_of"] = state["last_date"] if state else None
            rows.append(row)
        return rows

    def workspace(self, key):
        for row in self.context.workspaces():
            if row["id"] == key:
                return row
        raise ValueError("계좌를 찾을 수 없습니다.")

    def favorites(self, key):
        path = self.context.home / "favorites" / (self.workspace(key)["id"] + ".json")
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def set_favorite(self, key, symbol, selected):
        if not isinstance(symbol, str) or not re.fullmatch(r"[0-9A-Z]{6}", symbol) or type(selected) is not bool:
            raise ValueError("관심 종목 코드와 선택 상태를 확인하세요.")
        path = self.context.home / "favorites" / (self.workspace(key)["id"] + ".json")
        with control_lock(path):
            symbols = set(self.favorites(key))
            if selected:
                symbols.add(symbol)
            else:
                symbols.discard(symbol)
            save_json(path, sorted(symbols))

    def config_path(self, w, day=None):
        state_path = self.context.path(w["state"])
        if day is None:
            day = (self.state(w) or {}).get("last_date", "9999-12-31")
        return strategy_config(state_path, self.context.path(w["config"]), day)

    def enable_pyramiding(self, key, review, execution, expected_day):
        from ..ui_jobs import acquire_lease

        w = self.workspace(key)
        if w.get("archived_at") or w.get("mode") == "recalculation":
            raise ValueError("운용 중인 원계좌에서 설정하세요.")
        if type(review) is not bool or type(execution) is not bool or execution and not review:
            raise ValueError("추가매수 모의 체결에는 후보 검토가 필요합니다.")
        lease = acquire_lease(self.context.home / "jobs" / ".executor.lock")
        if lease is None:
            raise ValueError("실행 중인 작업이 끝난 후 설정하세요.")
        try:
            state_path = self.context.path(w["state"])
            with control_lock(state_path):
                day = (self.state(w) or {}).get("last_date")
                if day != expected_day:
                    raise ValueError("계좌 처리일이 바뀌었습니다. 화면을 새로고침하세요.")
                rules, limits = self.configuration(w, "9999-12-31")
                if rules.entry_method != "setup":
                    raise ValueError("파동 셋업 계좌에서 추가매수를 설정할 수 있습니다.")
                if rules.pyramiding_enabled and not execution or rules.pyramiding_review_enabled and not review:
                    raise ValueError("이미 적용한 추가매수 설정을 해제할 수 없습니다.")
                updated = replace(rules, pyramiding_review_enabled=review, pyramiding_enabled=execution,
                                  initial_stop_reference="entry_fill" if execution else rules.initial_stop_reference)
                updated.validate()
                if updated != rules:
                    save_json(strategy_versions(state_path) / ((day or "0000-00-00") + ".json"),
                              dict(rules=asdict(updated), limits=asdict(limits)))
        finally:
            lease.release()

    def configuration(self, w, day=None):
        cfg = json.loads(self.config_path(w, day).read_text(encoding="utf-8-sig"))
        if set(cfg) - {"rules", "limits"}:
            raise ValueError("설정에는 rules와 limits만 지정하세요.")
        rules = Rules(**cfg.get("rules", {}))
        limits = Limits(**cfg.get("limits", {}))
        rules.validate()
        limits.validate()
        return rules, limits

    def presets(self):
        result = []
        for p in sorted((self.context.root / "examples").glob("config*.json")):
            cfg = json.loads(p.read_text(encoding="utf-8-sig"))
            if not set(cfg) <= {"rules", "limits"}:
                continue
            rules = Rules(**cfg.get("rules", {}))
            limits = Limits(**cfg.get("limits", {}))
            rules.validate()
            limits.validate()
            result.append(
                dict(
                    id=p.stem,
                    name=p.stem.removeprefix("config-"),
                    rules=asdict(rules),
                    limits=asdict(limits),
                )
            )
        return result

    def create_workspace(self, payload):
        name = str(payload.get("name", "")).strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("계좌 이름은 1~60자로 입력하세요.")
        rules = Rules(**payload.get("rules", {}))
        limits = Limits(**payload.get("limits", {}))
        rules.validate()
        limits.validate()
        capital = float(payload.get("capital", 10000000))
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("초기 모의자금을 확인하세요.")
        key = uuid.uuid4().hex[:16]
        indices = rules.mansfield_benchmark == "market_index" or (
            rules.market_units_enabled
            and rules.market_units_benchmark == "market_index"
        )
        row = dict(
            id=key,
            name=name,
            config=f"outputs/ui/accounts/{key}/config.json",
            state=f"outputs/ui/accounts/{key}/state.json",
            capital=capital,
            builtin=False,
            universe=(
                "data/industries/universe.csv"
                if rules.entry_method == "setup"
                else "data/market/universe.csv"
            ),
            data_dir=(
                "data/market/history-mansfield" if indices else "data/market/history"
            ),
        )
        self.context.path(
            row["universe"]
        )  # validate path; actual availability is shown separately.
        with self.context.lock, control_lock(self.context.registry):
            save_json(
                self.context.path(row["config"]),
                dict(rules=asdict(rules), limits=asdict(limits)),
            )
            save_json(self.context.registry, [*self.context.registry_rows(), row])
        return row

    def state(self, w):
        p = self.context.path(w["state"])
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def update_workspace(self, row):
        with self.context.lock, control_lock(self.registry):
            save_json(
                self.registry,
                [row if x["id"] == row["id"] else x for x in self.registry_rows()],
            )

    def set_archived(self, key, archived):
        if type(archived) is not bool:
            raise ValueError("계좌 보관 상태를 확인하세요.")
        if key in BUILTINS:
            raise ValueError("기본 계좌는 보관할 수 없습니다.")
        with self.context.lock, control_lock(self.registry):
            rows = self.registry_rows()
            row = next((item for item in rows if item["id"] == key), None)
            if row is None:
                raise ValueError("계좌를 찾을 수 없습니다.")
            self.context.refresh_jobs()
            if archived and any(
                job["workspace"] == key and job["status"] in {"queued", "running", "unconfirmed"}
                for job in self.context.jobs.values()
            ):
                raise ValueError("이 계좌의 작업이 끝난 뒤 보관하세요.")
            updated = dict(row)
            if archived:
                updated["archived_at"] = row.get("archived_at") or datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
            else:
                updated.pop("archived_at", None)
            save_json(self.registry, [updated if item["id"] == key else item for item in rows])
            return updated

    def rename_workspace(self, key, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError("계좌 이름은 1~60자로 입력하세요.")
        if key in BUILTINS:
            raise ValueError("기본 계좌의 이름은 변경할 수 없습니다.")
        with self.context.lock, control_lock(self.registry):
            rows = self.registry_rows()
            row = next((item for item in rows if item["id"] == key), None)
            if row is None:
                raise ValueError("계좌를 찾을 수 없습니다.")
            updated = dict(row, name=name.strip())
            save_json(self.registry, [updated if item["id"] == key else item for item in rows])
            return updated
