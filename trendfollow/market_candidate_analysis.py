"""Read-only analysis of saved dated market selections."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import time

import pandas as pd
import requests

from .backtest import features
from .candidate_universe import available_selections, read_selection, selection_home
from .config import Rules
from .market import NaverResearchSource
from .research_store import require_published_research
from .relative_strength import multi_period_daily_rs

MULTI_RS_VERSION = 1


def analysis_home(root, workspace):
    return Path(root) / "outputs" / "ui" / "market-analyses" / workspace


def latest_analysis(root, workspace, as_of):
    folder = analysis_home(root, workspace) / as_of
    runs = [p for p in folder.iterdir() if (p / "signals.csv").is_file()
            and (p / "analysis.json").is_file()] if folder.exists() else []
    return max(runs, key=lambda p: p.stat().st_mtime_ns) if runs else None


def _industry_universe(root, as_of, members):
    snapshots = Path(root) / "data" / "industries" / "snapshots"
    available = sorted((p for p in snapshots.glob("????-??-??")
                        if (p / "memberships.csv").is_file()), key=lambda p: p.name)
    if not available:
        raise ValueError("산업 구성 기록이 없습니다. 산업 자료 갱신을 먼저 실행하세요.")
    prior = [p for p in available if p.name <= as_of]
    # The first archived membership may have been captured after the trading day.
    # Use it as an explicit historical proxy, never as an observed same-day list.
    observed = prior[-1] if prior else available[0]
    labels = pd.read_csv(observed / "memberships.csv", dtype={"symbol": str}, keep_default_na=False)
    if labels.symbol.duplicated().any() or "sector" not in labels:
        raise ValueError(f"{observed.name} 산업 구성에 중복 또는 필드 누락이 있습니다.")
    universe = members.drop(columns=["sector", "industry_name"], errors="ignore").merge(
        labels[["symbol", "sector", "industry_name"]], on="symbol", how="left", validate="one_to_one")
    missing = universe.sector.isna() | universe.sector.eq("")
    universe.loc[missing, "sector"] = "UNCLASSIFIED:" + universe.loc[missing, "symbol"]
    peer = Path(root) / "data" / "industries" / "peer-returns.csv"
    if not peer.is_file():
        raise ValueError("산업 동료 수익률 자료가 없습니다. 산업 갱신을 먼저 실행하세요.")
    universe["industry_peer_history"] = str(peer.resolve())
    return universe, observed.name, int(missing.sum())


def analyze_saved_selections(context, workspace, through, run_id, progress=None):
    """Analyze every saved selection through `through` using one account's rules, without changing that account."""
    dates = sorted(day for day in available_selections(context.root) if day <= through)
    if through not in dates:
        raise ValueError(f"{through} 날짜별 시장 후보군이 없습니다. 후보군을 먼저 저장하세요.")
    config_path = context.path(workspace["config"])
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    rules = Rules(**json.loads(config_path.read_text(encoding="utf-8-sig")).get("rules", {}))
    rules.validate()
    pending = []
    for day in dates:
        selection_path = selection_home(context.root) / day / "universe.csv"
        selection_hash = hashlib.sha256(selection_path.read_bytes()).hexdigest()
        prior = latest_analysis(context.root, workspace["id"], day)
        if prior:
            meta = json.loads((prior / "analysis.json").read_text(encoding="utf-8"))
            if (meta.get("config_sha256") == config_hash
                    and meta.get("selection_sha256") == selection_hash
                    and meta.get("multi_rs_version") == MULTI_RS_VERSION):
                continue
        pending.append((day, selection_hash))
    if not pending:
        return dict(status="already_present", checked_dates=dates, analyzed_dates=[])
    source_workspace = (context.workspace(workspace["source_workspace"])
                        if workspace.get("mode") == "recalculation" and workspace.get("source_workspace")
                        else workspace)
    prices_path = context.path(source_workspace["data_dir"]) / "prices.csv"
    require_published_research(prices_path)
    start = (date.fromisoformat(pending[0][0]) - timedelta(days=730)).isoformat()
    if progress:
        progress("시장 후보군의 기존 일봉 확인 중", 0, len(pending))
    selections = {day: pd.DataFrame(read_selection(context.root, day)["members"])
                  for day, _ in pending}
    # Validate industry inputs before fetching hundreds of historical price series.
    industry_inputs = {day: _industry_universe(context.root, day, members)
                       for day, members in selections.items()}
    required_symbols = set().union(*(set(frame.symbol) | set(frame.benchmark)
                                     for frame in selections.values()))
    required_symbols.add("KOSPI")
    if rules.mansfield_benchmark == "market_index":
        required_symbols |= {"KOSPI", "KOSDAQ"}
    base = pd.read_csv(prices_path, dtype={"symbol": str, "date": str})
    base = base[base.date.between(start, through) & base.symbol.isin(required_symbols)]
    extra_path = Path(context.root) / "outputs" / "ui" / "market-analyses" / "extra-prices.csv"
    extra = pd.read_csv(extra_path, dtype={"symbol": str, "date": str}) if extra_path.is_file() else pd.DataFrame(columns=base.columns)
    extra = extra[extra.date.between(start, through) & extra.symbol.isin(required_symbols)]
    prices = pd.concat([base, extra], ignore_index=True).drop_duplicates(["date", "symbol"], keep="first")
    existing = set(zip(prices.date, prices.symbol))
    fetch_symbols = sorted({symbol for day, frame in selections.items() for symbol in frame.symbol
                            if (day, symbol) not in existing})
    if fetch_symbols:
        source = NaverResearchSource(extra_path.parent / "raw")
        fresh = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(source.daily, symbol, start, through): symbol for symbol in fetch_symbols}
            for number, future in enumerate(as_completed(futures), 1):
                symbol = futures[future]
                try:
                    frame = future.result()
                except (ValueError, requests.RequestException) as exc:
                    for remaining in futures:
                        remaining.cancel()
                    raise ValueError(f"{symbol} 새 후보의 과거 일봉 수집에 실패했습니다: {exc}") from exc
                if not frame.empty:
                    fresh.append(frame)
                if progress and (number % 10 == 0 or number == len(fetch_symbols)):
                    progress(f"새 후보 과거 일봉 수집 중 · {number}/{len(fetch_symbols)}", number, len(fetch_symbols))
        if fresh:
            fresh = pd.concat(fresh, ignore_index=True)
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            stored = pd.read_csv(extra_path, dtype={"symbol": str, "date": str}) if extra_path.is_file() else pd.DataFrame(columns=fresh.columns)
            stored = pd.concat([stored, fresh], ignore_index=True).drop_duplicates(["date", "symbol"], keep="first")
            with tempfile.NamedTemporaryFile(dir=extra_path.parent, suffix=".csv", delete=False) as handle:
                temporary = Path(handle.name)
            try:
                stored.to_csv(temporary, index=False, encoding="utf-8-sig")
                temporary.replace(extra_path)
            finally:
                temporary.unlink(missing_ok=True)
            prices = pd.concat([prices, fresh], ignore_index=True).drop_duplicates(["date", "symbol"], keep="first")
    existing = set(zip(prices.date, prices.symbol))
    kospi_prices = prices[prices.symbol.eq("KOSPI")].copy()
    kospi_prices["date"] = pd.to_datetime(kospi_prices.date)
    kospi_close = kospi_prices.set_index("date").close
    for day, frame in selections.items():
        missing = sorted(symbol for symbol in frame.symbol if (day, symbol) not in existing)
        if missing:
            raise ValueError(f"{day} 시장 후보 {len(missing)}종목의 일봉이 없습니다 ({', '.join(missing[:5])}). 계좌는 변경되지 않았습니다.")
    completed = []
    for index, (day, selection_hash) in enumerate(pending, 1):
        if progress:
            progress(f"{day} 시장 후보 조건 계산 중", index - 1, len(pending))
        universe, membership_day, unclassified = industry_inputs[day]
        symbols = set(universe.symbol) | set(universe.benchmark)
        if rules.mansfield_benchmark == "market_index":
            symbols |= {"KOSPI", "KOSDAQ"}
        frame, invalid = features(prices[prices.date.le(day) & prices.symbol.isin(symbols)], universe, rules)
        today = frame[frame.date.eq(pd.Timestamp(day))].copy()
        daily = prices[prices.date.le(day) & prices.symbol.isin(set(today.symbol))].copy()
        daily["date"] = pd.to_datetime(daily.date)
        by_symbol = {symbol: multi_period_daily_rs(group.set_index("date").close, kospi_close.loc[:day])
                     for symbol, group in daily.groupby("symbol")}
        for key in ["multi_rs", "multi_rs_raw", "multi_rs_windows", "multi_rs_as_of"]:
            today[key] = today.symbol.map({symbol: value[key] for symbol, value in by_symbol.items()})
        if set(universe.symbol) - set(today.symbol):
            raise ValueError(f"{day} 후보 조건 계산 결과에 종목이 빠졌습니다. 저장하지 않았습니다.")
        folder = analysis_home(context.root, workspace["id"]) / day
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".market-analysis-", dir=folder) as temporary:
            stage = Path(temporary)
            today.to_csv(stage / "signals.csv", index=False, encoding="utf-8-sig")
            meta = dict(as_of=day, mode="market_candidate_research", source=read_selection(context.root, day)["summary"]["source"],
                        count=len(today), signals=int(today.signal.sum()), invalid_bars=len(invalid),
                        industry_membership_observed_at=membership_day,
                        industry_membership_reconstructed=membership_day > day,
                        unclassified_industries=unclassified,
                        price_source="saved account prices + Naver initial history for missing symbols",
                        config_sha256=config_hash, selection_sha256=selection_hash,
                        multi_rs_version=MULTI_RS_VERSION,
                        generated_at=context.now().isoformat())
            (stage / "analysis.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(5):
                try:
                    stage.rename(folder / run_id)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.2 * (attempt + 1))
        completed.append(day)
        if progress:
            progress(f"{day} 시장 후보 분석 저장 완료", index, len(pending))
    return dict(status="completed", checked_dates=dates, analyzed_dates=completed,
                market_research=True, account_unchanged=True)
