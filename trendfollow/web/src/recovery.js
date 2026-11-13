function receiveRecovery(value) {
  recoveryInfo = value || { report: null, policy: "manual" };
  const r = recoveryInfo.report;
  const key = r
    ? [workspace, r.id, r.applied, r.stale].join("|")
    : workspace + "none";
  if (key !== recoveryKey) {
    recoveryKey = key;
    revisionOffset = 0;
    $("recovery-history").open = !!r && !r.applied;
    $("revision-reviewed").checked = false;
    $("price-reviewed").checked = false;
    $("revision-policy").value = recoveryInfo.policy;
    if (r) loadRevisionRows().catch((e) => toast(e.message, true));
    else recoveryRows = null;
  }
  renderRecoveryControls();
}
async function loadRevisionRows() {
  const selected = workspace,
    id = recoveryInfo?.report?.id;
  if (!id) return;
  const query = new URLSearchParams({
    workspace: selected,
    report_id: id,
    offset: String(revisionOffset),
    symbol: $("revision-symbol").value.trim(),
    source: $("revision-source").value,
    date: $("revision-date").value,
  });
  const result = await api("/api/revision?" + query);
  if (selected !== workspace || id !== recoveryInfo?.report?.id) return;
  recoveryRows = result;
  table(
    "revision-table",
    [
      ["자료", (x) => (x.source === "prices" ? "가격·거래량" : "산업 동료")],
      ["날짜", (x) => x.date],
      ["코드", (x) => x.symbol],
      [
        "필드",
        (x) =>
          ({
            open: "시가",
            high: "고가",
            low: "저가",
            close: "종가",
            volume: "거래량",
            day_return: "동료 수익률",
            benchmark_day_return: "벤치마크 수익률",
            quote_eligible: "시세 적격",
            sector: "산업 코드",
          })[x.field] || x.field,
      ],
      ["이전 값", (x) => x.old_value],
      ["정정 값", (x) => x.new_value],
    ],
    result.rows,
  );
  $("revision-page").textContent =
    `${revisionOffset + 1}~${Math.min(revisionOffset + 100, result.total)} / ${result.total}건`;
  renderRecoveryControls();
}
function renderRecoveryControls() {
  if (!$("recovery-panel")) return;
  const r = recoveryInfo?.report,
    busy =
      loading ||
      currentJobs.some((j) => ["queued", "running"].includes(j.status));
  $("recovery-history").hidden = !r;
  $("recovery-history-summary").textContent = r
    ? r.applied
      ? "정정 반영 완료 · 변경 이력 " + number(r.row_changes) + "건 보기"
      : "정정 검토 필요 · 변경 내역 " + number(r.row_changes) + "건"
    : "변경 내역 확인";
  $("recovery-history").classList.toggle("applied", !!r?.applied);
  $("recovery-status").hidden = !!r?.applied;
  $("revision-reviewed-label").hidden = !!r?.applied;
  $("revision-continue").hidden = !!r?.applied;
  $("recovery-status").textContent = !r
    ? "변경 보고서가 없습니다. 실패 사유가 과거 입력 정정이면 대조를 실행하세요."
    : r.applied
      ? "정정 반영 완료 · 원래 결정과 변경 이력을 보존했습니다."
      : r.stale
        ? "보고서 이후 계좌가 바뀌었습니다. 다시 대조하세요."
        : "계좌 보존 · 정정 검토 필요 · 작업이 대기 중인 상태가 아닙니다.";
  if (r) {
    facts("recovery-facts", [
      ["계좌 보존일", r.account_as_of],
      ["계속 처리할 날짜", r.as_of],
      ["변경 코드", number(r.changed_symbols)],
      ["가격·거래량 필드", number(r.price_changes)],
      ["산업 결정 필드", number(r.peer_changes)],
      [
        "전체 과거 대조",
        r.baseline_origin === "verified_legacy_raw_cache"
          ? "원본 캐시·계좌 지문 일치"
          : "보존 스냅샷 확인",
      ],
    ]);
    $("recovery-explanation").textContent =
      r.explanation +
      (!r.applied && guidance?.as_of !== r.as_of
        ? " 정정 검토 후 계속하면 " +
          r.as_of +
          "부터 선택한 기준일 " +
          guidance?.as_of +
          "까지 순서대로 처리합니다."
        : "");
    $("recovery-blocker").hidden = r.can_continue;
    $("recovery-blocker").textContent = r.block_reason || "";
    $("price-review-label").hidden = r.applied || !r.price_fields_changed;
  }
  $("revision-continue").disabled =
    busy ||
    !r ||
    guidance?.as_of < r.as_of ||
    r.applied ||
    r.stale ||
    !r.can_continue ||
    !$("revision-reviewed").checked ||
    (r.price_fields_changed && !$("price-reviewed").checked);
  $("revision-replay").disabled = busy || !r || guidance?.as_of !== r.as_of;
  $("revision-continue").setAttribute("aria-busy", String(busy));
  $("revision-replay").setAttribute("aria-busy", String(busy));
  $("revision-prev").disabled = revisionOffset === 0;
  $("revision-next").disabled =
    !recoveryRows || revisionOffset + 100 >= recoveryRows.total;
}
$("revision-reviewed").onchange = renderRecoveryControls;
$("price-reviewed").onchange = renderRecoveryControls;
$("revision-search").onclick = () => {
  revisionOffset = 0;
  loadRevisionRows().catch((e) => toast(e.message, true));
};
$("revision-prev").onclick = () => {
  revisionOffset = Math.max(0, revisionOffset - 100);
  loadRevisionRows().catch((e) => toast(e.message, true));
};
$("revision-next").onclick = () => {
  revisionOffset += 100;
  loadRevisionRows().catch((e) => toast(e.message, true));
};
$("revision-continue").onclick = () =>
  job("revision_continue", {
    as_of: recoveryInfo.report.as_of,
    catch_up_to: $("asof").value,
    report_id: recoveryInfo.report.id,
    reviewed: $("revision-reviewed").checked,
    price_reviewed: $("price-reviewed").checked,
  });
$("revision-replay").onclick = () =>
  job("revision_replay", { report_id: recoveryInfo.report.id });
$("revision-open-replay").onclick = () =>
  bootstrap(replayWorkspace).then(() => flow(3));
$("revision-save-policy").onclick = async () => {
  try {
    await api("/api/revision/policy", {
      workspace,
      policy: $("revision-policy").value,
    });
    toast(
      "계좌별 정정 정책을 저장했습니다. 기존 정정은 보고서에서 별도로 검토하세요.",
    );
  } catch (e) {
    toast(e.message, true);
  }
};
