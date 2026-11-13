const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

test("active job shows its phase, elapsed time and measured progress", () => {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {
        hidden: true,
        dataset: {},
        classList: {
          add: (value) => classes.add(value),
          remove: (value) => classes.delete(value),
          contains: (value) => classes.has(value),
        },
        removeAttribute(name) {
          if (name === "value") delete this.value;
          if (name === "data-progress-label") delete this.dataset.progressLabel;
        },
      });
    }
    return nodes.get(id);
  };
  const button = node("flow-daily");
  const context = {
    $: node,
    document: {
      querySelectorAll: (selector) =>
        selector === '[data-action="daily"]' ||
        (selector === ".job-active-button" && button.classList.contains("job-active-button"))
          ? [button]
          : [],
    },
    currentJobs: [{
      workspace: "account",
      action: "daily",
      status: "running",
      progress: "가격 자료 수집 중",
      progress_current: 25,
      progress_total: 100,
      created_at: new Date(Date.now() - 65000).toISOString(),
    }],
    workspace: "account",
    submittingAction: null,
    labels: { daily: "데이터 갱신·일별 처리" },
    number: (value) => String(value),
  };
  runInNewContext(
    readFileSync(join(__dirname, "../trendfollow/web/src/jobs.js"), "utf8") +
      "\nglobalThis.renderJobProgressForTest = renderJobProgress;",
    context,
  );
  context.renderJobProgressForTest();
  assert.equal(node("job-progress").hidden, false);
  assert.equal(node("job-progress-phase").textContent, "가격 자료 수집 중");
  assert.equal(node("job-progress-bar").value, 25);
  assert.match(node("job-progress-count").textContent, /25 \/ 100 · 25%/);
  assert.equal(button.classList.contains("job-active-button"), true);
  context.currentJobs[0].status = "completed";
  context.renderJobProgressForTest();
  assert.equal(node("job-progress").hidden, true);
  assert.equal(button.classList.contains("job-active-button"), false);
});

test("daily buttons submit the globally selected price source", async () => {
  const nodes = new Map();
  const get = (id) => {
    if (!nodes.has(id))
      nodes.set(id, { value: "", hidden: true, textContent: "", dataset: {},
        classList: { add() {}, remove() {} }, removeAttribute() {} });
    return nodes.get(id);
  };
  get("asof").value = "2026-11-13";
  get("price-source").value = "pykrx";
  const calls = [];
  const context = {
    $: get,
    document: { querySelectorAll: () => [] },
    workspace: "account",
    currentJobs: [],
    submittingAction: null,
    labels: { daily: "데이터 갱신·일별 처리" },
    number: String,
    api: async (_path, payload) => {
      calls.push(payload);
      return { id: "job-1", workspace: "account", action: "daily",
        status: "queued", created_at: new Date().toISOString() };
    },
    toast() {},
    poll: async () => {},
  };
  runInNewContext(
    readFileSync(join(__dirname, "../trendfollow/web/src/jobs.js"), "utf8") +
      "\nglobalThis.jobForTest = job;",
    context,
  );
  await context.jobForTest("daily");
  assert.equal(calls[0].price_source, "pykrx");
});
