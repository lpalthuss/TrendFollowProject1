"""Immutable, dated market candidate selections for the operations UI."""

from datetime import date, time, timedelta
import time as clock_time
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .krx_sessions import calendar
from .market import build_universe, download_master


KST = ZoneInfo("Asia/Seoul")


def latest_completed_session(now):
    day = now.date()
    exchange = calendar(day.year)
    session = exchange.date_to_session(day, direction="previous")
    if session.date() == day and now.time().replace(tzinfo=None) < time(16, 10):
        session = exchange.previous_session(session)
    return session.date().isoformat()


def selection_home(root):
    return Path(root) / "outputs" / "ui" / "candidate-universes"


def _publish_directory(stage, target):
    """Retry transient Windows file locks while publishing an immutable snapshot."""
    for attempt in range(10):
        try:
            stage.rename(target)
            return
        except PermissionError:
            if attempt == 9 or target.exists():
                raise
            clock_time.sleep(0.05 * (attempt + 1))


def policy_path(root, workspace):
    return Path(root) / "outputs" / "ui" / "accounts" / workspace / "candidate-policy.json"


def read_policy(root, workspace):
    path = policy_path(root, workspace)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _copy_once(source, target):
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def enable_daily_account(context, workspace, progress=None):
    """Enable future dated selections on one existing account without replaying its past."""
    if workspace.get("builtin") or workspace.get("mode") == "recalculation":
        raise ValueError("기본·정정 계좌에는 날짜별 후보군을 직접 적용할 수 없습니다.")
    existing = read_policy(context.root, workspace["id"])
    if existing:
        baseline = context.path(existing["baseline_universe"])
        private = context.path(workspace["state"]).parent / "daily-prices"
        if not baseline.is_file() or not (private / "prices.csv").is_file():
            raise ValueError("날짜별 후보군 적용 파일이 불완전합니다. 계좌 파일을 확인하세요.")
        if workspace["universe"] != existing["baseline_universe"] or context.path(workspace["data_dir"]) != private:
            context.update_workspace(dict(workspace, universe=existing["baseline_universe"],
                                          data_dir=str(private.relative_to(context.root))))
        return existing
    state = context.state(workspace)
    if state is None:
        raise ValueError("계좌의 첫 종가 처리를 마친 뒤 날짜별 후보군을 켜세요.")
    if context.snapshot(workspace["id"])["status"] != "ready":
        raise ValueError("계좌 설정이 일치하지 않아 날짜별 후보군을 적용할 수 없습니다.")
    original = context.path(workspace["data_dir"])
    prices = original / "prices.csv"
    if not prices.is_file():
        raise ValueError("계좌의 저장 일봉이 없습니다.")
    private = context.path(workspace["state"]).parent / "daily-prices"
    if progress:
        progress("기존 일봉을 계좌 전용 저장소에 보존 중")
    for name in ("prices.csv", "manifest.json"):
        source = original / name
        target = private / name
        if source.is_file():
            _copy_once(source, target)
    baseline = context.path(workspace["state"]).parent / "baseline-universe.csv"
    _copy_once(context.path(workspace["universe"]), baseline)
    row = dict(workspace, data_dir=str(private.relative_to(context.root)),
               universe=str(baseline.relative_to(context.root)))
    policy = dict(mode="daily_universe", enabled_after=state["last_date"],
                  enabled_at=context.now().isoformat(), account_id=workspace["id"],
                  baseline_universe=row["universe"])
    from .workflow import save_json

    save_json(policy_path(context.root, workspace["id"]), policy)
    context.update_workspace(row)
    return policy


def available_selections(root):
    home = selection_home(root)
    return sorted(
        [p.name for p in home.iterdir() if p.is_dir() and len(p.name) == 10 and (p / "summary.json").is_file()],
        reverse=True,
    ) if home.exists() else []


REASON_LABELS = {
    "not_ordinary_stock": "일반 주권이 아님",
    "not_common_stock": "보통주가 아님",
    "halted": "거래정지",
    "liquidation": "정리매매",
    "administrative": "관리종목",
    "administrative_section": "관리·투자주의 구분",
    "low_liquidity_flag": "저유동성 종목",
    "preferred_or_other_class": "우선주 등 보통주 외 종목",
    "spac": "기업인수목적회사(스팩)",
    "spac_name": "기업인수목적회사(스팩)",
    "missing_industry": "업종 분류 없음",
    "missing_name": "종목명 확인 불가",
    "missing_trading_data": "당일 거래 자료 없음",
    "no_regular_trading": "해당 날짜 정규 거래 없음",
    "daily_turnover_below_threshold": "거래대금 기준 미만",
    "recent_turnover_proxy_below_threshold": "최근 거래대금 추정 기준 미만",
}


