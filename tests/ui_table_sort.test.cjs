const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this._text = "";
  }
  set textContent(value) {
    this.children = [];
    this._text = String(value);
  }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node.parent) node.parent.children = node.parent.children.filter((child) => child !== node);
      node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this._text = "";
    this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  get rows() { return this.children.filter((child) => child.tag === "tr"); }
  get cells() { return this.children.filter((child) => child.tag === "td" || child.tag === "th"); }
}

test("every table header cycles ascending, descending, and original order", () => {
  const root = new FakeNode("div");
  const context = {
    $: () => root,
    el: (tag, value) => {
      const node = new FakeNode(tag);
      if (value !== undefined) node.textContent = value;
      return node;
    },
    Node: FakeNode,
    document: { querySelectorAll: () => [] },
  };
  runInNewContext(readFileSync(join(__dirname, "../trendfollow/web/src/dom.js"), "utf8"), context);
  const columns = [["종목", (row) => row.name], ["가격", (row) => row.price]];
  const rows = [
    { name: "가", price: "900원" },
    { name: "다", price: "1,200원" },
    { name: "나", price: "10원" },
  ];
  const names = () => root.children[0].children[1].rows.map((row) => row.cells[0].textContent);
  const header = (index) => root.children[0].children[0].rows[0].cells[index];
  context.table("sample", columns, rows);
  assert.deepEqual(names(), ["가", "다", "나"]);
  assert.equal(header(0).children[0].tag, "button");
  header(1).children[0].onclick();
  assert.deepEqual(names(), ["나", "가", "다"]);
  assert.equal(header(1).attributes["aria-sort"], "ascending");
  header(1).children[0].onclick();
  assert.deepEqual(names(), ["다", "가", "나"]);
  assert.equal(header(1).attributes["aria-sort"], "descending");
  header(1).children[0].onclick();
  assert.deepEqual(names(), ["가", "다", "나"]);
  assert.equal(header(1).attributes["aria-sort"], "none");
  header(0).children[0].onclick();
  assert.deepEqual(names(), ["가", "나", "다"]);
  context.table("sample", columns, [rows[1], rows[2], rows[0]]);
  assert.deepEqual(names(), ["가", "나", "다"]);
  header(0).children[0].onclick();
  header(0).children[0].onclick();
  assert.deepEqual(names(), ["다", "나", "가"]);
});

test("table sort parses money units and keeps missing values last", () => {
  const context = { document: { querySelectorAll: () => [] } };
  runInNewContext(readFileSync(join(__dirname, "../trendfollow/web/src/dom.js"), "utf8"), context);
  assert.equal(context.tableSortValue("24억원가량"), 2_400_000_000);
  assert.equal(context.tableSortValue("1,200원"), 1200);
  assert.equal(context.tableSortValue("—"), null);
  assert.equal(context.tableSortDirection("none", true), "ascending");
  assert.equal(context.tableSortDirection("ascending", true), "descending");
  assert.equal(context.tableSortDirection("descending", true), "none");
});

test("display text can sort by a separate signed percentage", () => {
  const root = new FakeNode("div");
  const context = {
    $: () => root,
    el: (tag, value) => {
      const node = new FakeNode(tag);
      if (value !== undefined) node.textContent = value;
      return node;
    },
    Node: FakeNode,
    document: { querySelectorAll: () => [] },
  };
  runInNewContext(readFileSync(join(__dirname, "../trendfollow/web/src/dom.js"), "utf8"), context);
  context.table("stock-table", [
    ["종목", (row) => row.symbol],
    ["52주 신고가", (row) => row.label, { sortValue: (row) => row.gap }],
  ], [
    { symbol: "A", label: "2% 남음 종가", gap: -2 },
    { symbol: "B", label: "1% 돌파 현재가", gap: 1 },
    { symbol: "C", label: "자료 부족", gap: null },
  ]);
  const names = () => root.children[0].children[1].rows.map((row) => row.cells[0].textContent);
  const header = root.children[0].children[0].rows[0].cells[1].children[0];
  header.onclick();
  assert.deepEqual(names(), ["A", "B", "C"]);
  header.onclick();
  assert.deepEqual(names(), ["B", "A", "C"]);
});

test("a non-sortable price comparison column has no sort control", () => {
  const root = new FakeNode("div");
  root.dataset.sortColumn = "1";
  root.dataset.sortDirection = "ascending";
  const context = {
    $: () => root,
    el: (tag, value) => {
      const node = new FakeNode(tag);
      if (value !== undefined) node.textContent = value;
      return node;
    },
    Node: FakeNode,
    document: { querySelectorAll: () => [] },
  };
  runInNewContext(readFileSync(join(__dirname, "../trendfollow/web/src/dom.js"), "utf8"), context);
  context.table("stock-table", [
    ["종목", (row) => row.symbol],
    ["종가 / 52주 고가", (row) => row.prices, { sortable: false }],
  ], [
    { symbol: "005930", prices: "208,000 / 250,000" },
    { symbol: "000660", prices: "100,000 / 120,000" },
  ]);
  const table = root.children[0];
  const header = table.children[0].rows[0].cells;
  assert.equal(header[1].textContent, "종가 / 52주 고가");
  assert.equal(header[1].children.length, 0);
  assert.equal(header[0].children[0].tag, "button");
  assert.equal(root.dataset.sortDirection, "none");
  assert.deepEqual(table.children[1].rows.map((row) => row.cells[0].textContent), ["005930", "000660"]);
});
