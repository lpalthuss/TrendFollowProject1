function marketRows() {
  const rows = candidateView ? candidateView.rows : snapshot.rows;
  if (
    !candidateSelection ||
    candidateSelection.summary.as_of !== candidateView?.as_of
  )
    return rows;
  const analyzed = new Map(rows.map((row) => [row.symbol, row]));
  return candidateSelection.members.map(
    (member) =>
      analyzed.get(member.symbol) || {
        ...member,
        candidate_unanalysed: true,
        date: candidateView.as_of,
      },
  );
}

function approxTurnover(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "금액 확인 불가";
  if (amount >= 100_000_000) return `${number(amount / 100_000_000)}억원가량`;
  if (amount >= 10_000) return `${number(amount / 10_000)}만원가량`;
  return `${number(amount)}원가량`;
}

function candidateStage(row, rules) {
  const close =
    row.close == null && row.reference_price == null
      ? NaN
      : Number(row.close ?? row.reference_price);
  const high = row.high == null ? NaN : Number(row.high);
  const levels = [row.setup_resistance];
  if (rules.setup_52w_enabled || rules.entry_method === "breakout")
    levels.push(row.prior_52w_high);
  const resistance = levels
    .filter((value) => Number.isFinite(Number(value)) && Number(value) > 0)
    .map(Number)
    .sort((a, b) => a - b)[0];
  if (
    Number.isFinite(resistance) &&
    Number.isFinite(high) &&
    high >= resistance &&
    close <= resistance
  )
    return "faded";
  if (
    row.setup_trigger === true ||
    ((rules.setup_52w_enabled ||
      (rules.entry_method === "breakout" && rules.breakout_days === 252)) &&
      row.high52_breakout === true)
  )
    return "breakout";
  if (
    Number.isFinite(resistance) &&
    Number.isFinite(close) &&
    close < resistance &&
    Number.isFinite(Number(row.atr)) &&
    resistance - close <= Number(row.atr)
  )
    return "near";
  if (row.setup_ready === true) return "ready";
  return "other";
}

function liveCandidateStage(row, run) {
  if (!run?.targets?.includes(row.symbol)) return null;
  const quote = run.summary?.latest_target_quotes?.[row.symbol];
  const availability = quoteAvailability(run, row.symbol, quote);
  if (availability !== "수신 정상")
    return { stage: "unknown", reason: availability };
  const levels = run.levels?.[row.symbol] || {};
  const resistance = [
    levels.setup_resistance,
    levels.high52_resistance,
    levels.resistance,
  ]
    .filter((value) => Number.isFinite(Number(value)) && Number(value) > 0)
    .map(Number)
    .sort((a, b) => a - b)[0];
  if (!Number.isFinite(resistance))
    return { stage: "unknown", reason: "저항선 자료 없음" };
  if (quote.price > resistance)
    return { stage: "breakout", reason: "장중 잠정 돌파" };
  if (run.summary?.touched_targets?.includes(row.symbol))
    return { stage: "faded", reason: "관측 중 접촉 후 후퇴" };
  if (resistance - quote.price <= Number(row.atr))
    return { stage: "near", reason: "장중 저항선 접근" };
  return {
    stage: candidateStage(row, snapshot.rules),
    reason: "전일 준비 기준",
  };
}

function candidateLiveRun() {
  return watchData?.runs?.find(
    (item) =>
      item.as_of === candidateView?.as_of &&
      item.session === watchData.today &&
      !["completed", "closed", "failed"].includes(item.summary?.status),
  );
}

function high52Gap(row, run = candidateLiveRun()) {
  const quote = run?.targets?.includes(row.symbol)
    ? run.summary?.latest_target_quotes?.[row.symbol]
    : null;
  const liveResistance = run?.levels?.[row.symbol]?.high52_resistance;
  const live = quote && quoteAvailability(run, row.symbol, quote) === "수신 정상"
    && liveResistance != null && Number.isFinite(Number(liveResistance))
    && Number(liveResistance) > 0;
  const resistance = Number(live ? liveResistance : row.prior_52w_high);
  if ((!live && row.prior_52w_high == null) || !Number.isFinite(resistance) || resistance <= 0)
    return null;
  const source = live ? "현재가 · 잠정" : "종가";
  const rawPrice = live ? quote.price : row.close;
  const price = Number(rawPrice);
  if (rawPrice == null || !Number.isFinite(price) || price <= 0) return null;
  return { percent: (price / resistance - 1) * 100, source };
}

function high52GapText(gap) {
  const magnitude = Math.abs(gap.percent);
  const distance = magnitude > 0 && magnitude < 0.05
    ? "0.1% 미만" : `${number(magnitude, 1)}%`;
  return `${distance} ${gap.percent > 0 ? "돌파" : "남음"}`;
}

