"""Accounts responsibilities for the local operations UI."""

from dataclasses import asdict
import json
import math
import uuid
from ..risk import Limits
from ..config import Rules
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

    def workspace(self, key):
        for row in self.context.workspaces():
            if row["id"] == key:
                return row
        raise ValueError("계좌를 찾을 수 없습니다.")

    def configuration(self, w):
        cfg = json.loads(self.context.path(w["config"]).read_text(encoding="utf-8-sig"))
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
