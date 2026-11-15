function readGuidance(p) {
  if (!p.guidance)
    throw new Error(
      "실행 중인 UI 서버가 이전 버전입니다. 터미널에서 Ctrl+C 후 기존 ui 명령으로 다시 실행하고 페이지를 새로고침하세요.",
    );
  return p.guidance;
}
async function load(prime = true) {
  const selected = workspace,
    ticket = ++loadSequence;
  loading = true;
  renderWatchControls();
  document
    .querySelectorAll("[data-action]")
    .forEach((b) => (b.disabled = true));
  try {
    const data = await api(
      "/api/snapshot?workspace=" + encodeURIComponent(selected),
    );
    if (selected !== workspace || ticket !== loadSequence) return;
    snapshot = data;
    loading = false;
    render();
    await loadCandidateDates();
    if (selected !== workspace || ticket !== loadSequence) return;
    if (prime) markExisting();
    const p = await api(
      "/api/pulse?workspace=" +
        selected +
        ($("asof").value ? "&as_of=" + $("asof").value : ""),
    );
    if (selected !== workspace || ticket !== loadSequence) return;
    revision = p.revision;
    guidance = readGuidance(p);
    receiveRecovery(p.recovery);
    if (!$("asof").value) $("asof").value = guidance.as_of;
    renderJobs(p.jobs);
  } catch (e) {
    if (selected !== workspace || ticket !== loadSequence) return;
    loading = false;
    renderWatchControls();
    toast(e.message, true);
    $("context").textContent = e.message;
  }
}
function render() {
  const s = snapshot,
    r = s.rules,
    rows = s.rows,
    ready = rows.length > 0;
  $("context").textContent =
    `${s.workspace.name} · ${s.as_of || "분석 전"} 완료 종가 · ${r.rs_method === "percentile" ? "RS 순위" : "Mansfield RS"} · ${r.entry_method === "setup" ? "파동 셋업" : "돌파 기준선"}`;
  $("account-status").textContent =
    s.status === "not_started"
      ? "미시작 · 설정 저장됨"
      : s.status === "configuration_mismatch"
        ? "설정 불일치 · 기존 계좌 보존"
        : "기존 계좌 · 설정 일치";
  const frozen = s.workspace.mode === "recalculation";
  const builtin = Boolean(s.workspace.builtin);
  const accountContext = $("candidate-account-context");
  accountContext.className = `candidate-account-context ${frozen ? "frozen" : builtin ? "unavailable" : ""}`;
  $("candidate-account-title").textContent = frozen
    ? `정정 비교 계좌 · ${s.account_as_of || "저장일 미확인"}에 고정`
    : builtin
      ? "기본 계좌 · 날짜별 후보군 적용 불가"
      : s.candidate_policy
        ? "일반 모의계좌 · 날짜별 후보군 적용 중"
        : "일반 모의계좌 · 날짜별 후보군 미적용";
  $("candidate-account-description").textContent = frozen
    ? "시장 후보는 조회·조건 계산할 수 있지만 이 계좌의 날짜별 후보군 설정과 일별 처리는 할 수 없습니다. 원계좌로 이동해 이어 처리하세요."
    : builtin
      ? "시장 후보는 조회할 수 있습니다. 날짜별 후보군으로 이어 처리하려면 별도 일반 모의계좌가 필요합니다."
      : s.candidate_policy
        ? `${s.candidate_policy.enabled_after} 이후 거래일부터 저장된 날짜별 종목을 이 계좌의 일별 판단에 사용합니다.`
        : "시장 후보 표에는 계좌 규칙으로 계산한 결과가 보이지만, 날짜별 종목 목록은 아직 계좌의 일별 처리에 적용되지 않았습니다.";
  $("candidate-policy").hidden = Boolean(
    builtin || frozen || s.candidate_policy,
  );
  $("candidate-policy-note").textContent = frozen
    ? "이 계좌는 정정 비교용으로 고정돼 있어 적용·일별 처리 버튼이 비활성화됩니다."
    : builtin
      ? "기본 계좌에서는 날짜별 후보군을 적용할 수 없습니다."
      : s.candidate_policy
        ? `적용 중 · ${s.candidate_policy.enabled_after}까지의 기록은 유지하고 다음 거래일부터 날짜별 선정 종목을 사용합니다.`
        : s.account_as_of
          ? `미적용 · 적용 버튼을 누르면 계좌 처리일 ${s.account_as_of} 이후부터 날짜별 선정 종목을 사용합니다.`
          : "첫 거래일을 처리한 뒤 날짜별 후보군 적용을 켤 수 있습니다.";
  $("notice").hidden =
    s.status === "ready" && s.workspace.mode !== "recalculation";
  $("notice").textContent =
    s.workspace.mode === "recalculation"
      ? "정정 자료로 과거 판단을 다시 계산한 고정 연구 계좌입니다. 원계좌의 당시 운용 결과와 구분하고 실제 운용은 원계좌에서 계속하세요."
      : s.status === "configuration_mismatch"
        ? "계좌의 설정 지문이 일치하지 않습니다. 기존 계좌에 새 설정을 덮어쓰지 말고 별도 계좌를 생성하세요."
        : "아직 시작하지 않은 계좌입니다. 운용 순서 2단계에서 자료를 준비하고 종가를 처리하세요.";
  $("notice").className =
    "notice " +
    (s.status === "configuration_mismatch" ? "notice-error" : "notice-info");
  $("cash").textContent = number(s.cash) + "원";
  $("holding-count").textContent =
    s.positions.length + " / " + s.pending.length;
  $("rs-count").textContent = ready
    ? number(rows.filter((x) => x.rs_pass).length)
    : "—";
  $("signal-count").textContent = ready
    ? number(rows.filter((x) => x.signal === true).length)
    : "—";
  $("signal-note").textContent = ready
    ? "전체 조건 통과 · 다음 시가 계획"
    : "분석 자료 없음 · 0건과 구분";
  $("capital-note").textContent =
    `${s.units}유닛 · 계좌 기준 ${s.account_as_of || "미시작"}`;
  $("active-config").textContent = JSON.stringify(
    {
      rules: s.rules,
      limits: s.limits,
      universe: s.workspace.universe,
      data_dir: s.workspace.data_dir,
      state: s.workspace.state,
    },
    null,
    2,
  );
  updateIndustryFilter();
  renderStocks();
  renderSignals();
  renderValidation();
  $("source-label").textContent =
    `신호 기준 ${s.as_of || "없음"} / 계좌 기준 ${s.account_as_of || "미시작"} · 종가 계획과 모의 체결 구분`;
  $("updated").textContent =
    "화면 확인 " + new Date().toLocaleTimeString("ko-KR");
  renderWatchControls();
}
async function poll() {
  if (!workspace || polling || loading) return;
  polling = true;
  const selected = workspace;
  try {
    const p = await api(
      "/api/pulse?workspace=" +
        selected +
        ($("asof").value ? "&as_of=" + $("asof").value : ""),
    );
    if (selected !== workspace) return;
    guidance = readGuidance(p);
    receiveRecovery(p.recovery);
    renderJobs(p.jobs);
    if (p.revision !== revision) {
      const old = snapshot;
      await load(false);
      if (old && selected === workspace && snapshot.workspace.id === selected) {
        collectNotifications(snapshot.pending, "종가 매수 계획");
        collectNotifications(
          (snapshot.logs.events || []).filter(
            (r) => r.kind === "rs_exit_scheduled",
          ),
          "종가 청산 신호",
        );
      }
    }
    await renderWatch();
  } catch (e) {
    $("updated").textContent = "연결 확인 필요 · " + e.message;
  } finally {
    polling = false;
  }
}
