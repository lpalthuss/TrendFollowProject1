"""Date-specific KRX Open API market membership for historical candidate snapshots."""

import os
from pathlib import Path
import re

from dotenv import dotenv_values
import pandas as pd
import requests


BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKETS = {
    "KOSPI": ("stk_isu_base_info", "stk_bydd_trd", "069500"),
    "KOSDAQ": ("ksq_isu_base_info", "ksq_bydd_trd", "229200"),
}
SERVICE_NAMES = {
    "stk_isu_base_info": "유가증권 종목기본정보",
    "ksq_isu_base_info": "코스닥 종목기본정보",
    "stk_bydd_trd": "유가증권 일별매매정보",
    "ksq_bydd_trd": "코스닥 일별매매정보",
}


def _key(root):
    values = dotenv_values(Path(root) / ".env", interpolate=False)
    key = os.environ.get("KRX_OPEN_API_KEY") or values.get("KRX_OPEN_API_KEY")
    if not key:
        raise ValueError("KRX Open API 이용에는 .env의 KRX_OPEN_API_KEY가 필요합니다.")
    return key


def _request(api, day, key):
    try:
        response = requests.get(
            f"{BASE}/{api}", params={"basDd": day.replace("-", "")},
            headers={"AUTH_KEY": key}, timeout=30,
        )
        if response.status_code in (401, 403):
            service = SERVICE_NAMES.get(api, api)
            raise ValueError(
                f"KRX Open API '{service}'({api}) 이용이 거절됐습니다 ({response.status_code}). "
                "KRX Open API에서 해당 서비스의 이용 신청·승인 상태를 확인하세요."
            )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ValueError(f"KRX Open API {api} 연결에 실패했습니다. 네트워크를 확인하세요.") from exc
    except ValueError as exc:
        if "KRX Open API" in str(exc):
            raise
        raise ValueError(f"KRX Open API {api} 응답을 해석할 수 없습니다.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"KRX Open API {api} 응답 형식이 올바르지 않습니다.")
    if payload.get("respCode") not in (None, "200", 200):
        raise ValueError(f"KRX Open API {api} 조회가 거절됐습니다 (코드 {payload['respCode']}).")
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{day} KRX Open API {api} 결과가 비어 있습니다. 저장하지 않았습니다.")
    return rows


def _number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def fetch_historical_universe(root, day, threshold, progress=None):
    """Build a KRX-based universe; KIS-only status and industry fields are unavailable."""
    key = _key(root)
    members, excluded = [], []
    for market, (basic_api, trade_api, benchmark) in MARKETS.items():
        if progress:
            progress(f"{day} {market} 종목 기본정보·일별 거래 조회 중")
        basics = _request(basic_api, day, key)
        trades = _request(trade_api, day, key)
        if any(row.get("BAS_DD") and str(row["BAS_DD"]).replace("/", "").replace("-", "") != day.replace("-", "")
               for row in trades):
            raise ValueError(f"{day} KRX Open API 일별 거래 응답의 날짜가 다릅니다. 저장하지 않았습니다.")
        trading = {str(row.get("ISU_CD", "")).strip(): row for row in trades}
        if len(trading) < max(1, len(basics) // 2):
            raise ValueError(f"{day} {market} 일별 거래 응답의 종목 수가 부족합니다. 저장하지 않았습니다.")
        for info in basics:
            symbol = str(info.get("ISU_SRT_CD", "")).strip()
            if not re.fullmatch(r"[0-9A-Z]{6}", symbol):
                continue
            trade = trading.get(symbol) or trading.get(str(info.get("ISU_CD", "")).strip())
            kind = str(info.get("KIND_STKCERT_TP_NM", "")).strip()
            group = str(info.get("SECUGRP_NM", "")).strip()
            section = str(info.get("SECT_TP_NM", "")).strip()
            name = str(info.get("ISU_NM") or info.get("ISU_ABBRV") or symbol).strip()
            turnover = _number(trade.get("ACC_TRDVAL")) if trade else 0.0
            volume = _number(trade.get("ACC_TRDVOL")) if trade else 0.0
            open_price = _number(trade.get("TDD_OPNPRC")) if trade else 0.0
            reasons = []
            if group not in {"주권", "주식"} or not kind.startswith("보통주"):
                reasons.append("not_common_stock")
            if re.search(r"스팩|SPAC|기업인수목적", name, re.IGNORECASE):
                reasons.append("spac_name")
            if "관리" in section or "투자주의환기" in section:
                reasons.append("administrative_section")
            if not trade or volume <= 0 or open_price <= 0:
                reasons.append("no_regular_trading")
            if turnover < threshold:
                reasons.append("daily_turnover_below_threshold")
            row = dict(
                symbol=symbol, name=name, market=market,
                sector=f"UNCLASSIFIED:{symbol}", benchmark=benchmark,
                recent_turnover_proxy=turnover,
            )
            if reasons:
                excluded.append(dict(row, reasons=",".join(reasons)))
            else:
                members.append(row)
    if not members:
        raise ValueError(f"{day} KRX Open API 후보군이 비어 있습니다. 필드와 서비스 승인을 확인하세요.")
    frame = pd.DataFrame(members)
    if frame.symbol.duplicated().any():
        raise ValueError(f"{day} KRX Open API 응답에 중복 종목이 있습니다. 저장하지 않았습니다.")
    return frame, pd.DataFrame(excluded)
