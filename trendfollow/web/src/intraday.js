async function renderWatch() {
  const selected = workspace,
    data = await api("/api/intraday?workspace=" + selected);
  if (selected !== workspace) return;
  watchData = data;
  renderFlow();
  const c = watchData.control;
  $("watch-state").textContent = c.enabled
    ? "ON · " + c.status
    : "OFF · " + c.status;
  const select = $("watch-plan"),
    old = select.value;
  select.replaceChildren();
  for (const p of watchData.plans) {
    const op = el(
      "option",
      `${p.session} · ${p.targets.join(", ")} · ${p.subscriptions}구독`,
    );
    op.value = p.id;
    select.append(op);
  }
  if ([...select.options].some((x) => x.value === old)) select.value = old;
  const run = watchData.runs[0],
    summary = run?.summary;
  $("watch-info").textContent = summary
    ? `최근 관측: ${summary.status} / 체결 수신 ${summary.quote_ticks ?? "미확인"} / 연결 ${summary.connection_state ?? "미확인"} / 동시간 거래량 기준 미확보 시 진입은 미확정입니다.`
    : "UI에서 수집한 장중 기록이 없습니다. OFF·미관측을 신호 0건으로 판단하지 않습니다. 감시 계획은 기준일 후보 자료로 생성합니다.";
  const events = run?.events || [];
  table(
    "live-events",
    [
      ["시각", (r) => r.timestamp],
      ["종목", (r) => r.symbol],
      ["현재가", (r) => number(r.price)],
      ["돌파", (r) => flag(r.breakout_present)],
      ["산업 액션", (r) => flag(r.industry_action)],
      ["거래량", (r) => flag(r.volume_pass)],
      [
        "진입 조건",
        (r) =>
          r.entry_confirmed === true
            ? r.ui_current
              ? "잠정 충족"
              : "과거 관측 · 현재 미확정"
            : "미확정",
      ],
      [
        "손절 접촉",
        (r) =>
          r.holding_stop_breached === true
            ? "접촉"
            : r.holding_stop_breached === false
              ? "없음"
              : "미확인",
      ],
    ],
    events,
  );
  if (run) {
    const deliver = primedRuns.has(run.id);
    primedRuns.add(run.id);
    collectNotifications(
      events.filter(
        (e) =>
          e.ui_current &&
          (e.entry_confirmed === true || e.holding_stop_breached === true),
      ),
      "장중 잠정 신호",
      deliver,
    );
  }
}
$("prepare-watch").onclick = () =>
  job("prepare_intraday", {
    symbols: $("watch-symbols")
      .value.split(/[,\s]+/)
      .filter(Boolean),
    session: $("watch-session").value,
  });
$("start-watch").onclick = async () => {
  try {
    await api("/api/watch/start", {
      workspace,
      plan_id: $("watch-plan").value,
      seconds: Number($("watch-seconds").value),
    });
    toast("관측 시작을 요청했습니다. 정규장만 연결합니다.");
    await renderWatch();
  } catch (e) {
    toast(e.message, true);
  }
};
$("stop-watch").onclick = async () => {
  try {
    const r = await api("/api/watch/stop", {});
    toast("OFF 요청 · " + r.status);
    await renderWatch();
  } catch (e) {
    toast(e.message, true);
  }
};
$("volume-import").onclick = () => {
  if (!watchData?.runs.length)
    return toast("누적할 관측 기록이 없습니다.", true);
  job("volume_import", { run_id: watchData.runs[0].id });
};
$("close-compare").onclick = () => {
  if (!watchData?.runs.length)
    return toast("대조할 장중 기록이 없습니다.", true);
  job("intraday_close", { run_id: watchData.runs[0].id });
};
