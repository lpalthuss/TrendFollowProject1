async function job(action, extra = {}) {
  if (submittingAction) return;
  submittingAction = action;
  renderJobProgress();
  try {
    const queued = await api("/api/jobs", {
      workspace,
      action,
      as_of: $("asof").value,
      ...(action === "daily" ? { price_source: $("price-source").value } : {}),
      ...extra,
    });
    if (!currentJobs.some((item) => item.id === queued.id))
      currentJobs = [...currentJobs, queued];
    renderJobProgress();
    toast(
      "작업을 접수했습니다. 브라우저를 닫아도 UI 서버가 실행 중이면 처리됩니다.",
    );
    await poll();
  } catch (e) {
    toast(e.message, true);
  } finally {
    submittingAction = null;
    renderJobProgress();
  }
}
document
  .querySelectorAll("[data-action]")
  .forEach((b) => (b.onclick = () => job(b.dataset.action)));
const jobButtonIds = {
  prepare_intraday: ["prepare-watch"],
  revision_continue: ["revision-continue"],
  revision_replay: ["revision-replay"],
  intraday_close: ["close-compare"],
  volume_import: ["volume-import"],
};
function renderJobProgress() {
  const active = currentJobs.findLast(
    (j) => j.workspace === workspace && ["queued", "running"].includes(j.status),
  );
  const action = active?.action || submittingAction;
  const panel = $("job-progress");
  panel.hidden = !action;
  document.querySelectorAll(".job-active-button").forEach((button) => {
    button.classList.remove("job-active-button");
    button.removeAttribute("data-progress-label");
  });
  if (!action) return;
  const buttons = [
    ...document.querySelectorAll(`[data-action="${action}"]`),
    ...(jobButtonIds[action] || []).map((id) => $(id)).filter(Boolean),
  ];
  for (const button of buttons) {
    button.classList.add("job-active-button");
    button.dataset.progressLabel = "진행 중";
  }
  $("job-progress-title").textContent = `${labels[action] || action} · ${active?.status === "running" ? "진행 중" : "접수 중"}`;
  $("job-progress-phase").textContent = active?.progress || "작업 접수 중";
  const elapsed = active ? Math.max(0, Math.floor((Date.now() - Date.parse(active.created_at)) / 1000)) : 0;
  $("job-progress-elapsed").textContent = `경과 ${Math.floor(elapsed / 60)}분 ${elapsed % 60}초`;
  const current = active?.progress_current;
  const total = active?.progress_total;
  const counted = Number.isInteger(current) && Number.isInteger(total) && total > 0;
  const bar = $("job-progress-bar");
  if (counted) bar.value = Math.min(100, Math.round(current / total * 100));
  else bar.removeAttribute("value");
  $("job-progress-count").textContent = counted
    ? `${number(current)} / ${number(total)} · ${Math.round(current / total * 100)}%`
    : "현재 단계를 처리 중입니다. 완료 건수를 알 수 없는 작업은 진행률 대신 단계를 표시합니다.";
}
function renderJobs(jobs) {
  if (firstJobs) {
    for (const j of jobs)
      if (["completed", "failed"].includes(j.status)) finished.add(j.id);
    firstJobs = false;
  }
  currentJobs = jobs;
  renderJobProgress();
  const replay = jobs
    .filter(
      (j) =>
        j.workspace === workspace &&
        j.action === "revision_replay" &&
        j.status === "completed",
    )
    .at(-1);
  replayWorkspace = replay?.result?.workspace_id || null;
  $("revision-open-replay").hidden = !replayWorkspace;
  const busy =
    loading || !!submittingAction || jobs.some((j) => ["queued", "running"].includes(j.status));
  $("price-source").disabled = busy;
  document.querySelectorAll("[data-action]").forEach((b) => {
    b.disabled = busy;
    b.setAttribute("aria-busy", String(busy));
  });
  table(
    "job-table",
    [
      ["작업", (r) => labels[r.action] || r.action],
      ["기준일", (r) => r.as_of],
      ["상태", (r) => labels[r.status] || r.status],
      ["결과", (r) => r.error || JSON.stringify(r.result || {})],
    ],
    jobs.slice().reverse(),
  );
  renderFlow();
  for (const j of jobs) {
    if (["completed", "failed"].includes(j.status) && !finished.has(j.id)) {
      finished.add(j.id);
      const processed = j.action === "daily" && j.result?.processed_dates;
      toast(
        (labels[j.action] || j.action) +
          " · " +
          (j.error ||
            (processed
              ? `${processed.length}개 거래일 처리 완료` +
                (j.result.reconstructed_industry_dates?.length
                  ? ` · 산업 자료 ${j.result.reconstructed_industry_dates.length}일 재구성`
                  : "") +
                (j.result.source_comparison?.difference_count
                  ? ` · 원천 가격·거래량 차이 ${j.result.source_comparison.difference_count}건 기록`
                  : "")
              : "완료")),
        j.status === "failed",
      );
    }
  }
}
