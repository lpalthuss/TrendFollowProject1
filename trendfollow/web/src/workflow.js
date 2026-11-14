function flow(step) {
  flowStep = step;
  nav("workflow");
  window.scrollTo({ top: 0, behavior: "instant" });
  document
    .querySelectorAll(".flow-panel")
    .forEach((n) => (n.hidden = n.id !== "flow-" + step));
  document.querySelectorAll("#flow-steps [data-step]").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.step) === step);
    if (Number(b.dataset.step) === step) b.setAttribute("aria-current", "step");
    else b.removeAttribute("aria-current");
  });
  renderFlow();
}
function renderMarketActions() {
  if (!snapshot) return;
  const date = $("candidate-date").value;
  const accountDate = snapshot.account_as_of;
  const frozen = snapshot.workspace.mode === "recalculation";
  const analysisBlocked = !!accountDate && date !== accountDate;
  const busy =
    loading ||
    !!submittingAction ||
    currentJobs.some((j) => ["queued", "running"].includes(j.status));
  const savedMarket = candidateDates?.selection_dates.includes(date);
  $("candidate-analysis").textContent = savedMarket
    ? `${date}까지 저장된 시장 후보 계산`
    : "먼저 시장 후보군을 저장하세요";
  for (const button of document.querySelectorAll(
    '#market [data-action="market_analyze"], #market .action-strip [data-action="analyze"], #market .action-strip [data-action="paper"], #market .action-strip [data-action="daily"]',
  )) {
    const action = button.dataset.action;
    const blocked =
      !date ||
      (action === "market_analyze" && !savedMarket) ||
      (action === "analyze" && analysisBlocked) ||
      (!["analyze", "market_analyze"].includes(action) &&
        (frozen || (!!accountDate && date < accountDate)));
    button.disabled = busy || blocked;
    button.title = blocked
      ? action === "market_analyze"
        ? `${date || "선택 날짜"}의 시장 후보군을 먼저 저장하세요.`
        : action === "analyze" && analysisBlocked
        ? frozen
          ? `정정 비교 계좌는 ${accountDate}에 고정돼 있습니다. 원계좌에서 선택한 날짜를 확인하세요.`
          : `계좌 처리일은 ${accountDate}입니다. ${date} 후보 분석은 해당 날짜의 계좌 처리 후 가능합니다.`
        : frozen
          ? `정정 계좌는 ${accountDate}까지만 저장돼 있습니다. 원계좌에서 계속하세요.`
          : `계좌 처리일 ${accountDate || "미시작"}과 후보 기준일 ${date || "미선택"}을 확인하세요.`
      : "";
  }
  const analysisNote = $("candidate-analysis-note");
  analysisNote.hidden = !analysisBlocked && !savedMarket;
  if (savedMarket)
    analysisNote.textContent = `${date} 시장 후보군이 저장돼 있습니다. 위 '저장된 시장 후보 계산'은 해당 날짜까지 계산되지 않은 저장 날짜를 순서대로 분석합니다. 새 후보의 과거 일봉을 먼저 보충하며 계좌의 매매 기록은 바꾸지 않습니다.${analysisBlocked ? ` 아래 '계좌 후보 재계산'은 계좌 처리일 ${accountDate}에만 사용할 수 있습니다.` : ""}`;
  else if (analysisBlocked)
    analysisNote.textContent = frozen
      ? `현재 계좌는 ${accountDate}에 고정된 정정 비교 계좌입니다. ${date} 계좌 후보 재계산은 원계좌에서 확인하세요.`
      : `현재 계좌 처리일은 ${accountDate}입니다. ${date} 계좌 후보 재계산은 해당 날짜까지 일별 처리한 뒤 가능합니다.`;
  $("market-action-note").textContent = frozen
    ? `정정 비교 계좌 · 저장 종료일 ${accountDate}. 후보 재계산은 ${accountDate}에만 가능하며, 이후 날짜는 원계좌에서 확인하세요.`
    : accountDate && date !== accountDate
      ? `선택한 후보 기준일 ${date} · 계좌 처리일 ${accountDate}. 후보 재계산은 계좌 처리일에만 가능합니다.`
      : `선택한 후보 기준일 ${date || "없음"}에 실행합니다. 후보 재계산은 계좌 기록을 바꾸지 않습니다.`;
}
function renderFlow() {
  renderRecoveryControls();
  renderWatchControls();
  renderMarketActions();
  if (!snapshot || !guidance || snapshot.workspace.id !== workspace) {
    if (flowStep !== 4)
      showNextAction("자료 확인 중", "계좌와 작업 상태를 불러오고 있습니다.");
    return;
  }
  const s = snapshot,
    r = s.rules,
    g = guidance;
  const busy =
    loading ||
    currentJobs.some((j) => ["queued", "running"].includes(j.status));
  $("flow-date").textContent = "한국시간 " + g.today + " · 기준일 " + g.as_of;
  facts("flow-account", [
    ["선택 계좌", s.workspace.name],
    [
      "상대강도",
      r.rs_method === "mansfield" ? "Mansfield RS" : "RS 수익률 순위",
    ],
    ["진입 방식", r.entry_method === "setup" ? "파동 셋업" : "고가 돌파"],
    [
      "초기 손절",
      r.initial_stop_method === "hybrid"
        ? "자동 전환 C"
        : r.initial_stop_method === "fixed"
          ? "고정 손절"
          : "ATR 손절",
    ],
    [
      "추가매수",
      r.pyramiding_enabled
        ? "최대 " + r.pyramiding_max_additions + "회"
        : "OFF",
    ],
    ["계좌 처리일", s.account_as_of || "미시작"],
  ]);
  $("flow-industry-status").textContent = !g.requires_industry
    ? "이 계좌는 산업 스냅샷 준비 단계가 필요하지 않습니다."
    : g.industry_ready
      ? g.as_of +
        " 자료 준비됨 · 다시 갱신하지 않고 다음 작업을 진행할 수 있습니다."
      : "선택 날짜 산업 자료가 없습니다. 아래 종가 처리 버튼을 누르면 오늘 자료를 수집하거나 과거 자료를 이전 스냅샷으로 재구성합니다.";
  $("flow-industry").disabled =
    busy || !g.requires_industry || g.as_of !== g.today || !!g.blocked;
  $("flow-industry").textContent = g.industry_ready
    ? "산업 자료 다시 갱신"
    : "산업 자료 갱신";
  const mismatch = s.status === "configuration_mismatch";
  $("flow-daily-status").textContent = g.processed
    ? "이 날짜는 이미 처리됐습니다. 재실행 없이 결과 확인으로 넘어가세요."
    : "누락된 거래일의 가격과 산업 자료를 준비한 뒤 날짜순으로 로컬 모의계좌를 처리합니다. 과거 산업 자료를 재구성한 날짜는 결과에 표시합니다.";
  $("flow-daily").disabled =
    busy ||
    !!g.blocked ||
    mismatch ||
    g.processed ||
    s.workspace.mode === "recalculation";
  const blocker = mismatch
    ? "계좌 설정이 기존 기록과 다릅니다. 기존 계좌를 보존하고 1단계에서 새 비교 계좌를 만드세요."
    : g.blocked;
  $("flow-blocker").hidden = !blocker;
  $("flow-blocker").textContent = blocker || "";
  $("flow-blocker").className = "notice notice-error";
  $("flow-to-results").disabled = busy || !g.processed || mismatch;
  $("flow-to-results").setAttribute("aria-busy", String(busy));
  $("flow-job-status").replaceChildren();
  const jobs = currentJobs
    .filter((j) => j.workspace === workspace && j.as_of === g.as_of)
    .slice(-4)
    .reverse();
  const oldDateFailures = jobs.filter(
    (j) =>
      j.action === "daily" &&
      j.status === "failed" &&
      /미처리 거래일이 여러 개입니다.*--as-of/.test(j.error || ""),
  );
  for (const j of jobs.filter((x) => !oldDateFailures.includes(x))) {
    const resolved =
      j.status === "failed" &&
      ["daily", "paper", "revision_continue"].includes(j.action) &&
      jobs.some(
        (x) =>
          x.status === "completed" &&
          ["daily", "paper", "revision_continue"].includes(x.action) &&
          x.result?.status === "processed" &&
          x.as_of === j.as_of &&
          Date.parse(x.finished_at) > Date.parse(j.finished_at),
      );
    const n = el(
      "div",
      undefined,
      "flow-job " + (resolved ? "resolved" : j.status),
    );
    n.append(
      el(
        "strong",
        (labels[j.action] || j.action) +
          " · " +
          (resolved
            ? "이전 실패 · 이후 처리 완료"
            : labels[j.status] || j.status),
      ),
    );
    if (j.error) {
      n.append(el("div", j.error));
      if (resolved)
        n.append(
          el(
            "div",
            "이후 정상 처리된 날짜입니다. 위 내용은 이전 시도의 기록이며 다시 실행할 필요는 없습니다.",
          ),
        );
    } else if (["queued", "running"].includes(j.status))
      n.append(
        el(
          "div",
          j.progress || "처리 중입니다. 중복 실행하지 말고 완료를 기다리세요.",
        ),
      );
    else if (j.result?.processed_dates)
      n.append(
        el(
          "div",
          `${j.result.processed_dates.length}개 거래일 처리 완료` +
            (j.result.reconstructed_industry_dates?.length
              ? ` · 산업 자료 ${j.result.reconstructed_industry_dates.length}일 재구성`
              : "") +
            (j.result.price_source === "pykrx"
              ? " · KRX(pykrx) 누락 일봉"
              : "") +
            (j.result.source_comparison?.difference_count
              ? ` · 기존 원천과 가격·거래량 차이 ${j.result.source_comparison.difference_count}건 (수집 기록 확인)`
              : ""),
        ),
      );
    else if (j.result?.status?.startsWith("skipped"))
      n.append(
        el(
          "div",
          "건너뜀: " + j.result.status + " · 처리 완료로 간주하지 않습니다.",
        ),
      );
    $("flow-job-status").append(n);
  }
  if (oldDateFailures.length) {
    const history = el("details", undefined, "flow-job resolved");
    history.append(
      el(
        "summary",
        `이전 서버의 날짜 제한 실패 ${oldDateFailures.length}건 · 새 버튼으로 재실행 가능`,
      ),
    );
    for (const j of oldDateFailures)
      history.append(
        el("div", `${j.finished_at || j.created_at} · ${j.error}`),
      );
    $("flow-job-status").append(history);
  }
  $("flow-result-status").textContent = g.processed
    ? g.as_of +
      " 종가 처리 완료. 신호가 0건이어도 정상일 수 있습니다. 아래 대기는 다음 거래일 계획이며 증권사 주문이 아닙니다."
    : "선택 날짜의 종가 처리가 아직 완료되지 않았습니다. 아래는 마지막 저장 결과입니다. 2단계에서 먼저 처리하세요.";
  $("flow-result-status").classList.toggle("pending", !g.processed);
  facts("flow-results", [
    ["저장된 결과 기준일", s.as_of || "없음"],
    [
      "종가 진입 신호",
      s.rows.length
        ? number(s.rows.filter((x) => x.signal === true).length)
        : "자료 없음",
    ],
    ["보유 / 다음 시가 대기", s.positions.length + " / " + s.pending.length],
    [
      "RS 통과",
      s.rows.length
        ? number(s.rows.filter((x) => x.rs_pass === true).length)
        : "자료 없음",
    ],
  ]);
  table(
    "flow-pending",
    [
      ["종목", (x) => x.name || x.symbol],
      [
        "유형",
        (x) => (x.order_intent === "add" ? "추가매수 계획" : "신규 진입 계획"),
      ],
      ["신호일", (x) => String(x.date).slice(0, 10)],
      ["손절 기준", (x) => number(x.planned_common_stop ?? x.stop_price)],
    ],
    s.pending,
    "현재 저장된 진입·추가매수 대기가 없습니다.",
  );
  if (flowStep === 1) {
    showNextAction(
      "다음: 종가 처리 준비",
      "선택 계좌와 설정을 확인했다면 2단계로 이동하세요.",
      "flow-to-data",
    );
  } else if (flowStep === 2) {
    const latest = jobs.find((j) =>
      ["industry", "daily", "paper"].includes(j.action),
    );
    if (busy) {
      const active = currentJobs.findLast((j) =>
        ["queued", "running"].includes(j.status),
      );
      showNextAction(
        active
          ? `${labels[active.action] || active.action} 진행 중`
          : "작업 접수 중",
        active?.progress ||
          "작업을 접수하고 있습니다. 위 진행 상태를 확인하세요.",
      );
    } else if (g.processed)
      showNextAction(
        "종가 처리 완료 · 다음: 결과 확인",
        `${g.as_of} 처리 결과와 다음 거래일 계획을 확인하세요.`,
        "flow-to-results",
      );
    else if (
      latest?.status === "failed" &&
      /미처리 거래일/.test(latest.error || "")
    )
      showNextAction(
        "다음: 누락 거래일 자동 처리",
        "이제 한 번의 실행으로 누락된 거래일을 날짜순으로 처리합니다.",
        "flow-daily",
      );
    else if (
      latest?.status === "failed" &&
      /이미 처리한 과거 일봉이 수정/.test(latest.error || "") &&
      recoveryInfo?.report &&
      !recoveryInfo.report.applied
    )
      showNextAction(
        "정정 내역 확인 필요",
        `${recoveryInfo.report.account_as_of}까지의 입력이 바뀌어 계좌가 보존됐습니다. 아래 변경 내역 ${recoveryInfo.report.row_changes}건을 검토한 뒤 선택 날짜까지 계속 처리하세요.`,
        "recovery-history-summary",
      );
    else if (latest?.status === "failed")
      showNextAction(
        "작업 실패 · 다시 확인 필요",
        latest.error || "실패 사유를 확인하고 필요한 자료를 다시 준비하세요.",
        !$("flow-daily").disabled ? "flow-daily" : null,
      );
    else if (!$("flow-daily").disabled)
      showNextAction(
        "다음: 누락 거래일 포함 종가 처리",
        "선택 날짜까지 누락된 거래일을 날짜순으로 처리하세요.",
        "flow-daily",
      );
    else
      showNextAction(
        "진행 조건 확인",
        blocker || "버튼 위의 안내를 확인하세요.",
      );
  } else if (flowStep === 3) {
    showNextAction(
      g.processed ? "다음: 장중 관측 준비" : "다음: 종가 처리",
      g.processed
        ? "결과와 다음 거래일 계획을 확인한 뒤 관측 설정으로 이동하세요."
        : "종가 처리 단계로 돌아가 자료 준비 상태를 확인하세요.",
      g.processed ? "flow-to-watch" : "flow-back-to-data",
    );
  }
}
document
  .querySelectorAll("[data-step]")
  .forEach((b) => (b.onclick = () => flow(Number(b.dataset.step))));
$("flow-to-data").onclick = () => flow(2);
$("flow-to-results").onclick = () => flow(3);
$("flow-open-market").onclick = () => nav("market");
$("flow-open-signals").onclick = () => nav("signals");
$("asof").onchange = async () => {
  try {
    const p = await api(
      "/api/pulse?workspace=" + workspace + "&as_of=" + $("asof").value,
    );
    guidance = readGuidance(p);
    receiveRecovery(p.recovery);
    if (!$("asof").value) $("asof").value = guidance.as_of;
    renderJobs(p.jobs);
  } catch (e) {
    toast(e.message, true);
  }
};