function renderCandidateBoard() {
  const rows = marketRows();
  const run = candidateLiveRun();
  const groups = {
    ready: [],
    near: [],
    breakout: [],
    faded: [],
    unknown: [],
    other: [],
  };
  for (const row of rows) {
    const live = run ? liveCandidateStage(row, run) : null;
    const stage = row.candidate_unanalysed
      ? "unknown"
      : live?.stage || candidateStage(row, snapshot.rules);
    groups[stage].push({ ...row, candidate_reason: live?.reason });
  }
  const descriptions = [
    [
      "ready",
      "준비 후보",
      "종가 기준 셋업 준비 조건을 충족했습니다. 최종 진입 신호는 별도입니다.",
    ],
    ["near", "돌파선 접근", "종가가 저항선 아래이고 거리가 1 ATR 이내입니다."],
    [
      "breakout",
      run ? "돌파 중 · 장중 잠정" : "돌파 확인 · 종가",
      run
        ? "최근 KIS 체결가가 저항선 위입니다. 확정 종가가 아닙니다."
        : "저장된 종가 자료에서 셋업 또는 52주 돌파가 관측됐습니다.",
    ],
    [
      "faded",
      run ? "접촉 후 후퇴 · 장중 잠정" : "고가 접촉·종가 하회",
      run
        ? "관측 중 저항선 위 체결 후 최근 가격이 아래로 돌아왔습니다."
        : "당일 고가가 저항선에 닿았지만 종가는 아래에서 끝났습니다.",
    ],
  ];
  if (groups.unknown.length)
    descriptions.push([
      "unknown",
      run ? "시세·분석 미확인" : "가격·분석 자료 없음",
      "새 편입 종목의 일봉 준비 상태와 장중 연결·구독·체결 상태를 구분해 확인하세요.",
    ]);
  $("candidate-stage-board").replaceChildren(
    ...descriptions.map(([key, title, note]) => {
      const card = el("section", undefined, "candidate-stage-card");
      const heading = el("div", undefined, "candidate-stage-heading");
      heading.append(el("strong", title), el("b", number(groups[key].length)));
      card.append(heading, el("p", note, "muted"));
      const names = el("div", undefined, "candidate-stage-names");
      for (const row of groups[key].slice(0, 5)) {
        const button = el(
          "button",
          `${row.name || row.symbol}${row.candidate_reason ? ` · ${row.candidate_reason}` : ""}`,
          "quiet",
        );
        button.type = "button";
        button.onclick = () => detail(row);
        names.append(button);
      }
      if (!groups[key].length)
        names.append(el("span", "해당 종목 없음", "muted"));
      if (groups[key].length > 5)
        names.append(el("span", `외 ${groups[key].length - 5}종목`, "muted"));
      card.append(names);
      return card;
    }),
  );
  $("candidate-selection-note").textContent = candidateSelection
    ? `${candidateView?.as_of || snapshot.as_of} · ${candidateSelection.summary.source === "KRX Open API" ? "KRX Open API" : candidateSelection.summary.source === "pykrx" ? "pykrx" : "KIS"}로 ${number(candidateSelection.summary.count)}종목 선정 · 전회 대비 편입 ${number(candidateSelection.summary.added)} / 제외 ${number(candidateSelection.summary.removed)}. ${candidateView?.analysis?.mode === "market_candidate_research" ? "선택 계좌의 전략 조건 계산 완료 · 계좌 매매 기록에는 미반영." : "전략 조건 계산 전."} ${candidateSelection.summary.comparison_available && candidateSelection.summary.comparison_same_source === false ? "전회와 자료 원천이 달라 편입·제외 수치에 기준 차이가 있습니다." : ""} ${run ? "감시 종목만 KIS 장중 잠정 시세를 표시합니다." : "가격 판정은 저장된 종가 기준입니다."}`
    : `${candidateView?.as_of || snapshot.as_of || "선택한 날짜"}의 시장 후보군 선정 기록은 없습니다. 아래는 저장된 계좌 분석 결과이며 당시 시장 전체 목록의 편입·제외는 확인할 수 없습니다.${snapshot.workspace.source_workspace ? " 원계좌의 저장된 날짜를 보려면 위 원계좌 버튼을 누르세요." : ""}`;
  const criteria = $("candidate-criteria-note");
  const selectionSummary = candidateSelection?.summary;
  const threshold = Number(selectionSummary?.min_daily_turnover ?? selectionSummary?.min_recent_turnover_proxy);
  criteria.hidden = !selectionSummary || !Number.isFinite(threshold) || threshold <= 0;
  if (!criteria.hidden) {
    const basis = selectionSummary.source === "KIS market master"
      ? "최근 거래대금 추정액"
      : "당일 거래대금";
    const amount = threshold >= 100_000_000
      ? `${number(threshold / 100_000_000, 1)}억원`
      : `${number(threshold / 10_000, 1)}만원`;
    criteria.textContent = `1단계 선정 기준 · ${basis} ${amount} 이상 · 보통주 여부와 거래 상태 등도 확인`;
  }
  $("candidate-members").hidden = !candidateSelection;
  if (candidateSelection) {
    $("candidate-members-title").textContent =
      `선정된 시장 종목 ${number(candidateSelection.summary.count)}개 보기`;
    const observed = candidateSelection.summary.industry_classification_as_of;
    $("candidate-members-note").textContent = observed
      ? `산업 분류 ${number(candidateSelection.summary.industry_classified)} / ${number(candidateSelection.summary.count)}종목 · ${observed}에 저장된 산업 구성 사용${observed < candidateSelection.summary.as_of ? " · 기준일 사이의 산업 변경은 확인할 수 없습니다." : ""}`
      : "이 날짜 이전에 저장된 산업 구성이 없어 산업을 표시할 수 없습니다.";
    table(
      "candidate-members-table",
      [
        ["종목", (r) => `${r.name} (${r.symbol})`],
        ["시장", (r) => r.market],
        [
          "산업",
          (r) =>
            r.industry_name || (r.sector?.startsWith("UNCLASSIFIED:") ? "산업 확인 불가" : r.sector || "산업 확인 불가"),
        ],
        [candidateSelection.summary.source === "KIS market master" ? "최근 거래대금 추정" : "당일 거래대금", (r) => approxTurnover(r.recent_turnover_proxy)],
      ],
      candidateSelection.members,
    );
  }
  const changes = $("candidate-changes");
  changes.hidden = !candidateSelection?.summary.comparison_available;
  if (!changes.hidden) {
    $("candidate-changes-title").textContent =
      `${candidateSelection.summary.previous_as_of} 대비 ${candidateSelection.summary.as_of} 편입 ${candidateSelection.summary.added} · 제외 ${candidateSelection.summary.removed}`;
    $("candidate-changes-note").textContent =
      "편입·제외는 직전 저장일과 비교한 결과입니다. 근거에는 선택한 날짜의 자료만 표시합니다.";
    table(
      "candidate-added",
      [
        ["새로 편입", (r) => `${r.name} (${r.symbol})`],
        ["편입 근거", (r) => r.reason_display || r.reason],
      ],
      candidateSelection.added,
    );
    table(
      "candidate-removed",
      [
        ["제외", (r) => `${r.name} (${r.symbol})`],
        ["제외 근거", (r) => r.reason_display || r.reason],
      ],
      candidateSelection.removed,
    );
  }
}

