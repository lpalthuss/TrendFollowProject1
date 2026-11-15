const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

test("account menu shows account type and date, and hides archived accounts by default", async () => {
  const source = readFileSync(
    join(__dirname, "../trendfollow/web/src/bootstrap.js"),
    "utf8",
  ).split('$("workspace").onchange')[0];
  const controls = {
    workspace: {
      options: [],
      replaceChildren() {
        this.options = [];
      },
      append(option) {
        this.options.push(option);
      },
    },
    "show-archived": { checked: false },
    "toggle-account-archive": {},
    "rename-account": {},
    "rename-account-name": {},
    "price-source": { value: "naver" },
    "price-source-help": {},
    "candidate-date": { replaceChildren() {} },
    preset: {
      replaceChildren() {},
      append() {},
    },
  };
  const accounts = [
    { id: "baseline", name: "기본", builtin: true, account_as_of: null },
    { id: "live", name: "운용", account_as_of: "2026-11-13" },
    {
      id: "frozen",
      name: "비교",
      mode: "recalculation",
      account_as_of: "2026-11-10",
      archived_at: "2026-11-14T10:00:00+09:00",
    },
  ];
  const context = {
    $: (id) => controls[id],
    el: (_tag, label) => ({ textContent: label }),
    api: async () => ({
      workspaces: accounts,
      presets: [{ id: "preset", name: "예제" }],
    }),
    localStorage: { getItem: () => null, setItem() {} },
    load: async () => {},
    renderWatch: async () => {},
    choosePreset() {},
    workspace: null,
  };
  runInNewContext(source, context);
  await context.bootstrap();
  assert.equal(controls.workspace.options.length, 2);
  assert.equal(
    controls.workspace.options[1].textContent,
    "운용 · 일반 · 2026-11-13",
  );
  assert.equal(context.workspace, "live");
  controls["show-archived"].checked = true;
  await context.bootstrap("frozen");
  assert.equal(controls.workspace.options.length, 3);
  assert.equal(
    controls.workspace.options[2].textContent,
    "비교 · 정정 비교 · 2026-11-10 · 보관",
  );
  assert.equal(
    controls["toggle-account-archive"].textContent,
    "이 계좌 보관 해제",
  );
  assert.equal(controls["rename-account-name"].value, "비교");
  assert.equal(controls["rename-account"].hidden, false);
});
