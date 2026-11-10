const fieldSpecs = [
  ["rules", "rs_method", "상대강도 방식", ["percentile", "mansfield"]],
  ["rules", "min_rs", "RS 최소 순위"],
  ["rules", "min_mansfield", "Mansfield 최소값"],
  [
    "rules",
    "mansfield_benchmark",
    "Mansfield 비교 기준",
    ["universe", "market_index"],
  ],
  ["rules", "entry_method", "진입 방식", ["setup", "breakout"]],
  [
    "rules",
    "initial_stop_method",
    "초기 손절 방식",
    ["fixed", "atr", "hybrid"],
  ],
  [
    "rules",
    "initial_stop_reference",
    "초기 손절 기준",
    ["entry_fill", "signal_close"],
  ],
  ["rules", "fixed_stop_fraction", "고정 손절 폭 (%)", null, 100],
  ["rules", "stop_atr", "ATR 배수"],
  ["rules", "stop_limit_enabled", "손절 폭 제한"],
  ["rules", "max_stop_fraction", "손절 폭 제한 (%)", null, 100],
  ["rules", "sector_require_action", "산업 동반 상승 필수"],
  ["rules", "rs_exit_enabled", "RS 약화 청산"],
  ["rules", "market_units_enabled", "시장 유닛 상한"],
  ["rules", "pyramiding_review_enabled", "추가매수 검토"],
  ["rules", "pyramiding_enabled", "추가매수 모의 체결"],
  ["rules", "pyramiding_max_additions", "최대 추가 횟수 (0~3)"],
  ["limits", "max_trade_risk", "최대 거래 위험 (%)", null, 100],
  ["limits", "total_risk", "계좌 전체 위험 (%)", null, 100],
  ["limits", "max_position", "종목 최대 비중 (%)", null, 100],
  ["limits", "max_sector", "산업 최대 비중 (%)", null, 100],
  ["limits", "cash_reserve", "현금 유보 (%)", null, 100],
];
function renderSettings() {
  const root = $("settings-fields");
  root.replaceChildren();
  fieldSpecs.forEach(([group, key, label, choices, scale]) => {
    const value = draft[group][key],
      l = el("label", label),
      input = el(choices ? "select" : "input");
    if (choices)
      choices.forEach((c) => {
        const op = el(
          "option",
          {
            percentile: "RS 수익률 순위",
            mansfield: "Mansfield RS",
            universe: "시장 ETF",
            market_index: "KOSPI / KOSDAQ 지수",
            setup: "파동 셋업",
            breakout: "고가 돌파",
            fixed: "고정 손절",
            atr: "ATR 손절",
            hybrid: "자동 전환 C",
            entry_fill: "모의 체결가",
            signal_close: "신호일 종가",
          }[c] || c,
        );
        op.value = c;
        input.append(op);
      });
    if (typeof value === "boolean") {
      input.type = "checkbox";
      input.checked = value;
    } else if (!choices) {
      input.type = "number";
      input.step = "any";
      input.value = value * (scale || 1);
    } else input.value = value;
    input.onchange = () => {
      draft[group][key] =
        typeof value === "boolean"
          ? input.checked
          : choices
            ? input.value
            : Number(input.value) / (scale || 1);
      if (key === "pyramiding_enabled" && input.checked) {
        Object.assign(draft.rules, {
          entry_method: "setup",
          pyramiding_review_enabled: true,
          initial_stop_reference: "entry_fill",
        });
        renderSettings();
      }
      $("settings-json").value = JSON.stringify(draft, null, 2);
    };
    l.append(input);
    root.append(l);
  });
  $("settings-json").value = JSON.stringify(draft, null, 2);
}
function choosePreset() {
  const p = boot.presets.find((x) => x.id === $("preset").value);
  draft = structuredClone({ rules: p.rules, limits: p.limits });
  renderSettings();
}
$("preset").onchange = choosePreset;
$("apply-json").onclick = () => {
  try {
    const p = JSON.parse($("settings-json").value);
    if (!p.rules || !p.limits) throw new Error("rules와 limits가 필요합니다.");
    draft = p;
    renderSettings();
    toast("편집 내용을 폼에 적용했습니다.");
  } catch (e) {
    toast(e.message, true);
  }
};
$("save-profile").onclick = async () => {
  try {
    const row = await api("/api/workspaces", {
      name: $("profile-name").value,
      capital: Number($("profile-capital").value),
      rules: JSON.parse($("settings-json").value).rules,
      limits: JSON.parse($("settings-json").value).limits,
    });
    await bootstrap(row.id);
    toast("별도 설정과 미시작 계좌를 저장했습니다.");
    nav("workflow");
    flow(2);
  } catch (e) {
    toast(e.message, true);
  }
};
