function renderSignals() {
  const s = snapshot;
  table(
    "pending-table",
    [
      ["종목", (r) => r.name || r.symbol],
      ["코드", (r) => r.symbol],
      [
        "유형",
        (r) => (r.order_intent === "add" ? "추가매수 계획" : "신규 진입 계획"),
      ],
      ["신호일", (r) => String(r.date).slice(0, 10)],
      ["참고 가격", (r) => number(r.reference_price)],
      ["참고 손절", (r) => number(r.planned_common_stop ?? r.stop_price)],
    ],
    s.pending,
    "다음 시가 진입·추가매수 대기가 없습니다.",
  );
  table(
    "position-table",
    [
      ["종목", (r) => r.symbol],
      ["수량", (r) => number(r.quantity)],
      ["최초 매수가", (r) => number(r.entry_price)],
      ["평균 매수가", (r) => number(r.average_entry_price ?? r.entry_price)],
      ["평가 가격", (r) => number(r.last_price)],
      ["공통 손절", (r) => number(r.stop_price)],
      ["3R 기준", (r) => number(r.target_3r, 2)],
      ["최초 위험", (r) => number(r.initial_risk)],
      ["추가 횟수", (r) => number(r.addition_count ?? 0)],
      [
        "청산 대기",
        (r) =>
          r.rs_exit_pending
            ? "RS 약화"
            : r.exit_pending
              ? "가격·자료 확인"
              : "없음",
      ],
    ],
    s.positions,
    "보유 종목이 없습니다.",
  );
  table(
    "event-table",
    [
      ["일자", (r) => r.date],
      ["종목", (r) => r.symbol],
      ["이벤트", (r) => labels[r.kind] || r.kind],
      ["검토", (r) => r.review_status || "—"],
      ["사유", (r) => r.review_reasons || r.reason || "—"],
    ],
    s.logs.events || [],
  );
  table(
    "order-table",
    [
      ["일자", (r) => r.date],
      ["종목", (r) => r.symbol],
      [
        "유형",
        (r) =>
          r.order_intent === "add"
            ? "추가매수"
            : r.side === "sell"
              ? "청산"
              : "진입",
      ],
      ["결과", (r) => labels[r.status] || r.status],
      ["수량", (r) => number(r.quantity)],
      ["모의 체결가", (r) => number(r.fill_price)],
      ["사유", (r) => r.reason || "—"],
    ],
    s.logs.orders || [],
  );
  table(
    "trade-table",
    [
      ["종목", (r) => r.symbol],
      ["진입일", (r) => r.entry_date],
      ["청산일", (r) => r.exit_date],
      ["수량", (r) => number(r.quantity)],
      ["손익", (r) => number(r.pnl)],
      ["최초 위험 기준 R", (r) => number(r.r_multiple, 2)],
      ["청산 사유", (r) => r.exit_reason],
    ],
    s.logs.trades || [],
  );
}
function renderValidation() {
  const s = snapshot,
    r = s.rules;
  facts("validation-facts", [
    ["데이터 기준일", s.as_of || "분석 전"],
    ["계좌 처리일", s.account_as_of || "미시작"],
    [
      "격리 가격 행",
      s.analysis.quarantined_rows === undefined
        ? "일별 report.json 확인"
        : number(s.analysis.quarantined_rows),
    ],
    ["RS 약화 청산", r.rs_exit_enabled ? "ON" : "OFF"],
    ["시장 유닛", r.market_units_enabled ? "ON" : "OFF"],
    [
      "추가매수",
      r.pyramiding_enabled
        ? `ON · 최대 ${r.pyramiding_max_additions}회`
        : "OFF",
    ],
    ["초기 손절", r.initial_stop_method + " / " + r.initial_stop_reference],
    ["15% 폭 제한", r.stop_limit_enabled ? "ON" : "OFF"],
    ["최대 거래 위험", percent(s.limits.max_trade_risk) + " · 절대 상한 2%"],
  ]);
  table(
    "risk-table",
    [
      ["종목", (r) => r.symbol],
      ["순자산", (r) => number(r.equity)],
      ["공통 손절", (r) => number(r.common_stop)],
      ["계획 위험", (r) => number(r.planned_stop_risk)],
      ["운용 한도", (r) => number(r.operating_risk_limit)],
      [
        "한도 초과",
        (r) =>
          flag(
            r.operating_risk_exceeded === undefined
              ? null
              : !r.operating_risk_exceeded,
          ),
      ],
      ["시세 누락", (r) => (r.stale_price ? "확인 필요" : "없음")],
    ],
    s.logs["campaign-risk"] || [],
    "위험 기록이 없습니다. 해당 기능 적용 여부와 모의 처리 상태를 확인하세요.",
  );
  table(
    "review-table",
    [
      ["일자", (r) => r.date],
      ["종목", (r) => r.symbol],
      [
        "검토 결과",
        (r) => (r.eligible_for_risk_review ? "위험 검토 후보" : "보류"),
      ],
      ["기존 손절", (r) => number(r.holding_stop)],
      ["추가분 예상 손절", (r) => number(r.proposed_stop_price)],
      ["사유", (r) => r.review_reasons || r.risk_aggregation_status],
    ],
    s.logs["pyramiding-review"] || [],
  );
}
