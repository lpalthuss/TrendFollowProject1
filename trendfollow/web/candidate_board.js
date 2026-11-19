export default function ({
  parentElement,
  data,
  setStateValue,
}) {
  const board = parentElement.querySelector(".candidate-board");
  const find = (s) => board.querySelector(s);
  const { filterRows, figureSelection, phaseLabels, candidateConflict } =
    globalThis.TrendFollowFilters;
  const defaults = {
    industry: "all",
    rs: "all",
    signal: "all",
    action: "all",
    volume: "all",
    high52: "all",
    setupPhase: "all",
    setup: "all",
    search: "",
  };
  const view = (board.view ??= {
    filters: { ...defaults },
    stage: "",
    symbol: "",
    sort: "",
    direction: 0,
  });
  const filters = view.filters;
  const rows = data.rows;
  const stages = data.stages;
  const all = ["all", "전체"];
  const tests = [
    all,
    ["pass", "통과"],
    ["fail", "미통과"],
    ["unknown", "미확인"],
  ];
  const setup = data.rules.entry_method === "setup";
  function element(tag, text) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    return node;
  }
  function options(select, items, field) {
    select.replaceChildren(
      ...items.map(([value, label]) => new Option(label, value)),
    );
    select.value = filters[field];
    select.onchange = () => {
      filters[field] = select.value;
      render();
    };
  }
  const industries = new Map();
  for (const r of rows) {
    const group = industries.get(r.sector) ?? {
      name: r.industry_name || "산업 미분류",
      count: 0,
      pass: 0,
    };
    group.count++;
    group.pass += Number(r.rs_pass === true);
    industries.set(r.sector, group);
  }
  const industryOptions = [...industries]
    .sort((a, b) => b[1].pass - a[1].pass || b[1].count - a[1].count)
    .map(([id, g]) => [id, `${g.name} · RS 통과 ${g.pass}/${g.count}`]);
  function controls() {
    options(
      find('[data-filter="industry"]'),
      [all, ...industryOptions],
      "industry",
    );
    const search = find("input");
    search.value = filters.search;
    search.oninput = () => {
      filters.search = search.value;
      render();
    };
    const advanced = find(".advanced");
    advanced.replaceChildren();
    const fields = [
      ["rs", "RS 조건", tests],
      ["signal", "최종 신호", tests],
      ["volume", "거래량 조건", tests],
      [
        "high52",
        "52주 신고가",
        [all, ["pass", "돌파"], ["fail", "미돌파"], ["unknown", "자료 부족"]],
      ],
    ];
    if (setup)
      fields.push(
        ["action", "산업 조건", tests],
        ["setupPhase", "파동 단계", [all, ...Object.entries(phaseLabels)]],
        [
          "setup",
          "셋업 조건",
          [
            all,
            ["ready", "준비 충족"],
            ["not_ready", "준비 미충족"],
            ["trigger", "셋업 돌파"],
            ["invalidated", "무효화"],
            ["unknown", "미확인"],
          ],
        ],
      );
    for (const [field, label, items] of fields) {
      const wrapper = element("label", label),
        select = element("select");
      select.dataset.filter = field;
      options(select, items, field);
      wrapper.append(select);
      advanced.append(wrapper);
    }
  }
  function selectSymbol(symbol) {
    if (view.symbol === symbol) return;
    view.symbol = symbol;
    setStateValue("symbol", symbol);
  }
  const stateLabel = (v) => (v == null ? "미확인" : v ? "통과" : "미통과");
  const number = (v, n = 1) =>
    v == null ? "—" : v.toLocaleString("ko-KR", { maximumFractionDigits: n });
  const columns = [
    ["name", "종목"],
    ["industry_name", "산업"],
    ["stage", "후보 상태"],
    ["reference_price", "종가 / 52주 고가"],
    ["gap", "52주 거리"],
    ["rs_percentile", "RS 순위"],
    ["mansfield_rs", "주간 Mansfield"],
    ["multi_rs", "다기간 Mansfield"],
    ["rs_pass", "RS 조건"],
    ["sector_action", "산업 조건"],
    ["setup_phase", "파동 단계"],
    ["setup_ready", "준비"],
    ["setup_trigger", "셋업 돌파"],
    ["volume_ratio", "거래량 배수"],
    ["signal", "최종 신호"],
    ["signal_entry_path", "신호 경로"],
  ];
  function render() {
    const figure = find("figure");
    if (figure) {
      figure.hidden = !setup;
      figure.dataset.phase = filters.setupPhase;
      figure.dataset.condition = filters.setup;
      for (const g of figure.querySelectorAll("[role=button]")) {
        const isPhase = !!g.dataset.phaseChoice,
          choice = g.dataset.phaseChoice || g.dataset.conditionChoice;
        g.setAttribute(
          "aria-pressed",
          String(
            isPhase
              ? choice === filters.setupPhase &&
                  (choice !== "all" || filters.setup === "all")
              : choice === filters.setup,
          ),
        );
        g.onclick = () => {
          const next = figureSelection(
            filters.setupPhase,
            filters.setup,
            isPhase ? "phase" : "condition",
            choice,
          );
          filters.setupPhase = next.phase;
          filters.setup = next.condition;
          controls();
          render();
        };
        g.onkeydown = (e) => {
          if (["Enter", " "].includes(e.key)) {
            e.preventDefault();
            g.onclick();
          }
        };
      }
    }
    const base = filterRows(rows, filters, data.rules);
    const cards = find(".cards");
    cards.replaceChildren();
    for (const [stage, description] of Object.entries(stages)) {
      const members = base.filter((r) => r.stage === stage);
      const card = element("section"),
        button = element("button", stage),
        chips = element("div");
      card.className = "card" + (view.stage === stage ? " selected" : "");
      button.setAttribute("aria-pressed", String(view.stage === stage));
      button.append(element("strong", members.length));
      button.onclick = () => {
        view.stage = view.stage === stage ? "" : stage;
        render();
      };
      chips.className = "chips";
      for (const r of members) {
        const chip = element("button", r.name || r.symbol);
        chip.onclick = () => {
          view.stage = stage;
          selectSymbol(r.symbol);
          render();
        };
        chips.append(chip);
      }
      card.append(button, element("small", description), chips);
      cards.append(card);
    }
    const visible = base.filter((r) => !view.stage || r.stage === view.stage);
    if (view.direction)
      visible.sort((a, b) => {
        const x = a[view.sort],
          y = b[view.sort];
        if (x == null || y == null)
          return Number(x == null) - Number(y == null);
        return (
          view.direction *
          (typeof x === "string" ? x.localeCompare(y, "ko") : x - y)
        );
      });
    if (view.symbol && !visible.some(r => r.symbol === view.symbol))
      selectSymbol("");
    find(".count").textContent =
      `${data.day} 종가 기준 · 전체 ${rows.length}종목`;
    find(".summary").textContent = [
      candidateConflict(filters, data.rules)?.message,
      view.stage,
      `선별 종목 ${visible.length}개`,
    ]
      .filter(Boolean)
      .join(" · ");
    const header = element("tr");
    for (const [field, label] of columns) {
      const th = element("th", field === "reference_price" ? label : null);
      if (field !== "reference_price") {
        const active = view.sort === field && view.direction;
        const button = element(
          "button",
          label + (active ? (view.direction === 1 ? " ↑" : " ↓") : ""),
        );
        button.onclick = () => {
          view.direction =
            view.sort === field ? { 0: 1, 1: -1, "-1": 0 }[view.direction] : 1;
          view.sort = field;
          render();
        };
        th.append(button);
      }
      header.append(th);
    }
    find("thead").replaceChildren(header);
    const body = document.createDocumentFragment();
    for (const r of visible) {
      const tr = element("tr");
      tr.dataset.symbol = r.symbol;
      tr.classList.toggle("current", r.symbol === view.symbol);
      tr.onclick = () => {
        selectSymbol(r.symbol);
        render();
      };
      const values = [
        r.name || r.symbol,
        r.industry_name,
        r.stage,
        number(r.reference_price) + " / " + number(r.prior_52w_high),
        r.gap == null
          ? "자료 부족"
          : r.gap === 0
            ? "고가 일치"
            : number(Math.abs(r.gap)) + "% " + (r.gap > 0 ? "돌파" : "남음"),
        number(r.rs_percentile),
        number(r.mansfield_rs, 2),
        number(r.multi_rs, 2),
        stateLabel(r.rs_pass),
        stateLabel(r.sector_action),
        phaseLabels[r.setup_phase] || "—",
        stateLabel(r.setup_ready),
        stateLabel(r.setup_trigger),
        r.volume_ratio == null ? "—" : number(r.volume_ratio, 2) + "배",
        stateLabel(r.signal),
        r.signal_entry_path || "—",
      ];
      values.forEach((value, i) => {
        const td = element("td", i === 0 ? null : value);
        if (i === 0) {
          const button = element("button", value);
          td.append(button, element("small", r.symbol));
        }
        if (i === 4 && r.gap) td.className = r.gap > 0 ? "above" : "below";
        tr.append(td);
      });
      body.append(tr);
    }
    find("tbody").replaceChildren(body);
  }
  find(".reset").onclick = () => {
    Object.assign(filters, defaults);
    view.stage = "";
    view.direction = 0;
    controls();
    render();
  };
  controls();
  render();
}
