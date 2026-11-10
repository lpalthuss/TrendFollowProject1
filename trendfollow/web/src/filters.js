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
    untradable: "가격 이상·매매 불가",
  };
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
      return matches(volume, filters.volume);
    });
  }
  const api = { filterRows, phaseLabels };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") root.TrendFollowFilters = api;
})(globalThis);
