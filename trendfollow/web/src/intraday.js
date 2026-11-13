function watchSymbols() {
  return $("watch-symbols")
    .value.split(/[,\s]+/)
    .filter(Boolean);
}

let subscriptionPreview = null;
let subscriptionTimer;
let watchBasis = null;
let selectedPreparation = null;

function watchTimestamp(value) {
  const match =
    typeof value === "string" &&
    /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?\+09:00$/.exec(value);
  return match ? `${match[1]} ${match[2]}` : (value ?? "—");
}

function watchDecisionEvents(events) {
  return events.filter(
    (event) =>
      event.kind === "signal_state" &&
      [
        event.breakout_present,
        event.industry_action,
        event.volume_pass,
        event.entry_confirmed,
        event.holding_stop_breached,
      ].some((value) => value !== null && value !== undefined),
  );
}

function loadWatchBasis(session) {
  const query = new URLSearchParams({ workspace, session });
  const key = `${loadSequence}:${query}`;
  if (watchBasis?.key !== key) {
    const request = { key };
    watchBasis = request;
    api(`/api/watch/basis?${query}`)
      .then((result) => (request.result = result))
      .catch((error) => (request.error = error.message))
      .finally(() => {
        if (watchBasis === request) renderWatchControls();
      });
  }
  return watchBasis;
}

function renderWatchSubscriptions(asOf, session, symbols) {
  const status = $("watch-subscriptions");
  const query = new URLSearchParams({
    workspace,
    as_of: asOf,
    session,
    watch_mode: $("watch-mode").value,
  });
  for (const symbol of symbols) query.append("symbol", symbol);
  const key = `${loadSequence}:${query}`;
  if (subscriptionPreview?.key !== key) {
    const request = { key };
    subscriptionPreview = request;
    clearTimeout(subscriptionTimer);
    subscriptionTimer = setTimeout(async () => {
      try {
        request.result = await api(`/api/watch/subscriptions?${query}`);
      } catch (error) {
        request.error = error.message;
      }
      if (subscriptionPreview === request) renderWatchControls();
    }, 200);
  }
  if (subscriptionPreview.error) {
    status.textContent = `구독 수를 미리 확인하지 못했습니다. ${subscriptionPreview.error}`;
    return;
  }
  const result = subscriptionPreview.result;
  if (!result) {
    status.textContent = "필요한 구독 수를 계산 중입니다.";
    return;
  }
  const industries = result.industries.map(
    ({ name, members, holdings }) =>
      `${name} ${members}종목${holdings.length ? ` (보유 ${holdings.join(", ")})` : ""}`,
  );
  status.textContent =
    `필요 구독 ${result.count}개 · ` +
    (industries.length
      ? industries.join(", ")
      : "입력 종목의 시세만 구독합니다. 산업 동반 상승과 진입 조건은 미평가입니다.");
}