def _approx_amount(value):
    if value is None or not pd.notna(value):
        return "금액 확인 불가"
    amount = float(value)
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:,.0f}억원가량"
    if amount >= 10_000:
        return f"{amount / 10_000:,.0f}만원가량"
    return f"{amount:,.0f}원가량"


def _reason_label(raw):
    codes = [code.strip() for code in str(raw or "").split(",") if code.strip()]
    if "no_regular_trading" in codes or "missing_trading_data" in codes:
        codes = [code for code in codes if code != "daily_turnover_below_threshold"]
    labels = []
    for code in codes:
        labels.append(REASON_LABELS.get(code, code if any("가" <= c <= "힣" for c in code)
                                         else "선정 조건 미충족 · 상세 원인 확인 필요"))
    return " · ".join(dict.fromkeys(labels)) or "제외 원인 확인 불가"


def _change_display(summary, added, removed):
    for row in added:
        amount = _approx_amount(row.get("recent_turnover_proxy"))
        basis = "최근 거래대금 추정액" if summary.get("source") == "KIS market master" else "당일 거래대금"
        row["reason_display"] = f"{basis} {amount}"
    for row in removed:
        row["reason_display"] = _reason_label(row.get("reason"))


def read_selection(root, as_of):
    if as_of not in available_selections(root):
        raise ValueError(f"{as_of} 날짜의 후보군 선정 기록이 없습니다.")
    folder = selection_home(root) / as_of
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    codes = {"symbol": str, "benchmark": str}
    members = pd.read_csv(folder / "universe.csv", dtype=codes).to_dict("records")
    added = pd.read_csv(folder / "added.csv", dtype=codes).to_dict("records")
    removed = pd.read_csv(folder / "removed.csv", dtype=codes).to_dict("records")
    _change_display(summary, added, removed)
    return dict(summary=summary, members=members, added=added, removed=removed)


def display_selection(root, as_of, include_excluded=False):
    """Attach previously observed industry names for display without changing the saved selection."""
    result = read_selection(root, as_of)
    if include_excluded:
        excluded_path = selection_home(root) / as_of / "excluded.csv"
        result["excluded"] = (pd.read_csv(excluded_path, dtype={"symbol": str, "benchmark": str})
                              .to_dict("records") if excluded_path.is_file() else [])
        for row in result["excluded"]:
            row["reason_display"] = _reason_label(row.get("reasons"))
    snapshots = Path(root) / "data" / "industries" / "snapshots"
    observed = sorted((path for path in snapshots.glob("????-??-??")
                       if path.name <= as_of and (path / "memberships.csv").is_file()),
                      key=lambda path: path.name)
    if not observed:
        result["summary"]["industry_classification_as_of"] = None
        result["summary"]["industry_classified"] = 0
        return result
    source = observed[-1]
    labels = pd.read_csv(source / "memberships.csv", dtype={"symbol": str}, keep_default_na=False)
    if not {"symbol", "sector", "industry_name"}.issubset(labels) or labels.symbol.duplicated().any():
        raise ValueError(f"{source.name} 산업 구성의 필드나 종목 중복을 확인하세요.")
    by_symbol = labels.set_index("symbol")[["sector", "industry_name"]].to_dict("index")
    classified = 0
    for group in ("members", "added", "removed", "excluded"):
        for row in result.get(group, []):
            label = by_symbol.get(row["symbol"])
            if label and label["sector"] and label["industry_name"]:
                row["sector"] = label["sector"]
                row["industry_name"] = label["industry_name"]
                if group == "members":
                    classified += 1
    result["summary"]["industry_classification_as_of"] = source.name
    result["summary"]["industry_classified"] = classified
    return result


