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
  for (const row of marketRows())
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
function high52GapCell(row, gap = high52Gap(row)) {
  if (!gap) return el("span", "자료 부족", "pill warn");
  const cell = el("div", undefined, "high52-gap");
  cell.append(
    el(
      "strong",
      high52GapText(gap),
      gap.percent > 0 ? "high52-gap-over" : "high52-gap-under",
    ),
    el("small", gap.source, "muted"),
  );
  return cell;
}

function refreshHigh52Gaps() {
  const root = $("stock-table");
  const body = root.querySelector("tbody");
  if (!body) return;
  if (
    root.dataset.sortColumn === "4" &&
    root.dataset.sortDirection !== "none"
  ) {
    renderStocks();
    return;
  }
  const rows = new Map(marketRows().map((row) => [row.symbol, row]));
  for (const tr of body.rows) {
    const symbol = tr.cells[0].querySelector(".code")?.textContent;
    const row = rows.get(symbol);
    if (!row) continue;
    const cell = tr.cells[4];
    const gap = high52Gap(row);
    cell.replaceChildren(high52GapCell(row, gap));
    cell.dataset.sortValue = gap == null ? "" : String(gap.percent);
  }
}

function renderStocks() {
  if (!snapshot) return;
  const filters = normalizeCandidateFilters();
  const disabled = updateCandidateOptions(filters);
  renderSetupHelp();
  let rows = TrendFollowFilters.filterRows(
    marketRows(),
    filters,
    snapshot.rules,
  );
  const active = Object.entries(filters).filter(([key, value]) =>
    key === "search" ? value.trim() : value !== "all",
  ).length;
  $("filter-note").textContent =
    `선택한 조건 ${active}개를 모두 만족하는 ${rows.length}종목을 표시합니다 (AND). RS는 선택 계좌의 ${snapshot.rules.rs_method === "mansfield" ? "Mansfield RS" : "RS 수익률 순위"} 기준입니다. 52주 신고가의 장중 현재가 표시는 잠정값이며 필터는 저장된 종가 기준입니다. 표시 필터는 신호·계좌를 변경하지 않습니다.`;
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
  $("row-count").textContent = `${rows.length} / ${marketRows().length} 종목`;
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
      [
        "분석 상태",
        (r) =>
          r.candidate_unanalysed ? "일봉·후보 계산 전" : "저장된 종가 분석",
      ],
      [
        "종가 / 52주 고가",
        (r) =>
          `${number(r.close ?? r.reference_price)} / ${r.prior_52w_high == null ? "자료 부족" : number(r.prior_52w_high)}`,
        { sortable: false },
      ],
      [
        "52주 신고가",
        (r) => high52GapCell(r),
        { sortValue: (r) => high52Gap(r)?.percent },
      ],
      ["RS 순위", (r) => number(r.rs_percentile, 1)],
      ["주간 Mansfield", (r) => number(r.mansfield_rs, 2)],
      ["다기간 Mansfield", (r) => r.multi_rs == null ? "자료 없음" : number(r.multi_rs, 2)],
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
          const b = el(
            "button",
            r.candidate_unanalysed ? "분석 후 선택" : "선택",
            "quiet",
          );
          b.disabled = Boolean(r.candidate_unanalysed);
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
    marketRows().length
      ? filters.setup === "trigger" &&
        !marketRows().some((row) => row.setup_trigger === true)
        ? "이 기준일에 셋업 돌파가 발생한 종목이 없습니다. 다른 파동 구간을 선택하거나 파동 전체를 누르세요."
        : "현재 선택한 조건을 모두 만족하는 종목이 없습니다. 다른 조건을 해제하거나 필터 초기화를 누르세요."
      : "아직 후보 분석 자료가 없습니다. 후보 재계산을 실행하세요.",
  );
  const groups = {};
  for (const r of marketRows()) {
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
  const beforeBase = ["seeking", "rising"].includes(row.setup_phase);
  const unavailable = beforeBase ? "조정·베이스 형성 전" : "미확인";
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
        : beforeBase
          ? unavailable
          : row.setup_ready === false
            ? "미충족"
            : "미확인",
      row.setup_ready === true
        ? "pass"
        : beforeBase
          ? "unknown"
          : row.setup_ready === false
            ? "fail"
            : "unknown",
    ],
    [
      "준비 판정 반납 · 오늘 이전 베이스",
      known(row.setup_retracement)
        ? `${percent(row.setup_retracement)} / 기준 ${percent(rules.setup_max_retracement)} 이하`
        : `${unavailable} / 기준 ${percent(rules.setup_max_retracement)} 이하`,
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
        : `${unavailable} / 기준 ${percent(rules.setup_max_retracement)} 이하`,
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
        : `${unavailable} / 기준 4거래일 이상`,
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
        : `${unavailable} / 기준 ${number(rules.setup_volume_contraction, 2)} 이하`,
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
        : `${unavailable} / 기준 ${number(rules.setup_range_contraction, 2)} 이하`,
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
        : `${unavailable} / 저항 자료 없음`,
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
          : unavailable,
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
          : unavailable,
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
function renderDetailHelp(rules) {
  const groups = [
    [
      "추세·산업",
      [
        [
          "주봉 추세",
          `완료 주봉 종가가 ${rules.weekly_ma}주 평균보다 높고 평균도 상승 중인지 확인합니다. 최종 진입의 공통 조건입니다.`,
        ],
        [
          "RS 순위 / 주간 Mansfield",
          "RS 순위는 직전 126거래일 수익률을 해당 날짜의 분석 후보군 안에서 순위화한 백분위입니다. 기준 지수 대비 초과수익은 매수 조건에서 별도로 확인합니다. 주간 Mansfield는 52주 상대가격의 이동평균 대비 강도입니다. 계좌에서 선택한 방식만 최종 진입에 사용합니다.",
        ],
        [
          "다기간 Mansfield",
          "KOSPI 대비 일별 상대가격의 60·120·250거래일 Mansfield 값 중 계산 가능한 기간을 평균해 0~100으로 변환한 참고 점수입니다. 종목 간 백분위가 아닙니다. 기간과 계산 기준일을 함께 표시하며 계좌 매수·청산에는 사용하지 않습니다.",
        ],
        [
          "산업 동료 수",
          `같은 산업에서 자기 종목을 제외하고 당일 수익률을 확인한 종목 수입니다. 산업 액션의 계좌 기준은 ${number(rules.sector_min_peers)}종목 이상입니다.`,
        ],
        [
          "자기 제외 동료 상승 비율",
          `그 동료 중 당일 상승한 종목의 비율입니다. 산업 액션의 계좌 기준은 ${percent(rules.sector_min_breadth)} 이상입니다.`,
        ],
        [
          "동료 초과수익",
          "동료의 평균 수익률에서 각자의 기준 지수 수익률을 뺀 값입니다. 산업 액션은 이 값이 양수인지도 봅니다.",
        ],
        [
          "산업 자료 범위",
          "동료 집계에 사용한 산업 구성 기록의 종류입니다. 수익률이나 진입 조건의 점수가 아닙니다.",
        ],
      ],
    ],
  ];
  if (rules.entry_method === "setup")
    groups.push([
      "파동 셋업",
      [
        [
          "파동 단계",
          "상승 탐색→상승 진행→조정·베이스로 이어집니다. 돌파 당일에는 다음 상승 파동으로 전환되어도 그날의 베이스 값은 남습니다.",
        ],
        [
          "준비 판정 반납",
          "확정된 파동 상승 폭 중 오늘 이전 베이스에서 되돌린 비율입니다. 준비 조건의 반납 한도를 확인합니다.",
        ],
        [
          "오늘 저가 포함 반납",
          "당일 저가까지 포함한 되돌림 비율입니다. 한도를 넘으면 셋업 무효화로 판단합니다.",
        ],
        [
          "베이스 관측·준비 조건",
          `베이스를 4거래일 이상 관측하고 반납이 ${percent(rules.setup_max_retracement)} 이하이면 준비 충족입니다. 아직 진입 신호는 아닙니다.`,
        ],
        [
          "거래량 수축비",
          `베이스 후반 평균 거래량 ÷ 전반 평균 거래량입니다. 셋업 진입의 계좌 기준은 ${number(rules.setup_volume_contraction, 2)} 이하입니다.`,
        ],
        [
          "변동성 수축비",
          `일중 (고가−저가) ÷ 종가 비율의 베이스 후반 평균 ÷ 전반 평균입니다. 셋업 진입의 계좌 기준은 ${number(rules.setup_range_contraction, 2)} 이하입니다.`,
        ],
        [
          "셋업 저항·돌파",
          "선행 상승 파동의 고가가 저항선입니다. 전일 종가는 이하이고 당일 종가가 이를 넘으면 셋업 돌파입니다.",
        ],
        [
          "셋업 진입 조건",
          "준비·돌파·무효화 없음·거래량 및 변동성 수축을 함께 확인합니다. 최종 신호에는 공통 조건도 더 적용됩니다.",
        ],
      ],
    ]);
  groups.push([
    "돌파·계좌",
    [
      [
        "직전 252거래일 최고가 / 52주 신고가 돌파",
        "오늘을 제외한 직전 252거래일 최고 고가와 오늘 종가를 비교합니다. 52주 경로를 켠 계좌에서 별도 진입 후보로 사용합니다.",
      ],
      [
        "최종 신호 경로",
        "모든 조건을 통과했을 때 셋업 돌파·52주 신고가 등 어느 경로에서 신호가 났는지 표시합니다.",
      ],
      [
        "손절 폭",
        "기준 종가와 예상 손절가의 차이를 종가 대비 비율로 표시합니다. 모의 수량 배분과 위험 한도 확인에 사용합니다.",
      ],
      [
        "배분 사유",
        "모의계좌의 현금·보유·위험 한도로 수량을 계획한 결과입니다. 시장 후보 분석만으로는 계산하지 않습니다.",
      ],
      [
        "ATR",
        `일별 실제 변동폭을 Wilder 방식으로 평활화한 값입니다. 이 계좌는 ${rules.atr_days}거래일 기준이며 파동 판정과 설정에 따른 손절 폭에 쓰입니다.`,
      ],
    ],
  ]);
  $("detail-help-content").replaceChildren(
    ...groups.map(([heading, items]) => {
      const section = el("section", undefined, "detail-help-group");
      const list = el("dl");
      section.append(el("h3", heading));
      for (const [term, description] of items) {
        const item = el("div");
        item.append(el("dt", term), el("dd", description));
        list.append(item);
      }
      section.append(list);
      return section;
    }),
  );
}
let chartRows = [];
let chartRequest = 0;
let chartEnd = "";
function drawDetailChart() {
  const target = $("detail-chart");
  const months = Number($("chart-months").value);
  $("chart-months-value").textContent = `${months}개월`;
  if (!chartRows.length) return;
  const end = chartEnd;
  const first = new Date(`${end}T00:00:00Z`);
  const day = first.getUTCDate();
  first.setUTCDate(1);
  first.setUTCMonth(first.getUTCMonth() - months);
  first.setUTCDate(
    Math.min(
      day,
      new Date(
        Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0),
      ).getUTCDate(),
    ),
  );
  const start = first.toISOString().slice(0, 10);
  const p = chartRows.filter((bar) => bar.date >= start && bar.date <= end);
  target.replaceChildren();
  if (!p.length) {
    target.textContent = "선택한 기간에 표시할 일봉이 없습니다.";
    return;
  }
  const candle = $("chart-candle").getAttribute("aria-pressed") === "true";
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const width = 900;
  svg.setAttribute("viewBox", `0 0 ${width} 295`);
  svg.setAttribute("role", "img");
  svg.setAttribute("tabindex", "0");
  svg.setAttribute(
    "aria-label",
    `${months}개월 ${candle ? "캔들" : "종가 선"} 차트, ${p[0].date}부터 ${p.at(-1).date}까지`,
  );
  const min = Math.min(...p.map((bar) => Number(bar.low))) * 0.98;
  const max = Math.max(...p.map((bar) => Number(bar.high))) * 1.02;
  const y = (value) => 174 - ((value - min) * 150) / (max - min || 1);
  const x = (index) => 50 + ((width - 80) * (index + 0.5)) / p.length;
  const barWidth = Math.min(5, ((width - 80) / p.length) * 0.72);
  const maxVolume = Math.max(1, ...p.map((bar) => Number(bar.volume) || 0));
  const shape = (name, attributes, parent = svg) => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attributes))
      node.setAttribute(key, value);
    parent.append(node);
    return node;
  };
  if (candle) {
    p.forEach((bar, index) => {
      const center = x(index);
      const rise = Number(bar.close) >= Number(bar.open);
      const color = rise ? "#b33f38" : "#276eaa";
      const group = shape("g", {});
      shape(
        "line",
        {
          x1: center,
          x2: center,
          y1: y(bar.high),
          y2: y(bar.low),
          stroke: color,
          "stroke-width": 1.3,
        },
        group,
      );
      const top = Math.min(y(bar.open), y(bar.close));
      shape(
        "rect",
        {
          x: center - barWidth / 2,
          y: top,
          width: barWidth,
          height: Math.max(1, Math.abs(y(bar.open) - y(bar.close))),
          fill: color,
        },
        group,
      );
    });
  } else {
    shape("polyline", {
      points: p.map((bar, index) => `${x(index)},${y(bar.close)}`).join(" "),
      fill: "none",
      stroke: "#14796c",
      "stroke-width": 2,
    });
  }
  shape("line", {
    x1: 50,
    x2: width - 30,
    y1: 195,
    y2: 195,
    stroke: "#d8e0dc",
  });
  p.forEach((bar, index) => {
    const rise = Number(bar.close) >= Number(bar.open);
    const height = Math.max(1, ((Number(bar.volume) || 0) / maxVolume) * 48);
    shape("rect", {
      x: x(index) - barWidth / 2,
      y: 253 - height,
      width: barWidth,
      height,
      fill: rise ? "#b33f38" : "#276eaa",
      opacity: 0.65,
    });
  });
  const cursor = shape("line", {
    x1: 0,
    x2: 0,
    y1: 20,
    y2: 255,
    stroke: "#506b60",
    "stroke-width": 1,
    "stroke-dasharray": "3 3",
    visibility: "hidden",
    "pointer-events": "none",
  });
  let hovered = p.length - 1;
  const showBar = (index, showCursor = true) => {
    hovered = Math.max(0, Math.min(p.length - 1, index));
    const bar = p[hovered];
    const atrMovement =
      bar.atr_up_multiple == null || bar.atr_down_multiple == null
        ? "상·하 변동 계산 불가"
        : `상승 변동 ${number(bar.atr_up_multiple, 2)}배 · 하락 변동 ${number(bar.atr_down_multiple, 2)}배`;
    $("chart-hover").textContent =
      `${bar.date} · 시가 ${number(bar.open)}원 · 고가 ${number(bar.high)}원 · 저가 ${number(bar.low)}원 · 종가 ${number(bar.close)}원 · 거래량 ${number(bar.volume)}주 · ATR(${snapshot.rules.atr_days}) ${bar.atr == null ? "자료 부족" : `${number(bar.atr, 2)}원 (${atrMovement})`}`;
    cursor.setAttribute("x1", x(hovered));
    cursor.setAttribute("x2", x(hovered));
    cursor.setAttribute("visibility", showCursor ? "visible" : "hidden");
  };
  svg.onpointermove = (event) => {
    const rect = svg.getBoundingClientRect();
    const position = ((event.clientX - rect.left) * width) / rect.width;
    showBar(Math.floor(((position - 50) * p.length) / (width - 80)));
  };
  svg.onpointerleave = () => showBar(p.length - 1, false);
  svg.onkeydown = (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    showBar(hovered + (event.key === "ArrowRight" ? 1 : -1));
  };
  for (const [left, top, label] of [
    [5, 28, `${number(max)}원`],
    [5, 184, `${number(min)}원`],
    [5, 215, "거래량"],
    [5, 253, `${number(maxVolume)}주`],
    [50, 282, p[0].date],
    [width - 135, 282, p.at(-1).date],
  ]) {
    shape("text", {
      x: left,
      y: top,
      "font-size": 11,
      fill: "#657079",
    }).textContent = label;
  }
  for (const fraction of [0.25, 0.5, 0.75]) {
    const index = Math.round((p.length - 1) * fraction);
    shape("text", {
      x: x(index) - 34,
      y: 282,
      "font-size": 11,
      fill: "#657079",
    }).textContent = p[index].date;
  }
  target.append(svg);
  showBar(p.length - 1, false);
  target.append(
    el(
      "p",
      `선택 ${months}개월 전체 표시: ${p[0].date}~${p.at(-1).date} · ${p.length}거래일 · 상·하 변동 배수는 전일 종가에서 당일 고가·저가까지의 폭 ÷ ATR · 연구용 수정 가격 · 빨강 상승 / 파랑 하락 · 마우스나 방향키로 날짜 확인`,
      "muted",
    ),
  );
}
for (const type of ["candle", "line"]) {
  $(`chart-${type}`).onclick = () => {
    $("chart-candle").setAttribute("aria-pressed", String(type === "candle"));
    $("chart-line").setAttribute("aria-pressed", String(type === "line"));
    drawDetailChart();
  };
}
$("chart-months").oninput = drawDetailChart;
async function detail(row) {
  const request = ++chartRequest;
  chartRows = [];
  chartEnd = candidateView?.as_of || snapshot.as_of;
  const beforeBase =
    snapshot.rules.entry_method === "setup" &&
    ["seeking", "rising"].includes(row.setup_phase);
  const setupValue = (value, format) =>
    value == null && beforeBase ? "조정·베이스 형성 전" : format(value);
  const scopeLabels = {
    industry_snapshot: "저장된 산업 구성",
    candidate_universe: "후보군 산업 구성",
    missing_industry_snapshot: "산업 기록 없음",
    unclassified_industry: "산업 분류 없음",
  };
  $("stock-detail").hidden = false;
  $("detail-name").textContent = `${row.name || row.symbol} · ${row.symbol}`;
  $("chart-hover").textContent = "";
  $("detail-chart").textContent = "가격 이력을 읽고 있습니다.";
  facts("detail-facts", [
    ["주봉 추세", row.weekly_trend ? "통과" : "미통과·자료 부족"],
    [
      "RS 순위 / 주간 Mansfield",
      number(row.rs_percentile, 1) + " / " + number(row.mansfield_rs, 2),
    ],
    ["다기간 Mansfield", row.multi_rs == null ? "자료 없음" :
      `${number(row.multi_rs, 2)} (원점수 ${number(row.multi_rs_raw, 2)}) · ${row.multi_rs_windows || "기간 확인 불가"}일 · ${String(row.multi_rs_as_of || "날짜 확인 불가").slice(0, 10)}`],
    ["산업 동료 수", number(row.sector_peer_count)],
    ["자기 제외 동료 상승 비율", percent(row.sector_up_fraction)],
    [
      "동료 초과수익",
      row.sector_excess_return == null
        ? "산업 수익률 자료 없음"
        : percent(row.sector_excess_return),
    ],
    [
      "준비 판정 반납 · 오늘 이전 베이스",
      setupValue(row.setup_retracement, percent),
    ],
    ["오늘 저가 포함 반납", setupValue(row.setup_retracement_today, percent)],
    [
      "거래량 수축비",
      setupValue(row.setup_volume_ratio, (value) => number(value, 2)),
    ],
    [
      "변동성 수축비",
      setupValue(row.setup_range_ratio, (value) => number(value, 2)),
    ],
    ["셋업 저항", setupValue(row.setup_resistance, number)],
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
    [
      "배분 사유",
      row.allocation_reason ||
        (candidateView?.analysis?.mode === "market_candidate_research"
          ? "시장 후보 분석에서는 수량 미계산"
          : "수량 계획 미계산"),
    ],
    [
      "산업 자료 범위",
      scopeLabels[row.sector_action_scope] ||
        row.sector_action_scope ||
        "미적용",
    ],
  ]);
  renderDetailHelp(snapshot.rules);
  renderSetupDetail(row);
  try {
    const p = await api(
      `/api/prices?workspace=${workspace}&symbol=${row.symbol}&as_of=${candidateView?.as_of || snapshot.as_of}`,
    );
    if (request !== chartRequest) return;
    if (!p.length) {
      $("detail-chart").textContent = "표시 가능한 가격 이력이 없습니다.";
      return;
    }
    if (!("atr" in p[0]) || !("atr_up_multiple" in p[0])) {
      $("chart-hover").textContent = "";
      $("detail-chart").textContent =
        "UI 서버가 이전 가격 조회 코드를 사용 중입니다. UI 서버를 재시작한 뒤 화면을 새로고침하세요.";
      return;
    }
    chartRows = p;
    drawDetailChart();
  } catch (e) {
    if (request === chartRequest) $("detail-chart").textContent = e.message;
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
