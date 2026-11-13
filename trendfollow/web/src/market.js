function candidateFilters() {
  return {
    industry: $("filter-industry").value,
    rs: $("filter-rs").value,
    action: $("filter-action").value,
    setup: $("filter-setup").value,
    signal: $("filter-signal").value,
    volume: $("filter-volume").value,
    search: $("search").value,
  };
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
  const filters = candidateFilters();
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
                  r.setup_trigger === true ? "돌파 트리거" : "",
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
      ? "현재 필터에 맞는 종목이 없습니다."
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
    ["상승 파동 반납", percent(row.setup_retracement)],
    ["거래량 수축비", number(row.setup_volume_ratio, 2)],
    ["변동성 수축비", number(row.setup_range_ratio, 2)],
    ["셋업 저항", number(row.setup_resistance)],
    ["손절 폭", percent(row.stop_fraction)],
    ["배분 사유", row.allocation_reason || "수량 계획 미계산"],
    ["산업 자료 범위", row.sector_action_scope || "미적용"],
  ]);
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
document
  .querySelectorAll(".candidate-filters select")
  .forEach((input) => (input.onchange = renderStocks));
$("search").oninput = renderStocks;
$("reset-filters").onclick = () => {
  document
    .querySelectorAll(".candidate-filters select")
    .forEach((input) => (input.value = "all"));
  $("search").value = "";
  renderStocks();
};
