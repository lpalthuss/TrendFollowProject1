async function bootstrap(selected) {
  boot = await api("/api/bootstrap");
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
flow(1);
bootstrap().catch((e) => toast(e.message, true));
setInterval(poll, 5000);
