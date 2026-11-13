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
function renderFlow() {
  renderRecoveryControls();
  renderWatchControls();
  if (!snapshot || !guidance) return;
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
      : "선택 날짜 산업 자료가 없습니다. 오늘 자료는 갱신할 수 있지만 과거 날짜의 구성을 새로 재현할 수는 없습니다.";
  $("flow-industry").disabled =
    busy || !g.requires_industry || g.as_of !== g.today || !!g.blocked;
  $("flow-industry").textContent = g.industry_ready
    ? "산업 자료 다시 갱신"
    : "산업 자료 갱신";
  const mismatch = s.status === "configuration_mismatch";
  $("flow-daily-status").textContent = g.processed
    ? "이 날짜는 이미 처리됐습니다. 재실행 없이 결과 확인으로 넘어가세요."
    : "가격을 수집하고 키움 시세와 대조한 뒤 로컬 모의계좌를 처리합니다. 소요 시간 동안 아래에 진행 상태를 표시합니다.";
  $("flow-daily").disabled =
    busy ||
    !!g.blocked ||
    !g.industry_ready ||
    mismatch ||
    g.processed ||
    s.workspace.mode === "recalculation";
  const blocker = mismatch
    ? "계좌 설정이 기존 기록과 다릅니다. 기존 계좌를 보존하고 1단계에서 새 비교 계좌를 만드세요."
    : g.blocked ||
      (!g.industry_ready ? "먼저 선택 날짜의 산업 자료를 준비하세요." : null);
  $("flow-blocker").hidden = !blocker;
  $("flow-blocker").textContent = blocker || "";
  $("flow-to-results").disabled = busy || !g.processed || mismatch;
  $("flow-to-results").setAttribute("aria-busy", String(busy));
  $("flow-job-status").replaceChildren();
  const jobs = currentJobs
    .filter((j) => j.workspace === workspace && j.as_of === g.as_of)
    .slice(-4)
    .reverse();
  for (const j of jobs) {
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
        el("div", "처리 중입니다. 중복 실행하지 말고 완료를 기다리세요."),
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
  $("flow-result-status").textContent = g.processed
    ? g.as_of +
      " 종가 처리 완료. 신호가 0건이어도 정상일 수 있습니다. 아래 대기는 다음 거래일 계획이며 증권사 주문이 아닙니다."
    : "선택 날짜의 종가 처리가 아직 완료되지 않았습니다. 아래는 마지막 저장 결과입니다. 2단계에서 먼저 처리하세요.";
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