def account_universe(context, workspace, as_of, state):
    """Materialize one account's selection plus held symbols for continuing management."""
    selection = read_selection(context.root, as_of)
    folder = selection_home(context.root) / as_of / "account-universes"
    target = folder / f"{workspace['id']}.csv"
    if target.exists():
        return target
    members = pd.DataFrame(selection["members"])
    if members.empty:
        raise ValueError("날짜별 후보군이 비어 있습니다.")
    rules, _ = context.configuration(workspace)
    if rules.entry_method == "setup":
        memberships = Path(context.root) / "data" / "industries" / "snapshots" / as_of / "memberships.csv"
        peer = Path(context.root) / "data" / "industries" / "peer-returns.csv"
        if not memberships.is_file() or not peer.is_file():
            raise ValueError(f"{as_of} 산업 구성과 동료 기록을 먼저 수집하세요.")
        labels = pd.read_csv(memberships, dtype={"symbol": str}, keep_default_na=False)
        labels = labels.drop_duplicates("symbol")
        members = members.rename(columns={"sector": "sector_original"})
        members = members.merge(labels.drop(columns=["name", "market", "benchmark"], errors="ignore"),
                                on="symbol", how="left", validate="one_to_one")
        missing = members.sector.isna()
        members.loc[missing, "sector"] = "UNCLASSIFIED:" + members.loc[missing, "symbol"]
        members["industry_peer_history"] = str(peer.resolve())
    members["candidate_active"] = True
    held = set((state or {}).get("positions", {})) | {
        row["symbol"] for row in (state or {}).get("pending", []) if row.get("symbol")
    }
    if held:
        previous = (state or {}).get("decision_universe_path") or context.path(workspace["universe"])
        prior = pd.read_csv(previous, dtype={"symbol": str, "benchmark": str}, keep_default_na=False)
        prior = prior[prior.symbol.isin(held - set(members.symbol))].copy()
        if held - set(members.symbol) - set(prior.symbol):
            raise ValueError("보유 종목의 이전 유니버스 기록이 없어 계좌 처리를 보류했습니다.")
        if not prior.empty:
            prior["candidate_active"] = False
            members = pd.concat([members, prior], ignore_index=True)
    folder.mkdir(parents=True, exist_ok=True)
    members.sort_values("symbol").to_csv(target, index=False, encoding="utf-8-sig")
    return target


def acquisition_universe(context, workspace, decision_path):
    baseline = pd.read_csv(context.path(workspace["universe"]), dtype={"symbol": str, "benchmark": str}, keep_default_na=False)
    selected = pd.read_csv(decision_path, dtype={"symbol": str, "benchmark": str}, keep_default_na=False)
    combined = pd.concat([baseline, selected], ignore_index=True)
    combined = combined.drop_duplicates("symbol", keep="last")
    target = context.path(workspace["state"]).parent / "acquisition-universe.csv"
    combined.to_csv(target, index=False, encoding="utf-8-sig")
    return target


def backfill_added_history(data_dir, decision_path, as_of, progress=None):
    """Add older bars only for newly selected symbols; preserve all existing bars."""
    from .market import acquire_research
    from .research_store import publish_research

    folder = Path(data_dir)
    prices_path = folder / "prices.csv"
    if not prices_path.exists():
        return []
    selected = pd.read_csv(decision_path, dtype={"symbol": str, "benchmark": str})
    selected = selected[selected.candidate_active.eq(True)] if "candidate_active" in selected else selected
    known = set(pd.read_csv(prices_path, usecols=["symbol"], dtype={"symbol": str}).symbol)
    added = selected[~selected.symbol.isin(known)]
    if added.empty:
        return []
    if progress:
        progress(f"새 후보 {len(added)}종목의 과거 일봉 준비 중")
    with tempfile.TemporaryDirectory(prefix=".candidate-history-", dir=folder) as temporary:
        stage = Path(temporary)
        added.to_csv(stage / "universe.csv", index=False, encoding="utf-8-sig")
        report = acquire_research(stage / "universe.csv", stage / "research", "2020-01-01", as_of,
                                  required_session=as_of)
        if not report.get("published") or report.get("errors"):
            raise ValueError("새 후보의 과거 일봉을 모두 가져오지 못했습니다. 계좌 처리는 시작하지 않았습니다.")
        fresh = pd.read_csv(stage / "research" / "prices.csv", dtype={"symbol": str})
        fresh = fresh[fresh.symbol.isin(set(added.symbol))]
        if set(added.symbol) - set(fresh.symbol):
            raise ValueError("새 후보의 과거 일봉이 부족합니다. 계좌 처리는 시작하지 않았습니다.")
        existing = pd.read_csv(prices_path, dtype={"symbol": str})
        combined = pd.concat([existing, fresh], ignore_index=True).sort_values(["symbol", "date"])
        if combined.duplicated(["date", "symbol"]).any():
            raise ValueError("새 후보 일봉이 기존 자료와 중복됩니다. 계좌는 보존되었습니다.")
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        manifest.update(source="saved prices + Naver initial history for newly selected symbols",
                        added_history_symbols=sorted(added.symbol.tolist()), rows=len(combined), published=True,
                        collected_symbols=int(combined.symbol.nunique()),
                        universe_sha256=hashlib.sha256(Path(decision_path).read_bytes()).hexdigest(),
                        end=max(str(combined.date.max()), str(manifest.get("end", ""))))
        publish_research(folder, combined, manifest)
    return sorted(added.symbol.tolist())