function candidateCaptureEnabled() {
  if (!candidateDates) return false;
  if ($("candidate-source").value !== "kis") {
    if (!candidateDates.candidate_sources?.includes($("candidate-source").value))
      return false;
    const from = $("candidate-krx-start").value;
    return Boolean(from && from <= candidateDates.capture_as_of);
  }
  return Boolean(candidateDates.capture_available);
}

function renderCandidateCaptureControls() {
  if (!candidateDates) return;
  const source = $("candidate-source").value;
  const historical = source !== "kis";
  const label = source === "pykrx" ? "pykrx" : "KRX Open API";
  const supported = candidateDates.candidate_sources?.includes(source);
  $("candidate-krx-range").hidden = !historical;
  const canCapture = candidateCaptureEnabled();
  $("candidate-refresh").disabled = !canCapture;
  $("candidate-refresh").textContent = historical
    ? !supported
      ? `${label} 기능 사용 전 UI 서버 재시작 필요`
      : canCapture
        ? `${$("candidate-krx-start").value}부터 ${label} 누락 후보군 채우기`
        : "누락 후보군 시작일 확인 필요"
    : canCapture
      ? `${candidateDates.capture_as_of} KIS 후보군 저장`
      : `${candidateDates.capture_as_of} KIS 후보군 저장 불가`;
  $("candidate-refresh").title = historical
    ? source === "pykrx"
      ? "pykrx의 날짜별 종목 목록·거래정보로 누락 날짜를 순서대로 채웁니다. KRX_ID와 KRX_PW가 필요합니다."
      : "KRX Open API의 날짜별 기본정보·매매정보로 누락 날짜를 순서대로 채웁니다. KIS 선정 기준과 다릅니다."
    : canCapture
      ? "오늘 장 마감 후 KIS 종목 마스터로 시장 전체 후보군을 저장합니다."
      : "당시 KIS 종목 마스터가 없어 과거 날짜로 저장할 수 없습니다.";
  $("candidate-capture-note").textContent = historical
    ? !supported
      ? "화면은 새 버전이지만 실행 중인 UI 서버가 이전 버전입니다. UI 서버를 종료하고 다시 실행한 뒤 페이지를 새로고침하세요."
      : canCapture
        ? `${$("candidate-krx-start").value}부터 ${candidateDates.capture_as_of}까지 누락된 거래일을 날짜순으로 채웁니다. ${source === "pykrx" ? "pykrx는 날짜별 종목 목록·당일 거래대금 기준이며, 종목명은 조회 시점의 이름일 수 있습니다." : "KRX Open API는 보통주·당일 거래대금 기준입니다."} KIS 마스터 선정 결과와 다를 수 있습니다. 이미 저장된 날짜는 바꾸지 않습니다.`
        : "누락 시작일을 완료된 거래일로 지정하세요. 한 번에 최대 120거래일입니다."
    : canCapture
      ? "KIS 현재 마스터는 오늘 장 마감 날짜로만 저장합니다. 위의 후보 기준일 선택과 별개입니다."
      : "KIS 현재 마스터로 과거 날짜의 후보군을 저장할 수 없습니다. 과거 누락 날짜는 KRX Open API 또는 pykrx를 선택해 채울 수 있습니다.";
}

