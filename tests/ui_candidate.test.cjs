const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

test("unknown candidate route names the server mismatch", async () => {
  const context = {
    fetch: async () => ({ status: 404 }),
    boot: { csrf: "test" },
  };
  runInNewContext(
    readFileSync(join(__dirname, "../trendfollow/web/src/api.js"), "utf8"),
    context,
  );
  await assert.rejects(
    context.api("/api/candidate/dates?workspace=test"),
    /\/api\/candidate\/dates.*서버 주소·포트/,
  );
});

test("frozen comparison account explains why a later candidate cannot be recalculated", () => {
  const analyze = { dataset: { action: "analyze" }, disabled: false };
  const marketAnalyze = { dataset: { action: "market_analyze" }, disabled: false };
  const controls = {
    "candidate-date": { value: "2026-11-13" },
    "candidate-analysis": marketAnalyze,
    "candidate-analysis-note": { hidden: true, textContent: "" },
    "market-action-note": { textContent: "" },
  };
  const context = {
    $: (id) => (controls[id] ||= {}),
    document: { querySelectorAll: (query) =>
      query.startsWith("#market [data-action") ? [marketAnalyze, analyze] : [] },
    snapshot: { account_as_of: "2026-11-10", workspace: { mode: "recalculation" } },
    candidateDates: { signal_dates: [], selection_dates: ["2026-11-13"] },
    currentJobs: [],
    loading: false,
    submittingAction: null,
  };
  runInNewContext(
    readFileSync(join(__dirname, "../trendfollow/web/src/workflow.js"), "utf8"),
    context,
  );
  context.renderMarketActions();
  assert.equal(analyze.disabled, true);
  assert.equal(marketAnalyze.disabled, false);
  assert.equal(controls["candidate-analysis-note"].hidden, false);
  assert.match(controls["candidate-analysis-note"].textContent, /11-13.*시장 후보 계산.*11-10/);
});

test("candidate stages distinguish confirmed close from live quote evidence", () => {
  const context = {
    $: () => ({}),
    quoteAvailability: (_run, _symbol, quote) =>
      quote ? "수신 정상" : "최근 체결 없음",
    snapshot: { rules: { entry_method: "setup", setup_52w_enabled: true } },
  };
  runInNewContext(
    readFileSync(
      join(__dirname, "../trendfollow/web/src/candidate.js"),
      "utf8",
    ),
    context,
  );
  const row = {
    symbol: "005930",
    high: 112,
    close: 108,
    setup_resistance: 110,
    atr: 3,
  };
  assert.equal(context.candidateStage(row, context.snapshot.rules), "faded");
  assert.equal(
    context.candidateStage({ ...row, high: 109 }, context.snapshot.rules),
    "near",
  );
  assert.equal(
    context.candidateStage(
      { ...row, high: 114, close: 112, setup_trigger: true },
      context.snapshot.rules,
    ),
    "breakout",
  );
  const run = {
    targets: ["005930"],
    levels: { "005930": { setup_resistance: 110 } },
    summary: {
      latest_target_quotes: { "005930": { price: 112 } },
      touched_targets: ["005930"],
    },
  };
  assert.equal(context.liveCandidateStage(row, run).stage, "breakout");
  run.summary.latest_target_quotes["005930"].price = 108;
  assert.equal(context.liveCandidateStage(row, run).stage, "faded");
  delete run.summary.latest_target_quotes["005930"];
  assert.equal(context.liveCandidateStage(row, run).reason, "최근 체결 없음");
});

test("52-week gap uses a fresh live quote with the live resistance, then falls back to close", () => {
  const context = {
    $: () => ({}),
    watchData: null,
    candidateView: { as_of: "2026-11-13" },
    quoteAvailability: () => "수신 정상",
    number: (value, digits) => Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits }),
  };
  runInNewContext(readFileSync(join(__dirname, "../trendfollow/web/src/candidate.js"), "utf8"), context);
  const row = { symbol: "005930", close: 99, high: 105, prior_52w_high: 100 };
  let gap = context.high52Gap(row);
  assert.equal(gap.source, "종가");
  assert.ok(Math.abs(gap.percent + 1) < 1e-10);
  assert.equal(context.high52GapText(gap), "1% 남음");
  const run = {
    as_of: "2026-11-13", session: "2026-11-14", targets: ["005930"],
    levels: { "005930": { high52_resistance: 105 } },
    summary: { status: "running", latest_target_quotes: { "005930": { price: 106 } } },
  };
  context.watchData = { today: "2026-11-14", runs: [run] };
  gap = context.high52Gap(row);
  assert.equal(gap.source, "현재가 · 잠정");
  assert.ok(gap.percent > 0 && gap.percent < 1);
  assert.match(context.high52GapText(gap), /돌파$/);
  context.quoteAvailability = () => "시세 지연";
  assert.ok(Math.abs(context.high52Gap(row).percent + 1) < 1e-10);
  context.quoteAvailability = () => "수신 정상";
  delete run.levels["005930"];
  assert.equal(context.high52Gap(row).source, "종가");
  run.summary.status = "completed";
  assert.equal(context.high52Gap(row).source, "종가");
  assert.equal(context.high52Gap({ ...row, prior_52w_high: null }), null);
});

test("market action uses its selected candidate date", () => {
  const context = {
    $: (id) => ({
      value: id === "candidate-date" ? "2026-11-10" : "2026-11-13",
    }),
    document: { querySelectorAll: () => [] },
  };
  runInNewContext(
    readFileSync(join(__dirname, "../trendfollow/web/src/jobs.js"), "utf8"),
    context,
  );
  assert.equal(context.actionDate({ closest: () => ({}) }), "2026-11-10");
  assert.equal(context.actionDate({ closest: () => null }), "2026-11-13");
});

test("KRX historical source can fill missing dates after the session day", () => {
  const controls = {
    "candidate-source": { value: "krx_open_api" },
    "candidate-krx-start": { value: "2026-11-10" },
    "candidate-date": {},
    "candidate-source-account": {},
  };
  const context = {
    $: (id) => controls[id] || {},
    candidateDates: {
      reference_dates: ["2026-11-13"],
      selection_dates: [],
      capture_as_of: "2026-11-13",
      capture_available: false,
      candidate_sources: ["kis", "krx_open_api", "pykrx"],
    },
  };
  runInNewContext(
    readFileSync(
      join(__dirname, "../trendfollow/web/src/candidate.js"),
      "utf8",
    ),
    context,
  );
  assert.equal(context.candidateCaptureEnabled(), true);
  context.candidateDates.candidate_sources = ["kis"];
  assert.equal(context.candidateCaptureEnabled(), false);
  context.candidateDates.candidate_sources = ["kis", "krx_open_api"];
  controls["candidate-source"].value = "pykrx";
  assert.equal(context.candidateCaptureEnabled(), false);
  context.candidateDates.candidate_sources.push("pykrx");
  assert.equal(context.candidateCaptureEnabled(), true);
  controls["candidate-source"].value = "kis";
  assert.equal(context.candidateCaptureEnabled(), false);
});