def capture_selection(root, as_of, now, progress=None):
    """Capture only the latest completed session; never label current master as older history."""
    expected = latest_completed_session(now)
    if as_of != expected:
        raise ValueError(f"현재 마스터로는 직전 완료 거래일 {expected} 후보군만 선정할 수 있습니다.")
    home = selection_home(root)
    target = home / as_of
    if target.exists():
        return read_selection(root, as_of)["summary"]
    if now.date().isoformat() != as_of:
        raise ValueError(
            f"{as_of} 후보군 기록이 없습니다. 현재 종목 마스터를 과거 날짜의 목록으로 소급할 수 없습니다. "
            "거래일 장 마감 후 후보군을 저장하면 다음 장중에 해당 목록을 사용할 수 있습니다."
        )
    home.mkdir(parents=True, exist_ok=True)
    previous_dates = available_selections(root)
    previous = read_selection(root, previous_dates[0]) if previous_dates else None
    metadata = Path(root) / "data" / "market" / "universe-metadata.json"
    settings = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
    threshold = float(settings.get("min_recent_turnover_proxy", 1e9))
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=home) as temporary:
        stage = Path(temporary)
        if progress:
            progress("KIS 종목 마스터 갱신 중")
        try:
            frames = download_master(stage / "master")
        except requests.RequestException as exc:
            raise ValueError("KIS 종목 마스터를 가져오지 못했습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.") from exc
        if progress:
            progress("후보 선정 기준 적용 중")
        members, excluded = build_universe(frames, threshold)
        if members.empty:
            raise ValueError("선정된 종목이 없어 후보군 기록을 저장하지 않았습니다.")
        members = members.sort_values("symbol")
        before = {r["symbol"]: r for r in previous["members"]} if previous else {}
        after = {r["symbol"]: r for r in members.to_dict("records")}
        rejected = {r["symbol"]: r.get("reasons", "") for r in excluded.to_dict("records")}
        added = [dict(after[symbol], reason="신규 편입") for symbol in sorted(after.keys() - before.keys())]
        removed = [dict(before[symbol], reason=rejected.get(symbol) or "마스터에서 제외·확인 불가")
                   for symbol in sorted(before.keys() - after.keys())]
        members.to_csv(stage / "universe.csv", index=False, encoding="utf-8-sig")
        excluded.reindex(columns=[*members.columns, "reasons"]).to_csv(
            stage / "excluded.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(added, columns=[*members.columns, "reason"]).to_csv(stage / "added.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(removed, columns=[*members.columns, "reason"]).to_csv(stage / "removed.csv", index=False, encoding="utf-8-sig")
        summary = dict(as_of=as_of, captured_at=now.isoformat(), source="KIS market master",
                       count=len(members), added=len(added), removed=len(removed),
                       previous_as_of=previous_dates[0] if previous_dates else None,
                       comparison_same_source=bool(previous and previous["summary"].get("source") == "KIS market master"),
                       min_recent_turnover_proxy=threshold,
                       comparison_available=bool(previous))
        (stage / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _publish_directory(stage, target)
    return summary


def capture_krx_selection(root, as_of, now, progress=None, source="krx_open_api"):
    """Save a historical KRX or pykrx selection without replacing existing snapshots."""
    if source == "krx_open_api":
        from .krx_open_api import fetch_historical_universe
        label = "KRX Open API"
        basis = "historical basic info + same-day regular trading"
        note = "KIS 마스터 선정 702종목과 기준이 다릅니다. KRX 보통주·당일 거래대금 기준입니다."
    elif source == "pykrx":
        from .pykrx_universe import fetch_historical_universe
        label = "pykrx"
        basis = "historical ticker list + same-day KRX trading; ticker-cache names"
        note = ("KIS 마스터 선정과 기준이 다릅니다. pykrx 날짜별 종목 목록·당일 거래대금 기준이며 "
                "종목명은 조회 시점의 목록일 수 있습니다. 관리종목·저유동성 표시는 확인하지 못합니다.")
    else:
        raise ValueError("지원하지 않는 과거 후보군 원천입니다.")

    day = date.fromisoformat(as_of)
    if not calendar(day.year).is_session(day) or as_of > latest_completed_session(now):
        raise ValueError("완료된 거래일만 과거 후보군으로 조회할 수 있습니다.")
    home = selection_home(root)
    target = home / as_of
    if target.exists():
        if not (target / "summary.json").is_file():
            raise ValueError(f"{as_of} 후보군 저장 폴더가 불완전합니다. 확인 후 다시 시도하세요.")
        return read_selection(root, as_of)["summary"]
    metadata = Path(root) / "data" / "market" / "universe-metadata.json"
    settings = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
    threshold = float(settings.get("min_recent_turnover_proxy", 1e9))
    members, excluded = fetch_historical_universe(root, as_of, threshold, progress)
    earlier = [item for item in available_selections(root) if item < as_of]
    previous_date = earlier[0] if earlier else None
    previous = read_selection(root, previous_date) if previous_date else None
    before = {r["symbol"]: r for r in previous["members"]} if previous else {}
    members = members.sort_values("symbol")
    after = {r["symbol"]: r for r in members.to_dict("records")}
    rejected = {r["symbol"]: r.get("reasons", "") for r in excluded.to_dict("records")}
    added = [dict(after[symbol], reason="신규 편입") for symbol in sorted(after.keys() - before.keys())]
    removed = [dict(before[symbol], reason=rejected.get(symbol) or f"{label} 종목·거래 자료에서 확인 불가")
               for symbol in sorted(before.keys() - after.keys())]
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".candidate-historical-", dir=home) as temporary:
        stage = Path(temporary)
        members.to_csv(stage / "universe.csv", index=False, encoding="utf-8-sig")
        excluded.reindex(columns=[*members.columns, "reasons"]).to_csv(
            stage / "excluded.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(added, columns=[*members.columns, "reason"]).to_csv(
            stage / "added.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(removed, columns=[*members.columns, "reason"]).to_csv(
            stage / "removed.csv", index=False, encoding="utf-8-sig")
        summary = dict(
            as_of=as_of, captured_at=now.isoformat(), source=label,
            basis=basis,
            count=len(members), added=len(added), removed=len(removed),
            previous_as_of=previous_date, comparison_available=bool(previous),
            comparison_same_source=bool(previous and previous["summary"].get("source") == label),
            min_daily_turnover=threshold,
            unavailable_kis_filters=["KIS low liquidity flag", "KIS industry classification"],
            note=note,
        )
        (stage / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _publish_directory(stage, target)
    return summary


def capture_krx_missing(root, now, progress=None, count=5, from_as_of=None, source="krx_open_api"):
    """Fill selected missing completed sessions in chronological order."""
    if source not in {"krx_open_api", "pykrx"}:
        raise ValueError("지원하지 않는 과거 후보군 원천입니다.")
    latest = latest_completed_session(now)
    exchange = calendar(int(latest[:4]))
    if from_as_of:
        try:
            start = date.fromisoformat(from_as_of)
        except (TypeError, ValueError):
            raise ValueError("누락 후보군 시작일을 YYYY-MM-DD로 입력하세요.") from None
        if (start.isoformat() != from_as_of or from_as_of > latest
                or start < date.fromisoformat(latest) - timedelta(days=180)
                or not exchange.is_session(start)):
            raise ValueError("누락 후보군 시작일은 완료된 거래일로 선택하세요.")
        sessions = [item.date().isoformat() for item in exchange.sessions_in_range(start, latest)]
        if len(sessions) > 120:
            raise ValueError("한 번에 120거래일까지 조회할 수 있습니다. 시작일을 조정하세요.")
    else:
        sessions = [latest]
        for _ in range(count - 1):
            sessions.append(exchange.previous_session(sessions[-1]).date().isoformat())
        sessions.reverse()
    available = set(available_selections(root))
    missing = [day for day in sessions if day not in available]
    completed = []
    for index, day in enumerate(missing, 1):
        if progress:
            progress(f"{day} {source} 과거 후보군 저장 중", index - 1, len(missing))
        capture_krx_selection(root, day, now, progress, source=source)
        completed.append(day)
    if progress:
        progress(f"{source} 과거 후보군 저장 완료", len(completed), len(missing))
    return dict(source="pykrx" if source == "pykrx" else "KRX Open API", checked_dates=sessions, saved_dates=completed,
                existing_dates=[day for day in sessions if day in available])
