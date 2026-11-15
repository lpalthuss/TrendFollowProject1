function renderPriceSourceHelp() {
  $("price-source-help").textContent =
    $("price-source").value === "pykrx"
      ? "모든 가격 갱신·일별 처리에 적용 · 저장된 날짜는 유지하고 새 거래일만 KRX에서 추가 · KRX 로그인 필요"
      : "모든 가격 갱신·일별 처리에 적용 · 저장된 날짜는 유지하고 새 거래일만 네이버에서 추가";
}
function accountOptionLabel(w) {
  const kind = w.builtin
    ? "기본"
    : w.mode === "recalculation"
      ? "정정 비교"
      : "일반";
  return `${w.name} · ${kind} · ${w.account_as_of || "미시작"}${w.archived_at ? " · 보관" : ""}`;
}
function renderArchiveControl() {
  const selected = boot.workspaces.find((w) => w.id === workspace);
  const button = $("toggle-account-archive");
  button.disabled = !selected || Boolean(selected.builtin);
  button.textContent = selected?.archived_at
    ? "이 계좌 보관 해제"
    : "이 계좌 보관";
  button.title = selected?.builtin
    ? "기본 계좌는 보관할 수 없습니다."
    : "보관은 계좌 자료를 삭제하지 않고 기본 목록에서 숨깁니다.";
  $("rename-account").hidden = !selected || Boolean(selected.builtin);
  $("rename-account-name").value = selected?.name || "";
}
async function bootstrap(selected) {
  boot = await api("/api/bootstrap");
  const rememberedSource = localStorage.getItem("trendfollow-price-source");
  if (["naver", "pykrx"].includes(rememberedSource))
    $("price-source").value = rememberedSource;
  renderPriceSourceHelp();
  const select = $("workspace");
  select.replaceChildren();
  const visible = boot.workspaces.filter(
    (w) => $("show-archived").checked || !w.archived_at,
  );
  for (const w of visible) {
    const op = el("option", accountOptionLabel(w));
    op.value = w.id;
    select.append(op);
  }
  const preferred =
    selected || workspace || localStorage.getItem("trendfollow-workspace");
  const previous = workspace;
  workspace = visible.some((w) => w.id === preferred)
    ? preferred
    : (
        visible
          .filter((w) => !w.builtin && w.mode !== "recalculation")
          .at(-1) ||
        visible.find((w) => w.id === "setup-rs") ||
        visible[0]
      ).id;
  if (workspace !== previous) watchData = null;
  if (workspace !== previous) {
    candidateView = null;
    candidateDates = null;
    candidateSelection = null;
    $("candidate-date").replaceChildren();
  }
  localStorage.setItem("trendfollow-workspace", workspace);
  select.value = workspace;
  renderArchiveControl();
  const presets = $("preset");
  presets.replaceChildren();
  for (const p of boot.presets) {
    const op = el("option", p.name);
    op.value = p.id;
    presets.append(op);
  }
  presets.value =
    boot.presets.find((p) => p.id === "config-pyramiding")?.id ||
    boot.presets[0].id;
  choosePreset();
  await load();
  await renderWatch();
}
$("workspace").onchange = async () => {
  workspace = $("workspace").value;
  $("rename-account").open = false;
  renderArchiveControl();
  localStorage.setItem("trendfollow-workspace", workspace);
  watchData = null;
  candidateView = null;
  candidateDates = null;
  candidateSelection = null;
  $("candidate-date").replaceChildren();
  recoveryInfo = null;
  recoveryRows = null;
  recoveryKey = "";
  $("watch-symbols").value = "";
  $("watch-session").value = "";
  revision = "";
  $("context").textContent = "선택 계좌의 자료를 읽고 있습니다.";
  await load();
  await renderWatch();
};
$("show-archived").onchange = () =>
  bootstrap().catch((e) => toast(e.message, true));
$("toggle-account-archive").onclick = async () => {
  const selected = boot.workspaces.find((w) => w.id === workspace);
  if (!selected || selected.builtin) return;
  const archived = !selected.archived_at;
  const button = $("toggle-account-archive");
  button.disabled = true;
  try {
    await api("/api/workspaces/archive", { workspace, archived });
    await bootstrap();
    toast(
      archived
        ? "계좌를 보관했습니다. 자료는 유지되며 '보관 계좌 표시'에서 다시 선택할 수 있습니다."
        : "계좌 보관을 해제했습니다.",
    );
  } catch (e) {
    renderArchiveControl();
    toast(e.message, true);
  }
};
$("rename-account-form").onsubmit = async (event) => {
  event.preventDefault();
  const name = $("rename-account-name").value.trim();
  const submit = $("rename-account-form").querySelector("button");
  submit.disabled = true;
  try {
    await api("/api/workspaces/rename", { workspace, name });
    await bootstrap(workspace);
    $("rename-account").open = false;
    toast("계좌 이름을 변경했습니다. 전략 설정과 매매 기록은 유지됩니다.");
  } catch (e) {
    toast(e.message, true);
  } finally {
    submit.disabled = false;
  }
};
$("reload").onclick = () => load().then(renderWatch);
$("price-source").onchange = () => {
  localStorage.setItem("trendfollow-price-source", $("price-source").value);
  renderPriceSourceHelp();
};
flow(1);
bootstrap().catch((e) => toast(e.message, true));
setInterval(() => {
  if (currentJobs.some((j) => ["queued", "running"].includes(j.status)))
    renderJobProgress();
}, 1000);
setInterval(poll, 2000);
