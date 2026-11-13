function renderPriceSourceHelp() {
  $("price-source-help").textContent =
    $("price-source").value === "pykrx"
      ? "모든 가격 갱신·일별 처리에 적용 · 저장된 날짜는 유지하고 새 거래일만 KRX에서 추가 · KRX 로그인 필요"
      : "모든 가격 갱신·일별 처리에 적용 · 저장된 날짜는 유지하고 새 거래일만 네이버에서 추가";
}
async function bootstrap(selected) {
  boot = await api("/api/bootstrap");
  const rememberedSource = localStorage.getItem("trendfollow-price-source");
  if (["naver", "pykrx"].includes(rememberedSource))
    $("price-source").value = rememberedSource;
  renderPriceSourceHelp();
  const select = $("workspace");
  select.replaceChildren();
  for (const w of boot.workspaces) {
    const op = el("option", w.name);
    op.value = w.id;
    select.append(op);
  }
  const preferred =
    selected || workspace || localStorage.getItem("trendfollow-workspace");
  const previous = workspace;
  workspace = boot.workspaces.some((w) => w.id === preferred)
    ? preferred
    : (
        boot.workspaces
          .filter((w) => !w.builtin && w.mode !== "recalculation")
          .at(-1) ||
        boot.workspaces.find((w) => w.id === "setup-rs") ||
        boot.workspaces[0]
      ).id;
  if (workspace !== previous) watchData = null;
  localStorage.setItem("trendfollow-workspace", workspace);
  select.value = workspace;
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
  localStorage.setItem("trendfollow-workspace", workspace);
  watchData = null;
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
