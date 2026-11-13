const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

test("watch preparation and start require the previous KRX session close", async () => {
  const nodes = new Map();
  const get = (id) => {
    if (!nodes.has(id)) nodes.set(id, { value: "" });
    return nodes.get(id);
  };
  const requests = [];
  const previews = [];
  let count = 165;
  let candidatesReady = false;
  let industryReady = false;
  const context = {
    $: get,
    URLSearchParams,
    loadSequence: 1,
    clearTimeout: () => {},
    setTimeout: (callback) => previews.push(callback),
    api: async (url) =>
      url.startsWith("/api/watch/basis?")
        ? {
            as_of: "2026-11-12",
            candidates_ready: candidatesReady,
            industry_ready: industryReady,
          }
        : { count, industries: [] },
    job: (action, payload) => requests.push({ action, ...payload }),
    workspace: "setup-rs",
    snapshot: {
      workspace: { id: "setup-rs" },
      rules: { entry_method: "setup" },
      rows: [{ symbol: "068930" }],
      as_of: "2026-11-10",
      account_as_of: "2026-11-10",
    },
    guidance: { as_of: "2026-11-12", processed: false },
    currentJobs: [],
    loading: false,
    watchData: {
      control: { active_or_unconfirmed: [] },
      today: "2026-11-13",
      today_as_of: "2026-11-12",
      regular_session: true,
      plans: [],
      runs: [],
    },
  };
  runInNewContext(
    readFileSync(
      join(__dirname, "../trendfollow/web/src/intraday.js"),
      "utf8",
    ) +
      "\nglobalThis.watchDecisionEventsForTest = watchDecisionEvents; globalThis.watchTimestampForTest = watchTimestamp;",
    context,
  );
  assert.equal(
    context.watchTimestampForTest("2026-11-13T17:40:25+09:00"),
    "2026-11-13 17:40:25",
  );
  assert.equal(
    context.watchTimestampForTest("2026-11-13T17:40:25.123456+09:00"),
    "2026-11-13 17:40:25",
  );
  const initial = {
    kind: "signal_state",
    breakout_present: null,
    price_basis_mismatch: false,
  };
  const mismatch = {
    kind: "signal_state",
    breakout_present: null,
    price_basis_mismatch: true,
  };
  const evaluated = { kind: "signal_state", breakout_present: false };
  assert.deepEqual(
    Array.from(
      context.watchDecisionEventsForTest([initial, mismatch, evaluated]),
    ),
    [evaluated],
  );
  get("watch-symbols").value = "068930";
  get("watch-session").value = "2026-11-13";
  get("watch-mode").value = "industry";
  get("watch-session").oninput();
  await new Promise(setImmediate);
  assert.equal(get("prepare-watch").disabled, true);
  assert.match(
    get("flow-watch-status").textContent,
    /산업 구성 자료가 없습니다/,
  );
  get("watch-mode").value = "symbols";
  get("watch-mode").onchange();
  assert.equal(get("prepare-watch").disabled, false);
  assert.match(get("flow-watch-status").textContent, /후보를 계산/);
  get("prepare-watch").onclick();
  assert.equal(requests.shift().as_of, "2026-11-12");
  candidatesReady = true;
  industryReady = true;
  get("watch-mode").value = "industry";
  context.loadSequence++;
  get("watch-session").oninput();
  await new Promise(setImmediate);
  assert.equal(get("prepare-watch").disabled, false);
  await previews.shift()();
  assert.equal(get("prepare-watch").disabled, false);
  assert.match(get("watch-subscriptions").textContent, /165/);
  get("prepare-watch").onclick();
  assert.deepEqual(Array.from(requests[0].symbols), ["068930"]);
  context.currentJobs = [
    {
      workspace: "setup-rs",
      action: "prepare_intraday",
      as_of: "2026-11-12",
      status: "failed",
      error: "전체 산업 동료 포함 구독 165개가 KIS 한도 40개를 넘습니다.",
    },
  ];
  get("watch-session").oninput();
  assert.match(
    get("flow-watch-status").textContent,
    /최근 계획 생성 실패.*165/,
  );
  count = 8;
  get("watch-symbols").value = "001120";
  get("watch-mode").value = "symbols";
  get("watch-mode").onchange();
  await previews.shift()();
  assert.equal(get("prepare-watch").disabled, false);
  assert.equal(get("start-watch").disabled, true);
  get("prepare-watch").onclick();
  assert.equal(requests[1].action, "prepare_intraday");
  assert.equal(requests[1].as_of, "2026-11-12");
  assert.equal(requests[1].session, "2026-11-13");
  assert.equal(requests[1].watch_mode, "symbols");

  const plan = { id: "plan", session: "2026-11-12" };
  context.watchData.plans.push(plan);
  get("watch-plan").value = plan.id;
  get("watch-plan").onchange();
  assert.equal(get("start-watch").disabled, true);
  assert.match(get("watch-start-status").textContent, /2026-11-12/);
  plan.session = "2026-11-13";
  context.watchData.regular_session = false;
  get("watch-plan").onchange();
  assert.equal(get("start-watch").disabled, true);
  context.watchData.regular_session = true;
  get("watch-plan").onchange();
  assert.equal(get("start-watch").disabled, true);
  plan.as_of = "2026-11-12";
  get("watch-plan").onchange();
  assert.equal(get("start-watch").disabled, false);
});