async function loadCandidateDate(date) {
  const selected = workspace;
  const ticket = ++candidateLoadSequence;
  const known = candidateDates?.signal_dates.includes(date);
  const view = known
    ? await api(
        `/api/candidate/day?workspace=${encodeURIComponent(workspace)}&as_of=${date}`,
      )
    : { as_of: date, rows: [] };
  const selection = candidateDates?.selection_dates.includes(date)
    ? await api(`/api/candidate/selection?as_of=${date}`)
    : null;
  if (selected !== workspace || ticket !== candidateLoadSequence) return;
  candidateView = view;
  candidateSelection = selection;
  updateIndustryFilter();
  renderStocks();
  renderCandidateBoard();
  renderMarketActions();
}

function candidateDateLabel(dates, date) {
  const hasSelection = dates.selection_dates.includes(date);
  const hasMarketAnalysis = dates.market_analysis_dates?.includes(date);
  const hasAccountAnalysis = dates.account_analysis_dates?.includes(date);
  if (hasMarketAnalysis && hasAccountAnalysis) return "시장·계좌 조건 계산 기록 있음";
  if (hasMarketAnalysis) return "시장 후보 조건 계산 기록 있음";
  if (hasSelection && hasAccountAnalysis) return "시장 종목 선정·계좌 계산 기록 있음";
  if (hasSelection) return "시장 종목 선정 기록 있음";
  if (hasAccountAnalysis) return "계좌 조건 계산 기록 있음";
  return "후보·계좌 기록 없음";
}

async function loadCandidateDates() {
  const selected = workspace;
  const datesResult = await api(
    `/api/candidate/dates?workspace=${encodeURIComponent(selected)}`,
  );
  if (selected !== workspace) return;
  candidateDates = datesResult;
  if (!$("candidate-krx-start").value)
    $("candidate-krx-start").value = candidateDates.reference_dates.at(-1);
  renderCandidateCaptureControls();
  const source = snapshot.workspace.source_workspace;
  $("candidate-source-account").hidden = !source;
  if (source)
    $("candidate-source-account").textContent =
      `원계좌로 이동해 계속 처리 · ${boot.workspaces.find((w) => w.id === source)?.name || "원계좌"}`;
  const select = $("candidate-date");
  const prior = select.value;
  const dates = [
    ...new Set([
      ...candidateDates.signal_dates,
      ...candidateDates.selection_dates,
      ...candidateDates.reference_dates,
    ]),
  ]
    .sort()
    .reverse();
  select.replaceChildren(
    ...dates.map((date) => {
      const label = candidateDateLabel(candidateDates, date);
      const option = el("option", `${date} · ${label}`);
      option.value = date;
      return option;
    }),
  );
  if (!dates.length) {
    candidateView = null;
    candidateSelection = null;
    renderCandidateBoard();
    renderMarketActions();
    return;
  }
  select.value = dates.includes(prior)
    ? prior
    : dates.includes(candidateDates.capture_as_of)
      ? candidateDates.capture_as_of
      : dates[0];
  await loadCandidateDate(select.value);
}

$("candidate-date").onchange = () =>
  loadCandidateDate($("candidate-date").value).catch((e) =>
    toast(e.message, true),
  );
$("candidate-source").onchange = () => renderCandidateCaptureControls();
$("candidate-krx-start").onchange = () => renderCandidateCaptureControls();
$("candidate-source-account").onclick = () => {
  const source = snapshot.workspace.source_workspace;
  if (!source) return;
  $("workspace").value = source;
  $("workspace").dispatchEvent(new Event("change"));
};
