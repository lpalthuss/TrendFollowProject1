function candidateFilters() {
  return {
    industry: $("filter-industry").value,
    rs: $("filter-rs").value,
    action: $("filter-action").value,
    setupPhase: $("filter-setup-phase").value,
    setup: $("filter-setup").value,
    high52: $("filter-high52").value,
    signal: $("filter-signal").value,
    volume: $("filter-volume").value,
    search: $("search").value,
  };
}
const candidateFilterIds = {
  rs: "filter-rs",
  action: "filter-action",
  setupPhase: "filter-setup-phase",
  setup: "filter-setup",
  high52: "filter-high52",
  signal: "filter-signal",
  volume: "filter-volume",
};
function normalizeCandidateFilters() {
  let filters = candidateFilters();
  for (let count = 0; count < 7; count++) {
    const conflict = TrendFollowFilters.candidateConflict(
      filters,
      snapshot.rules,
    );
    if (!conflict) break;
    $(candidateFilterIds[conflict.fields.at(-1)]).value = "all";
    filters = candidateFilters();
  }
  return filters;
}
function updateCandidateOptions(filters) {
  const disabled = [];
  for (const [key, id] of Object.entries(candidateFilterIds)) {
    const select = $(id);
    for (const option of select.options) {
      const conflict =
        option.value === "all"
          ? null
          : TrendFollowFilters.candidateConflict(
              { ...filters, [key]: option.value },
              snapshot.rules,
            );
      option.disabled = Boolean(conflict);
      if (
        conflict &&
        conflict.fields.some(
          (other) => other !== key && filters[other] !== "all",
        )
      )
        disabled.push(
          `${select.getAttribute("aria-label")} → ${option.textContent}: ${conflict.message}`,
        );
    }
  }
  return [...new Set(disabled)];
}
function renderSetupHelp() {
  const rules = snapshot.rules;
  const high52Help = $("filter-high52-help");
  const high52Card = $("filter-high52-card");
  high52Card.hidden = $("filter-high52").value === "all";
  if (!high52Card.hidden) {
    const selected = $("filter-high52").selectedOptions[0].textContent;
    const role =
      rules.entry_method === "setup" && rules.setup_52w_enabled
        ? "현재 계좌에서는 파동 셋업과 별도인 매수 후보 경로입니다."
        : rules.entry_method === "breakout" && rules.breakout_days === 252
          ? "현재 계좌의 252일 고가 돌파 조건과 같은 기준입니다."
          : "현재 계좌의 매수 신호에는 반영되지 않습니다.";
    high52Help.textContent = `52주 신고가 → ${selected}: 오늘 종가와 직전 252거래일 최고가를 비교합니다. ${role}`;
  }
  const phaseChoice = $("filter-setup-phase").value;
  const conditionChoice = $("filter-setup").value;
  $("phase-example").dataset.phase = phaseChoice;
  $("phase-example").dataset.condition = conditionChoice;
  document
    .querySelectorAll(
      "#phase-example [data-phase-choice], #phase-example [data-condition-choice]",
    )
    .forEach((choice) => {
      const selected = choice.dataset.phaseChoice
        ? choice.dataset.phaseChoice === phaseChoice &&
          (phaseChoice !== "all" || conditionChoice === "all")
        : choice.dataset.conditionChoice === conditionChoice;
      choice.setAttribute("aria-pressed", String(selected));
    });
  if (rules.entry_method !== "setup") {
    $("phase-example").hidden = true;
    $("filter-setup-help").textContent =
      "현재 계좌는 파동 셋업 전략을 사용하지 않습니다. 셋업 단계와 조건은 표시되지 않습니다.";
    return;
  }
  $("phase-example").hidden = false;
  $("phase-example-caption").textContent =
    "그림의 구간을 클릭하면 필터가 바뀝니다. 다시 클릭하면 해제됩니다. " +
    (phaseChoice === "untradable"
      ? "거래량이 0인 날에는 셋업 계산을 중단하고 파동 탐색을 다시 시작합니다."
      : conditionChoice === "trigger"
        ? "적용 중: 셋업 조건 → 셋업 돌파. 선행 파동 고점을 넘는 조건이며 ‘52주 신고가 → 돌파’ 필터와는 별개입니다."
        : "그림 ④는 ‘셋업 조건 → 셋업 돌파’에 연결됩니다. ‘52주 신고가 → 돌파’는 별도 필터입니다. 가상의 가격 흐름입니다.");
  if ($("filter-high52").value !== "all")
    $("phase-example-caption").textContent +=
      ` 현재 ‘52주 신고가 → ${$("filter-high52").selectedOptions[0].textContent}’ 필터도 함께 적용 중입니다.`;
  const phase = {
    all: "파동 단계 전체",
    seeking: `상승 파동 탐색: 저점에서 고점까지 ${number(rules.setup_min_rise_atr, 1)} ATR 이상 상승하면 진행 단계로 전환`,
    rising: `상승 파동 진행: 고점에서 종가가 ${number(rules.setup_reversal_atr, 1)} ATR 이상 하락하면 조정 단계 확인`,
    base: `조정·베이스: 파동 상승 폭 대비 반납이 ${percent(rules.setup_max_retracement)} 이하인 조정 구간`,
    untradable: "거래량 0: 해당 날짜의 셋업 계산 불가",
  }[$("filter-setup-phase").value];
  const condition = {
    all: "셋업 조건 전체",
    ready: `준비 충족: 파동 상승 폭 대비 반납 ${percent(rules.setup_max_retracement)} 이하, 베이스 관측 4거래일 이상`,
    not_ready: `준비 미충족: 베이스가 없거나 반납 ${percent(rules.setup_max_retracement)} 초과 또는 베이스 관측 4거래일 미만`,
    trigger:
      "셋업 돌파: 오늘 종가가 셋업 저항선을 넘고 전일 종가는 저항선 이하",
    invalidated: `셋업 무효화: 오늘 저가를 포함한 반납이 ${percent(rules.setup_max_retracement)} 초과`,
    unknown: "미적용·자료 없음: 셋업 전략이 아니거나 단계 자료가 없음",
  }[$("filter-setup").value];
  const conditionNote =
    conditionChoice === "invalidated"
      ? $("filter-high52").value === "pass"
        ? "두 판정은 같은 기준일에 발생할 수 있습니다. 그날 저가는 셋업 반납 한도를 넘고, 종가는 직전 252거래일 고점을 넘은 경우입니다. 52주 돌파 때문에 셋업이 무효화된 것은 아니며, 돌파 후 며칠 동안 상승했다는 뜻도 아닙니다."
        : "무효화는 선택 기준일의 저가로 판단합니다. 이전 신고가 돌파 이후 일정 기간의 상승 여부를 뜻하지 않습니다."
      : conditionChoice === "ready"
        ? `준비 충족은 최종 진입 신호가 아닙니다. 진입 판단에는 거래량 수축비 ${number(rules.setup_volume_contraction, 2)} 이하, 변동성 수축비 ${number(rules.setup_range_contraction, 2)} 이하 등 추가 조건이 적용됩니다.`
        : conditionChoice === "trigger"
          ? "셋업 돌파만으로 최종 진입 신호가 확정되지는 않습니다."
          : "셋업 조건은 최종 진입 신호와 구분합니다.";
  $("filter-setup-help").textContent =
    `${phase} · ${condition}. ${conditionNote}`;
}
function updateIndustryFilter() {
  const select = $("filter-industry"),
    old = select.value;
  select.replaceChildren();
  const all = el("option", "전체 산업");
  all.value = "all";
  select.append(all);
  const groups = new Map();
  for (const row of snapshot.rows)
    if (row.sector) groups.set(row.sector, row.industry_name || row.sector);
  for (const [id, name] of [...groups].sort((a, b) =>
    a[1].localeCompare(b[1], "ko"),
  )) {
    const op = el("option", name);
    op.value = id;
    select.append(op);
  }
  select.value = groups.has(old) ? old : "all";
  $("volume-help-text").textContent =
    `거래량 배수 = 기준일 하루 거래량 ÷ 기준일을 제외한 직전 20거래일 평균 거래량입니다. 현재 계좌는 ${number(snapshot.rules.volume_multiple, 2)}배 이상을 거래량 조건 통과로 판단합니다. 1배는 평소 수준, 2배는 평균의 두 배를 뜻합니다.`;
}
function renderStocks() {
  if (!snapshot) return;
  const filters = normalizeCandidateFilters();
  const disabled = updateCandidateOptions(filters);
  renderSetupHelp();
  let rows = TrendFollowFilters.filterRows(
    snapshot.rows,
    filters,
    snapshot.rules,
  );
  const active = Object.entries(filters).filter(([key, value]) =>
    key === "search" ? value.trim() : value !== "all",
  ).length;
  $("filter-note").textContent =
    `선택한 조건 ${active}개를 모두 만족하는 ${rows.length}종목을 표시합니다 (AND). RS는 선택 계좌의 ${snapshot.rules.rs_method === "mansfield" ? "Mansfield RS" : "RS 수익률 순위"} 기준입니다. 표시 필터는 신호·계좌를 변경하지 않습니다.`;
  $("filter-disabled-help").hidden = !disabled.length;
  $("filter-disabled-summary").textContent =
    `현재 선택 때문에 비활성화된 다른 항목 ${disabled.length}개 보기`;
  $("filter-disabled-list").replaceChildren(
    ...disabled.map((reason) => el("li", reason)),
  );
  const combinationNote = $("filter-combination-note");
  const intersection =
    filters.setup !== "all" && filters.high52 !== "all"
      ? snapshot.rules.setup_52w_enabled &&
        snapshot.rules.entry_method === "setup"
        ? "유효한 조합입니다. 셋업 조건과 52주 신고가를 모두 만족한 종목만 표시합니다(AND). 계좌의 매수 후보는 두 경로 중 하나만 통과해도 될 수 있습니다(OR). 두 경로의 최종 후보를 함께 보려면 이 두 필터를 ‘전체’로 두고 ‘최종 신호 → 통과’를 선택하세요."
        : "유효한 조합입니다. 셋업 조건과 52주 신고가를 모두 만족한 종목만 표시합니다(AND). 현재 계좌에서는 52주 신고가가 별도 매수 경로로 켜져 있지 않습니다."
      : "";
  combinationNote.hidden = !intersection;
  combinationNote.textContent = intersection;
  rows.sort(
    (a, b) =>
      (b.allocation_score ??
        (snapshot.rules.rs_method === "mansfield"
          ? b.mansfield_rs
          : b.rs_percentile) ??
        -Infinity) -
      (a.allocation_score ??
        (snapshot.rules.rs_method === "mansfield"
          ? a.mansfield_rs
          : a.rs_percentile) ??
        -Infinity),
  );
  $("row-count").textContent = `${rows.length} / ${snapshot.rows.length} 종목`;
  table(
    "stock-table",
    [
      [
        "종목",
        (r) => {
          const n = el("div"),
            b = el("button", r.name || r.symbol, "stock-link");
          b.onclick = () => detail(r);
          n.append(b, el("span", r.symbol, "code"));
          return n;
        },
      ],
      ["산업", (r) => r.industry_name || r.sector],
      ["종가", (r) => number(r.reference_price)],
      [
        "52주 신고가",
        (r) =>
          flag(
            r.prior_52w_high == null ? null : r.high52_breakout,
            "자료 부족",
          ),
      ],
      ["RS 순위", (r) => number(r.rs_percentile, 1)],
      ["Mansfield", (r) => number(r.mansfield_rs, 2)],
      ["RS 조건", (r) => flag(r.rs_pass)],
      [
        "산업 액션",
        (r) =>
          flag(
            r.sector_action,
            snapshot.rules.entry_method === "setup" ? "미확인" : "미적용",
          ),
      ],
      [
        "셋업",
        (r) => {
          const n = el(
            "div",
            TrendFollowFilters.phaseLabels[r.setup_phase] ||
              r.setup_phase ||
              "미적용·자료 없음",
          );
          if (r.setup_phase)
            n.append(
              el(
                "small",
                [
                  r.setup_ready === true
                    ? "준비 충족"
                    : r.setup_ready === false
                      ? "준비 미충족"
                      : "",
                  r.setup_trigger === true ? "셋업 돌파" : "",
                  r.setup_invalidated === true ? "무효화" : "",
                ]
                  .filter(Boolean)
                  .join(" · "),
                "code",
              ),
            );
          return n;
        },
      ],
      [
        "거래량 배수",
        (r) =>
          r.volume_ratio === null || r.volume_ratio === undefined
            ? "—"
            : number(r.volume_ratio, 2) + "배",
      ],
      ["최종 신호", (r) => flag(r.signal)],
      ["신호 경로", (r) => r.signal_entry_path || "—"],
      ["계획 수량", (r) => number(r.quantity)],
      [
        "감시",
        (r) => {
          const b = el("button", "선택", "quiet");
          b.onclick = () => {
            const set = new Set(watchSymbols());
            set.add(r.symbol);
            $("watch-symbols").value = [...set].join(", ");
            renderWatchControls();
            toast("감시 목록에 " + r.symbol + " 추가");
          };
          return b;
        },
      ],
    ],
    rows,
    snapshot.rows.length
      ? filters.setup === "trigger" &&
        !snapshot.rows.some((row) => row.setup_trigger === true)
        ? "이 기준일에 셋업 돌파가 발생한 종목이 없습니다. 다른 파동 구간을 선택하거나 파동 전체를 누르세요."
        : "현재 선택한 조건을 모두 만족하는 종목이 없습니다. 다른 조건을 해제하거나 필터 초기화를 누르세요."
      : "아직 후보 분석 자료가 없습니다. 후보 재계산을 실행하세요.",
  );
  const groups = {};
  for (const r of snapshot.rows) {
    const g =
      groups[r.sector] ||
      (groups[r.sector] = {
        sector: r.industry_name || r.sector,
        count: 0,
        known: 0,
        pass: 0,
        entry: 0,
      });
    g.count++;
    if (r.sector_action !== null && r.sector_action !== undefined) g.known++;
    if (r.sector_action === true) g.pass++;
    if (r.signal === true) g.entry++;
  }
  table(
    "industry-table",
    [
      ["산업", (g) => g.sector],
      ["분석 후보", (g) => number(g.count)],
      [
        "후보별 산업 액션 통과",
        (g) => (g.known ? `${g.pass} / ${g.known}` : "미적용·자료 없음"),
      ],
      ["최종 진입 신호", (g) => number(g.entry)],
    ],
    Object.values(groups).sort((a, b) => b.pass - a.pass),
  );
}
function renderSetupDetail(row) {
  const section = $("detail-setup");
  section.hidden = snapshot.rules.entry_method !== "setup";
  if (section.hidden) return;
  const rules = snapshot.rules;
  const known = (value) => typeof value === "number" && Number.isFinite(value);
  const checks = [
    [
      "파동 단계",
      TrendFollowFilters.phaseLabels[row.setup_phase] || "자료 없음",
      "",
    ],
    [
      "준비 조건",
      row.setup_ready === true
        ? "충족"
        : row.setup_ready === false
          ? "미충족"
          : "미확인",
      row.setup_ready === true
        ? "pass"
        : row.setup_ready === false
          ? "fail"
          : "unknown",
    ],
    [
      "준비 판정 반납 · 오늘 이전 베이스",
      known(row.setup_retracement)
        ? `${percent(row.setup_retracement)} / 기준 ${percent(rules.setup_max_retracement)} 이하`
        : `미확인 / 기준 ${percent(rules.setup_max_retracement)} 이하`,
      known(row.setup_retracement)
        ? row.setup_retracement <= rules.setup_max_retracement
          ? "pass"
          : "fail"
        : "unknown",
    ],
    [
      "오늘 저가 포함 반납",
      known(row.setup_retracement_today)
        ? `${percent(row.setup_retracement_today)} / 기준 ${percent(rules.setup_max_retracement)} 이하`
        : `미확인 / 기준 ${percent(rules.setup_max_retracement)} 이하`,
      known(row.setup_retracement_today)
        ? row.setup_retracement_today <= rules.setup_max_retracement
          ? "pass"
          : "fail"
        : "unknown",
    ],
    [
      "베이스 관측",
      known(row.setup_base_bars) && row.setup_resistance != null
        ? `${number(row.setup_base_bars)}거래일 / 기준 4거래일 이상`
        : "미확인 / 기준 4거래일 이상",
      known(row.setup_base_bars) && row.setup_resistance != null
        ? row.setup_base_bars >= 4
          ? "pass"
          : "fail"
        : "unknown",
    ],
    [
      "거래량 수축비",
      known(row.setup_volume_ratio)
        ? `${number(row.setup_volume_ratio, 2)} / 기준 ${number(rules.setup_volume_contraction, 2)} 이하`
        : `미확인 / 기준 ${number(rules.setup_volume_contraction, 2)} 이하`,
      known(row.setup_volume_ratio)
        ? row.setup_volume_ratio <= rules.setup_volume_contraction
          ? "pass"
          : "fail"
        : "unknown",
    ],
    [
      "변동성 수축비",
      known(row.setup_range_ratio)
        ? `${number(row.setup_range_ratio, 2)} / 기준 ${number(rules.setup_range_contraction, 2)} 이하`
        : `미확인 / 기준 ${number(rules.setup_range_contraction, 2)} 이하`,
      known(row.setup_range_ratio)
        ? row.setup_range_ratio <= rules.setup_range_contraction
          ? "pass"
          : "fail"
        : "unknown",
    ],
    [
      "셋업 돌파",
      row.setup_resistance != null
        ? `${row.setup_trigger === true ? "발생" : "미발생"} · 종가 ${number(row.reference_price)}원 / 저항 ${number(row.setup_resistance)}원`
        : "미확인 / 저항 자료 없음",
      row.setup_resistance == null
        ? "unknown"
        : row.setup_trigger === true
          ? "pass"
          : "fail",
    ],
    [
      "오늘 저가 포함 무효화",
      row.setup_invalidated === true
        ? "무효화 발생"
        : row.setup_resistance != null
          ? "무효화 없음"
          : "미확인",
      row.setup_resistance == null
        ? "unknown"
        : row.setup_invalidated === true
          ? "fail"
          : "pass",
    ],
    [
      "셋업 진입 조건",
      row.setup_entry === true
        ? "충족"
        : row.setup_entry === false && row.setup_resistance != null
          ? "미충족"
          : "미확인",
      row.setup_resistance == null
        ? "unknown"
        : row.setup_entry === true
          ? "pass"
          : "fail",
    ],
    [
      "최종 진입 신호",
      row.signal === true ? "통과" : row.signal === false ? "미통과" : "미확인",
      row.signal === true ? "pass" : row.signal === false ? "fail" : "unknown",
    ],
  ];
  $("detail-setup-list").replaceChildren(
    ...checks.map(([label, value, state]) => {
      const item = el("li", "", `setup-check ${state}`);
      item.append(el("span", label), el("strong", value));
      return item;
    }),
  );
}
async function detail(row) {
  $("stock-detail").hidden = false;
  $("detail-name").textContent = `${row.name || row.symbol} · ${row.symbol}`;
  $("detail-chart").textContent = "가격 이력을 읽고 있습니다.";
  facts("detail-facts", [
    ["주봉 추세", row.weekly_trend ? "통과" : "미통과·자료 부족"],
    [
      "RS 순위 / Mansfield",
      number(row.rs_percentile, 1) + " / " + number(row.mansfield_rs, 2),
    ],
    ["산업 동료 수", number(row.sector_peer_count)],
    ["자기 제외 동료 상승 비율", percent(row.sector_up_fraction)],
    ["동료 초과수익", percent(row.sector_excess_return)],
    ["준비 판정 반납 · 오늘 이전 베이스", percent(row.setup_retracement)],
    ["오늘 저가 포함 반납", percent(row.setup_retracement_today)],
    ["거래량 수축비", number(row.setup_volume_ratio, 2)],
    ["변동성 수축비", number(row.setup_range_ratio, 2)],
    ["셋업 저항", number(row.setup_resistance)],
    ["직전 252거래일 최고가", number(row.prior_52w_high)],
    [
      "52주 신고가 돌파",
      row.prior_52w_high == null
        ? "자료 부족"
        : row.high52_breakout
          ? "돌파"
          : "미돌파",
    ],
    ["최종 신호 경로", row.signal_entry_path || "해당 없음"],
    ["손절 폭", percent(row.stop_fraction)],
    ["배분 사유", row.allocation_reason || "수량 계획 미계산"],
    ["산업 자료 범위", row.sector_action_scope || "미적용"],
  ]);
  renderSetupDetail(row);
  try {
    const p = await api(
      `/api/prices?workspace=${workspace}&symbol=${row.symbol}`,
    );
    $("detail-chart").replaceChildren();
    if (!p.length) {
      $("detail-chart").textContent = "표시 가능한 가격 이력이 없습니다.";
      return;
    }
    const ns = "http://www.w3.org/2000/svg",
      svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 900 210");
    const values = p.map((x) => x.close),
      min = Math.min(...values) * 0.98,
      max = Math.max(...values) * 1.02;
    const xy = (v, i) =>
      `${50 + (i * 820) / Math.max(1, p.length - 1)},${170 - ((v - min) * 145) / (max - min || 1)}`;
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", p.map((x, i) => xy(x.close, i)).join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#14796c");
    line.setAttribute("stroke-width", "2");
    svg.append(line);
    for (const [x, y, text] of [
      [5, 30, number(max) + "원"],
      [5, 175, number(min) + "원"],
      [50, 202, p[0].date],
      [765, 202, p.at(-1).date],
    ]) {
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", x);
      t.setAttribute("y", y);
      t.setAttribute("font-size", "10");
      t.setAttribute("fill", "#657079");
      t.textContent = text;
      svg.append(t);
    }
    $("detail-chart").append(
      svg,
      el(
        "p",
        "최근 최대 180개 완료 일봉 종가 · 저장된 연구용 수정 가격 · 원 단위",
        "muted",
      ),
    );
  } catch (e) {
    $("detail-chart").textContent = e.message;
  }
}
$("detail-close").onclick = () => ($("stock-detail").hidden = true);
document.querySelectorAll(".candidate-filters select").forEach((input) => {
  input.onchange = () => {
    if (
      input.id === "filter-setup-phase" &&
      $("filter-setup").value === "trigger"
    )
      $("filter-setup").value = "all";
    renderStocks();
  };
});
document
  .querySelectorAll(
    "#phase-example [data-phase-choice], #phase-example [data-condition-choice]",
  )
  .forEach((choice) => {
    const selectFromFigure = () => {
      const next = TrendFollowFilters.figureSelection(
        $("filter-setup-phase").value,
        $("filter-setup").value,
        choice.dataset.phaseChoice ? "phase" : "condition",
        choice.dataset.phaseChoice || choice.dataset.conditionChoice,
      );
      $("filter-setup-phase").value = next.phase;
      $("filter-setup").value = next.condition;
      renderStocks();
    };
    choice.onclick = selectFromFigure;
    choice.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectFromFigure();
      }
    };
  });
$("search").oninput = renderStocks;
$("reset-filters").onclick = () => {
  document
    .querySelectorAll(".candidate-filters select")
    .forEach((input) => (input.value = "all"));
  $("search").value = "";
  renderStocks();
};