function renderWatchControls() {
  const s = snapshot?.workspace.id === workspace ? snapshot : null;
  const busy = currentJobs.some((j) =>
    ["queued", "running"].includes(j.status),
  );
  const symbols = watchSymbols();
  const session = $("watch-session").value;
  const watchMode = $("watch-mode").value;
  const basis = !loading && s && session ? loadWatchBasis(session) : null;
  const prepareBlocker = (() => {
    if (loading || !s) return "선택 계좌의 후보 자료를 확인 중입니다.";
    if (s.workspace.mode === "recalculation")
      return "정정 재계산 계좌입니다. 원계좌에서 감시 계획을 생성하세요.";
    if (s.status === "configuration_mismatch")
      return "계좌 설정이 저장된 자료와 다릅니다. 설정이 일치하는 계좌를 선택하세요.";
    if (s.rules.entry_method !== "setup") return "파동 셋업 계좌를 선택하세요.";
    if (s.rules.market_units_enabled)
      return "시장 유닛이 비활성화된 셋업 계좌를 선택하세요.";
    if (busy) return "진행 중인 작업이 끝나면 감시 계획을 생성할 수 있습니다.";
    if (!session) return "감시 거래일을 선택하세요.";
    if (basis?.error) return basis.error;
    if (!basis?.result) return "직전 거래일을 확인 중입니다.";
    if (watchMode === "industry" && basis.result.industry_ready === false)
      return `${basis.result.as_of} 산업 구성 자료가 없습니다. 감시 범위를 입력 종목만으로 선택하세요.`;
    if (!symbols.length)
      return "감시할 종목 코드를 입력하거나 시장 후보에서 선택하세요.";
    if (symbols.some((symbol) => !/^[0-9A-Z]{6}$/.test(symbol)))
      return "감시 종목 코드는 영문 대문자와 숫자 6자리로 입력하세요.";
    return "";
  })();
  const asOf = basis?.result?.as_of;
  const source = asOf ? `필요한 후보 기준일 ${asOf}. ` : "";
  const lastPreparation = currentJobs
    .filter(
      (j) =>
        j.workspace === workspace &&
        j.action === "prepare_intraday" &&
        j.as_of === asOf,
    )
    .at(-1);
  if (!prepareBlocker && basis.result.candidates_ready)
    renderWatchSubscriptions(asOf, session, symbols);
  else {
    subscriptionPreview = null;
    clearTimeout(subscriptionTimer);
    $("watch-subscriptions").textContent =
      !prepareBlocker && watchMode === "symbols"
        ? `필요 구독 ${new Set(symbols).size}개`
        : "";
  }
  $("prepare-watch").disabled = !!prepareBlocker;
  $("flow-watch-status").textContent =
    source +
    (prepareBlocker ||
      (basis.result.candidates_ready
        ? "저장된 종가 후보를 사용합니다."
        : "버튼을 누르면 일봉과 비교 자료를 확인하고 후보를 계산합니다.")) +
    (!prepareBlocker &&
    basis.result.account_as_of &&
    basis.result.account_as_of !== asOf
      ? " 계좌 처리일이 달라 보유 종목과 손절선은 이번 계획에 반영되지 않습니다."
      : "") +
    (lastPreparation?.status === "failed"
      ? ` 최근 계획 생성 실패 · ${lastPreparation.error}`
      : "");

  const plan = watchData?.plans.find((p) => p.id === $("watch-plan").value);
  const startBlocker = !watchData
    ? "감시 계획을 확인 중입니다."
    : !plan
      ? "선택 계좌의 저장된 감시 계획이 없습니다. 먼저 계획을 생성하세요."
      : plan.session !== watchData.today
        ? `선택한 계획은 ${plan.session}용입니다. 오늘(${watchData.today})의 계획을 선택하세요.`
        : !watchData.regular_session
          ? "관측은 해당 거래일 09:00~15:30 또는 NXT 애프터마켓 15:40~20:00 한국시간에 시작할 수 있습니다."
          : plan.as_of !== watchData.today_as_of
            ? `직전 거래일 ${watchData.today_as_of} 종가 후보로 감시 계획을 다시 생성하세요.`
            : watchData.control.active_or_unconfirmed.length
              ? "이미 실행 중이거나 종료를 확인 중인 관측이 있습니다."
              : "";
  $("start-watch").disabled = !!startBlocker;
  $("watch-start-status").textContent =
    startBlocker || "선택한 감시 계획으로 관측을 시작할 수 있습니다.";
  const hasRuns = !!watchData?.runs.length;
  $("volume-import").disabled = loading || busy || !hasRuns;
  $("close-compare").disabled = loading || busy || !hasRuns;
  if (
    typeof flowStep !== "undefined" &&
    flowStep === 4 &&
    typeof showNextAction === "function"
  ) {
    if (watchData?.control.active_or_unconfirmed.length)
      showNextAction(
        "관측 진행 중",
        "시세 수신과 구독 상태가 아래 결과에 표시됩니다. 관측은 20:00에 자동 종료됩니다.",
      );
    else if (busy)
      showNextAction("자료 준비 중", "작업 결과가 표시될 때까지 기다리세요.");
    else if (!startBlocker)
      showNextAction(
        "감시 계획 준비 완료 · 다음: 관측 ON",
        "선택한 계획으로 실시간 체결 관측을 시작하세요.",
        "start-watch",
      );
    else if (!symbols.length)
      showNextAction(
        "다음: 종목 코드 입력",
        "관측할 6자리 종목 코드를 입력하세요.",
        "watch-symbols",
      );
    else if (
      watchMode === "industry" &&
      basis?.result?.industry_ready === false
    )
      showNextAction(
        "다음: 감시 범위 선택",
        "산업 자료가 없으면 '입력 종목만'을 선택하세요.",
        "watch-mode",
      );
    else if (!prepareBlocker)
      showNextAction(
        "다음: 자료 준비 및 계획 생성",
        "직전 거래일 후보를 준비하고 감시 계획을 저장하세요.",
        "prepare-watch",
      );
    else showNextAction("관측 준비 조건 확인", prepareBlocker || startBlocker);
  }
}

