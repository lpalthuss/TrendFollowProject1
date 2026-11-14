const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  filterRows,
  figureSelection,
  setupFilterConflict,
  candidateConflict,
} = require("../trendfollow/web/app.js");
const defaults = {
  industry: "all",
  rs: "all",
  action: "all",
  setupPhase: "all",
  setup: "all",
  high52: "all",
  signal: "all",
  volume: "all",
  search: "",
};
const rules = { entry_method: "setup", volume_multiple: 1.5 };
const rows = [
  {
    symbol: "000001",
    name: "후보 A",
    sector: "A",
    industry_name: "반도체",
    rs_pass: true,
    sector_action: true,
    setup_phase: "base",
    setup_ready: true,
    prior_52w_high: 100,
    high52_breakout: true,
    signal: false,
    volume_ratio: 1.5,
  },
  {
    symbol: "000002",
    name: "후보 B",
    sector: "A",
    industry_name: "반도체",
    rs_pass: true,
    sector_action: false,
    setup_phase: "base",
    setup_ready: false,
    prior_52w_high: 100,
    high52_breakout: false,
    signal: false,
    volume_ratio: 1.49,
  },
  {
    symbol: "000003",
    name: "후보 C",
    sector: "B",
    industry_name: "기계",
    rs_pass: false,
    sector_action: true,
    setup_phase: "rising",
    setup_ready: false,
    setup_trigger: true,
    prior_52w_high: 100,
    high52_breakout: false,
    signal: true,
    volume_ratio: 2,
  },
  {
    symbol: "000004",
    sector: "B",
    rs_pass: null,
    sector_action: null,
    signal: null,
    volume_ratio: null,
  },
];
const select = (filters = {}, config = rules) =>
  filterRows(rows, { ...defaults, ...filters }, config).map((r) => r.symbol);
test("industry, RS, action, setup, volume and search compose with AND", () => {
  assert.deepEqual(
    select({
      industry: "A",
      rs: "pass",
      action: "pass",
      setup: "ready",
      signal: "fail",
      volume: "pass",
      search: "반도체",
    }),
    ["000001"],
  );
  assert.deepEqual(select({ industry: "A", signal: "pass" }), []);
  assert.deepEqual(
    select({ industry: "B", search: "후보 C", setup: "trigger" }),
    ["000003"],
  );
});
test("unknown is separate from failed and unapplied industry action", () => {
  assert.deepEqual(select({ rs: "unknown" }), ["000004"]);
  assert.deepEqual(select({ rs: "fail" }), ["000003"]);
  assert.deepEqual(select({ action: "unknown" }), ["000004"]);
  assert.deepEqual(select({ action: "unapplied" }), []);
  assert.deepEqual(
    select({ action: "unapplied" }, { ...rules, entry_method: "breakout" }),
    rows.map((r) => r.symbol),
  );
  assert.deepEqual(
    select({ action: "unknown" }, { ...rules, entry_method: "breakout" }),
    [],
  );
});
test("setup phase is distinct from readiness and trigger flags", () => {
  assert.deepEqual(select({ setupPhase: "base" }), ["000001", "000002"]);
  assert.deepEqual(select({ setup: "ready" }), ["000001"]);
  assert.deepEqual(select({ setup: "not_ready" }), ["000002", "000003"]);
  assert.deepEqual(select({ setup: "unknown" }), ["000004"]);
  assert.deepEqual(select({ setupPhase: "base", setup: "ready" }), ["000001"]);
  assert.deepEqual(select({ setupPhase: "rising", setup: "trigger" }), [
    "000003",
  ]);
  assert.deepEqual(select({ setupPhase: "base", setup: "trigger" }), []);
});
test("the UI explains only structurally impossible setup filter pairs", () => {
  assert.match(
    setupFilterConflict("base", "trigger"),
    /함께 만족할 수 없습니다/,
  );
  assert.match(
    setupFilterConflict("base", "invalidated"),
    /함께 만족할 수 없습니다/,
  );
  assert.match(
    setupFilterConflict("untradable", "ready"),
    /함께 만족할 수 없습니다/,
  );
  assert.equal(setupFilterConflict("rising", "trigger"), null);
  assert.equal(setupFilterConflict("base", "ready"), null);
});
test("candidate choices disable only combinations that cannot produce a result", () => {
  assert.match(
    candidateConflict(
      { ...defaults, setupPhase: "base", setup: "trigger" },
      rules,
    ).message,
    /함께 만족할 수 없습니다/,
  );
  assert.match(
    candidateConflict({ ...defaults, signal: "pass", volume: "fail" }, rules)
      .message,
    /거래량 조건 통과/,
  );
  assert.match(
    candidateConflict({ ...defaults, signal: "pass", rs: "unknown" }, rules)
      .message,
    /RS 통과/,
  );
  assert.equal(
    candidateConflict({ ...defaults, setup: "trigger", high52: "pass" }, rules),
    null,
  );
  assert.equal(
    candidateConflict(
      { ...defaults, setupPhase: "base", high52: "pass" },
      rules,
    ),
    null,
  );
  assert.equal(
    candidateConflict(
      { ...defaults, signal: "pass", setup: "not_ready" },
      { ...rules, setup_52w_enabled: true },
    ),
    null,
  );
});
test("selecting a new phase in the figure clears a previous trigger filter", () => {
  assert.deepEqual(figureSelection("all", "all", "condition", "trigger"), {
    phase: "all",
    condition: "trigger",
  });
  assert.deepEqual(figureSelection("all", "trigger", "phase", "base"), {
    phase: "base",
    condition: "all",
  });
  assert.deepEqual(figureSelection("base", "all", "phase", "base"), {
    phase: "all",
    condition: "all",
  });
  assert.deepEqual(figureSelection("base", "ready", "phase", "all"), {
    phase: "all",
    condition: "all",
  });
});
test("volume uses configured threshold inclusively and does not coerce null to zero", () => {
  assert.deepEqual(select({ volume: "pass" }), ["000001", "000003"]);
  assert.deepEqual(select({ volume: "fail" }), ["000002"]);
  assert.deepEqual(select({ volume: "unknown" }), ["000004"]);
  assert.deepEqual(
    select({ volume: "pass" }, { ...rules, volume_multiple: 2 }),
    ["000003"],
  );
});
test("52-week breakout filter separates a breakout from missing history", () => {
  assert.deepEqual(select({ high52: "pass" }), ["000001"]);
  assert.deepEqual(select({ high52: "fail" }), ["000002", "000003"]);
  assert.deepEqual(select({ high52: "unknown" }), ["000004"]);
});
test("all conditions cleared returns every row without mutating inputs", () => {
  const before = JSON.stringify(rows);
  assert.deepEqual(
    select(),
    rows.map((r) => r.symbol),
  );
  assert.equal(JSON.stringify(rows), before);
});
