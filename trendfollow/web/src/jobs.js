async function job(action, extra = {}) {
  try {
    await api("/api/jobs", {
      workspace,
      action,
      as_of: $("asof").value,
      ...extra,
    });
    toast(
      "작업을 접수했습니다. 브라우저를 닫아도 UI 서버가 실행 중이면 처리됩니다.",
    );
    await poll();
  } catch (e) {
    toast(e.message, true);
  }
}
document
  .querySelectorAll("[data-action]")
  .forEach((b) => (b.onclick = () => job(b.dataset.action)));
function renderJobs(jobs) {
  if (firstJobs) {
    for (const j of jobs)
      if (["completed", "failed"].includes(j.status)) finished.add(j.id);
    firstJobs = false;
  }
  currentJobs = jobs;
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
    loading || jobs.some((j) => ["queued", "running"].includes(j.status));
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
      toast(
        (labels[j.action] || j.action) + " · " + (j.error || "완료"),
        j.status === "failed",
      );
    }
  }
}