async function renderWatch() {
  const selected = workspace,
    data = await api("/api/intraday?workspace=" + encodeURIComponent(selected));
  if (selected !== workspace) return;
  watchData = data;
  if (!$("watch-session").value)
    $("watch-session").value = data.default_session;
  const c = watchData.control;
  $("watch-state").textContent = c.enabled
    ? "ON · " + c.status
    : "OFF · " + c.status;
  const select = $("watch-plan"),
    old = select.value;
  select.replaceChildren();
  const plans = [...watchData.plans].sort((a, b) =>
    b.session.localeCompare(a.session),
  );
  for (const p of plans) {
    const op = el(
      "option",
      `${p.session} · 후보 ${p.as_of} · ${p.watch_mode === "symbols" ? "입력 종목만" : "전체 산업"} · ${p.targets.join(", ")} · ${p.subscriptions}구독`,
    );
    op.value = p.id;
    select.append(op);
  }
  const prepared = currentJobs.findLast(
    (j) =>
      j.workspace === selected &&
      j.action === "prepare_intraday" &&
      j.status === "completed" &&
      j.result?.session === $("watch-session").value,
  );
  if (
    prepared &&
    prepared.id !== selectedPreparation &&
    plans.some((p) => p.id === prepared.result.plan_id)
  ) {
    select.value = prepared.result.plan_id;
    selectedPreparation = prepared.id;
  } else if ([...select.options].some((x) => x.value === old))
    select.value = old;
  else {
    const today = plans.find(
      (p) => p.session === watchData.today && p.as_of === watchData.today_as_of,
    );
    if (today) select.value = today.id;
  }
  renderWatchControls();
  const run = watchData.runs[0],
    summary = run?.summary;
  const reasons = {
    subscription_limit: "KIS 실시간 구독 한도 초과",
    subscription_rejected: "KIS 실시간 구독 거절",
    subscription_incomplete: "관측 시간 내 구독 승인 미완료",
    credentials_error: "KIS 접속 설정 확인 실패",
    approval_error: "KIS WebSocket 접속키 발급 실패",
    connection_error: "KIS 실시간 연결 실패",
    observation_failed: "장중 관측 처리 실패",
  };
  const failure =
    summary?.status === "failed"
      ? ` / ${reasons[summary.failure_reason] || "관측 실패"}${summary.kis_error_code ? ` (${summary.kis_error_code})` : ""}${summary.rejected_symbol ? ` · 거절 종목 ${summary.rejected_symbol}` : ""}`
      : "";
  $("watch-info").className =
    "notice " +
    (summary?.status === "failed"
      ? "result-error"
      : summary?.connection_state === "active"
        ? "result-working"
        : summary?.status === "completed"
          ? "result-success"
          : "");
  $("watch-info").textContent = summary
    ? `최근 관측: ${summary.status}${run.as_of ? ` / 후보 기준일 ${run.as_of}` : ""} / 구독 승인 ${summary.subscription_acks ?? "미확인"} / 요청 ${summary.subscriptions_requested ?? "미확인"} / 체결 수신 ${summary.quote_ticks ?? "미확인"} / 연결 ${summary.connection_state ?? "미확인"}${failure}${summary.watch_mode === "symbols" ? " / 입력 종목만 관측 · 산업 및 진입 조건 미평가" : ""}`
    : "UI에서 수집한 장중 기록이 없습니다. OFF·미관측을 신호 0건으로 판단하지 않습니다. 감시 계획은 기준일 후보 자료로 생성합니다.";
  const events = run?.events || [];
  const quotes = Object.values(summary?.latest_target_quotes || {});
  const mismatches = quotes.filter((quote) => quote.price_basis_mismatch);
  $("watch-decision-status").className = mismatches.length
    ? "decision-alert"
    : "muted";
  $("watch-decision-status").textContent = mismatches.length
    ? `판단 보류: ${mismatches
        .map(
          (quote) =>
            `${quote.symbol}의 KIS 전일 종가 ${number(quote.previous_close)}원과 계획 기준 종가 ${number(quote.planned_previous_close)}원이 다릅니다. 현재가와 거래량은 수신했지만 이 계획의 돌파·진입 판단에는 사용할 수 없습니다. 기준 종가 자료를 확인·정정한 뒤 계획을 다시 생성하세요.`,
        )
        .join(" / ")}`
    : summary?.watch_mode === "symbols"
      ? "입력 종목만 관측 기록입니다. 산업 동반 상승과 진입 조건은 평가하지 않습니다."
      : "";
  $("watch-quotes").hidden = !quotes.length;
  if (quotes.length) {
    table(
      "watch-quotes",
      [
        ["최근 체결 시각 (한국시간)", (r) => watchTimestamp(r.timestamp)],
        ["종목", (r) => r.symbol],
        ["시장", (r) => (r.market === "nxt_after" ? "NXT 애프터" : "KRX 정규")],
        ["최근 체결가", (r) => number(r.price)],
        ["누적 거래량", (r) => number(r.cumulative_volume)],
        ["KIS 전일 종가", (r) => number(r.previous_close)],
        ["후보 기준 종가", (r) => number(r.planned_previous_close)],
        [
          "가격 기준",
          (r) => (r.price_basis_mismatch ? "불일치 · 판단 보류" : "일치"),
        ],
      ],
      quotes,
    );
  }
  const hourly = summary?.hourly_quotes || [];
  $("watch-hourly").hidden = !hourly.length;
  if (hourly.length) {
    const hourSelect = $("watch-hour-filter");
    const previousHour = hourSelect.value;
    const hours = [...new Set(hourly.map((r) => `${r.hour}|${r.market}`))].sort().reverse();
    hourSelect.replaceChildren();
    for (const hour of hours) {
      const option = el("option", `${watchTimestamp(hour.split("|")[0]).slice(0, 16)} · ${hour.endsWith("nxt_after") ? "NXT 애프터" : "KRX 정규"}`);
      option.value = hour;
      hourSelect.append(option);
    }
    hourSelect.value = hours.includes(previousHour) ? previousHour : hours[0];
    table(
      "watch-hourly-table",
      [
        ["시간대 (한국시간)", (r) => watchTimestamp(r.hour).slice(0, 16)],
        ["종목", (r) => r.symbol],
        ["시장", (r) => r.market === "nxt_after" ? "NXT 애프터" : "KRX 정규"],
        ["마지막 체결", (r) => watchTimestamp(r.timestamp)],
        ["잠정 가격", (r) => number(r.price)],
        ["잠정 누적 거래량", (r) => number(r.cumulative_volume)],
        ["상태", (r) => r.price_basis_mismatch ? "가격 기준 불일치 · 판단 보류" : r.sample_status],
        ["돌파", (r) => r.valid && !r.price_basis_mismatch ? flag(r.breakout_present) : "판단 보류"],
        ["거래량 조건", (r) => r.valid && !r.price_basis_mismatch ? flag(r.volume_pass) : "판단 보류"],
        ["진입 조건", (r) => r.valid && !r.price_basis_mismatch ? flag(r.entry_confirmed) : "판단 보류"],
      ],
      hourly.filter((r) => `${r.hour}|${r.market}` === hourSelect.value),
    );
  }
  const decisionEvents = watchDecisionEvents(events);
  $("watch-decision-history").hidden = !decisionEvents.length;
  table(
    "live-events",
    [
      ["시각 (한국시간)", (r) => watchTimestamp(r.timestamp)],
      ["종목", (r) => r.symbol],
      ["판단 시 가격", (r) => number(r.price)],
      ["돌파", (r) => flag(r.breakout_present)],
      ["산업 액션", (r) => flag(r.industry_action)],
      ["거래량 조건", (r) => flag(r.volume_pass)],
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
    decisionEvents,
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
$("watch-plan").onchange = renderWatchControls;
$("watch-symbols").oninput = renderWatchControls;
$("watch-session").oninput = renderWatchControls;
$("watch-mode").onchange = renderWatchControls;
$("watch-hour-filter").onchange = () => renderWatch().catch((e) => toast(e.message, true));
$("prepare-watch").onclick = () =>
  job("prepare_intraday", {
    as_of: watchBasis.result.as_of,
    symbols: watchSymbols(),
    session: $("watch-session").value,
    watch_mode: $("watch-mode").value,
  });
$("start-watch").onclick = async () => {
  try {
    await api("/api/watch/start", {
      workspace,
      plan_id: $("watch-plan").value,
      seconds: Number($("watch-seconds").value),
    });
    toast("관측 시작을 요청했습니다. 현재 거래 시간의 체결 채널에 연결합니다.");
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
