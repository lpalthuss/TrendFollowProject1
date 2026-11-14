"use strict";
(function (root) {
  const matches = (value, choice) =>
    choice === "all" ||
    (choice === "pass"
      ? value === true
      : choice === "fail"
        ? value === false
        : value !== true && value !== false);
  const phaseLabels = {
    seeking: "상승 파동 탐색",
    rising: "상승 파동 진행",
    base: "조정·베이스",
    untradable: "거래량 0 · 셋업 계산 불가",
  };
  function figureSelection(currentPhase, currentCondition, type, choice) {
    if (choice === "all") return { phase: "all", condition: "all" };
    if (type === "condition")
      return {
        phase: "all",
        condition:
          currentCondition === choice && currentPhase === "all"
            ? "all"
            : choice,
      };
    return {
      phase: currentPhase === choice ? "all" : choice,
      condition: "all",
    };
  }
  function setupFilterConflict(phase, condition) {
    if (phase === "all" || condition === "all") return null;
    if (condition === "unknown")
      return "파동 단계를 선택하면서 셋업 자료 없음도 선택할 수 없습니다.";
    if (phase === "base" && condition === "trigger")
      return "셋업 돌파 당일에는 파동 단계가 조정·베이스에서 벗어나므로 두 조건을 함께 만족할 수 없습니다.";
    if ((phase === "base" || phase === "rising") && condition === "invalidated")
      return "셋업 무효화 당일에는 파동 단계가 상승 파동 탐색으로 돌아가므로 두 조건을 함께 만족할 수 없습니다.";
    if (
      phase === "untradable" &&
      ["ready", "trigger", "invalidated"].includes(condition)
    )
      return "거래량이 0인 날에는 준비·돌파·무효화를 계산하지 않으므로 두 조건을 함께 만족할 수 없습니다.";
    return null;
  }
  function candidateConflict(filters, rules) {
    const pair = (message, first, second) => ({
      message,
      fields: [first, second],
    });
    const setupAccount = rules.entry_method === "setup";
    if (!setupAccount) {
      if (filters.setupPhase !== "all")
        return {
          message: "파동 단계는 셋업 계좌에서만 적용됩니다.",
          fields: ["setupPhase"],
        };
      if (!["all", "unknown"].includes(filters.setup))
        return {
          message: "셋업 조건은 셋업 계좌에서만 적용됩니다.",
          fields: ["setup"],
        };
      if (!["all", "unapplied"].includes(filters.action))
        return {
          message: "산업 액션은 셋업 계좌에서만 적용됩니다.",
          fields: ["action"],
        };
    } else if (filters.action === "unapplied") {
      return {
        message: "셋업 계좌에는 산업 액션이 적용됩니다.",
        fields: ["action"],
      };
    }
    const setupConflict = setupFilterConflict(
      filters.setupPhase,
      filters.setup,
    );
    if (setupConflict) return pair(setupConflict, "setupPhase", "setup");
    if (filters.signal === "pass") {
      if (["fail", "unknown"].includes(filters.rs))
        return pair("최종 신호 통과에는 RS 통과가 필요합니다.", "signal", "rs");
      if (["fail", "unknown"].includes(filters.volume))
        return pair(
          "최종 신호 통과에는 거래량 조건 통과가 필요합니다.",
          "signal",
          "volume",
        );
      if (
        setupAccount &&
        rules.sector_require_action &&
        ["fail", "unknown"].includes(filters.action)
      )
        return pair(
          "현재 계좌의 최종 신호에는 산업 액션 통과가 필요합니다.",
          "signal",
          "action",
        );
      if (setupAccount && filters.setupPhase === "untradable")
        return pair(
          "거래량 0인 날에는 최종 진입 신호가 발생하지 않습니다.",
          "signal",
          "setupPhase",
        );
      if (setupAccount && !rules.setup_52w_enabled) {
        if (["not_ready", "invalidated", "unknown"].includes(filters.setup))
          return pair(
            "현재 계좌의 최종 신호에는 유효한 셋업 준비가 필요합니다.",
            "signal",
            "setup",
          );
        if (["base", "seeking"].includes(filters.setupPhase))
          return pair(
            "현재 계좌의 셋업 신호는 돌파 당일 조정·베이스나 파동 탐색 단계에 머물지 않습니다.",
            "signal",
            "setupPhase",
          );
      }
      if (
        !setupAccount &&
        rules.breakout_days === 252 &&
        ["fail", "unknown"].includes(filters.high52)
      )
        return pair(
          "252일 고가 돌파 계좌의 최종 신호에는 52주 신고가 돌파가 필요합니다.",
          "signal",
          "high52",
        );
    }
    return null;
  }
  function filterRows(rows, filters, rules) {
    const search = (filters.search || "").trim().toLocaleLowerCase();
    return rows.filter((row) => {
      if (filters.industry !== "all" && row.sector !== filters.industry)
        return false;
      if (
        ![row.name, row.symbol, row.sector, row.industry_name]
          .join(" ")
          .toLocaleLowerCase()
          .includes(search)
      )
        return false;
      if (
        !matches(row.rs_pass, filters.rs) ||
        !matches(row.signal, filters.signal)
      )
        return false;
      const applied = rules.entry_method === "setup";
      const phase = filters.setupPhase || "all";
      if (phase !== "all" && (!applied || row.setup_phase !== phase))
        return false;
      if (filters.action === "unapplied") {
        if (applied) return false;
      } else if (
        filters.action !== "all" &&
        (!applied || !matches(row.sector_action, filters.action))
      )
        return false;
      const setup = filters.setup;
      if (setup !== "all") {
        if (setup === "unknown") {
          if (applied && row.setup_phase) return false;
        } else if (!applied) return false;
        else if (setup === "ready" || setup === "not_ready") {
          if (row.setup_ready !== (setup === "ready")) return false;
        } else if (setup === "trigger" || setup === "invalidated") {
          if (row["setup_" + setup] !== true) return false;
        } else if (row.setup_phase !== setup) return false;
      }
      const volume =
        typeof row.volume_ratio === "number" &&
        Number.isFinite(row.volume_ratio)
          ? row.volume_ratio >= rules.volume_multiple
          : null;
      const high52 =
        typeof row.prior_52w_high === "number" &&
        Number.isFinite(row.prior_52w_high)
          ? row.high52_breakout
          : null;
      return (
        matches(volume, filters.volume) &&
        matches(high52, filters.high52 || "all")
      );
    });
  }
  const api = {
    filterRows,
    phaseLabels,
    figureSelection,
    setupFilterConflict,
    candidateConflict,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") root.TrendFollowFilters = api;
})(globalThis);
