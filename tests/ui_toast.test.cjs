const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

test("errors remain visible until dismissed and announce themselves as alerts", () => {
  const timers = [];
  const attributes = {};
  const root = {
    hidden: true,
    replaceChildren(...items) {
      this.children = items;
    },
    setAttribute(name, value) {
      attributes[name] = value;
    },
  };
  const context = {
    document: { querySelectorAll: () => [] },
    $: () => root,
    el: (tag, label, className) => ({
      tag,
      label,
      className,
      setAttribute() {},
    }),
    clearTimeout() {},
    setTimeout(_callback, duration) {
      timers.push(duration);
      return 1;
    },
  };
  runInNewContext(
    readFileSync(join(__dirname, "../trendfollow/web/src/dom.js"), "utf8"),
    context,
  );
  context.toast("가격 수집 실패", true);
  assert.equal(root.hidden, false);
  assert.equal(root.className, "error");
  assert.equal(attributes.role, "alert");
  assert.equal(root.children[0].label, "실행 실패");
  assert.equal(timers.length, 0);
  root.children[2].onclick();
  assert.equal(root.hidden, true);

  context.toast("작업 접수");
  assert.equal(root.className, "success");
  assert.equal(attributes.role, "status");
  assert.equal(timers[0], 10000);
});
