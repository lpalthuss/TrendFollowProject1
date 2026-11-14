let toastTimer;
function toast(message, error = false) {
  const root = $("toast");
  clearTimeout(toastTimer);
  const close = el("button", "닫기", "toast-close");
  close.type = "button";
  close.setAttribute("aria-label", "알림 닫기");
  close.onclick = () => (root.hidden = true);
  root.replaceChildren(
    el("strong", error ? "실행 실패" : "알림"),
    el("span", message, "toast-message"),
    close,
  );
  root.className = error ? "error" : "success";
  root.setAttribute("role", error ? "alert" : "status");
  root.setAttribute("aria-live", error ? "assertive" : "polite");
  root.hidden = false;
  if (!error) toastTimer = setTimeout(() => (root.hidden = true), 10000);
}

function showNextAction(title, detail, targetId = null) {
  const panel = $("flow-next");
  const target = targetId ? $(targetId) : null;
  document
    .querySelectorAll(".next-action-target")
    .forEach((node) => node.classList.remove("next-action-target"));
  panel.hidden = !title;
  if (!title) return;
  if ($("flow-next-title").textContent !== title)
    $("flow-next-title").textContent = title;
  if ($("flow-next-text").textContent !== detail)
    $("flow-next-text").textContent = detail;
  const go = $("flow-next-button");
  go.hidden = !target || target.disabled;
  if (!go.hidden) {
    target.classList.add("next-action-target");
    go.onclick = () => {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.focus();
    };
  }
}
const tableCollator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });

function tableSortValue(value) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text || ["—", "자료 부족", "자료 없음", "미확인", "금액 확인 불가"].includes(text))
    return null;
  const match = /^([+-]?\d[\d,]*(?:\.\d+)?)\s*(억원가량|억원|만원가량|만원|원가량|원|배|%)?$/.exec(text);
  if (match) {
    const amount = Number(match[1].replaceAll(",", ""));
    const unit = match[2] || "";
    const multiplier = unit.startsWith("억원") ? 100_000_000 : unit.startsWith("만원") ? 10_000 : 1;
    return amount * multiplier;
  }
  return text;
}

function tableSortDirection(current, sameColumn) {
  if (!sameColumn) return "ascending";
  return current === "ascending" ? "descending" : current === "descending" ? "none" : "ascending";
}

function table(id, columns, rows, empty = "해당 기록이 없습니다.") {
  const root = $(id);
  root.replaceChildren();
  if (!rows.length) {
    root.append(el("div", empty, "empty"));
    return;
  }
  const t = el("table"),
    head = el("tr"),
    headers = [];
  columns.forEach((c, index) => {
    if (c[2]?.sortable === false) {
      const cell = el("th", c[0]);
      head.append(cell);
      headers.push({ cell, button: null });
      return;
    }
    const cell = el("th"),
      button = el("button", c[0], "table-sort-button"),
      indicator = el("span", "↕", "table-sort-indicator");
    button.type = "button";
    indicator.setAttribute("aria-hidden", "true");
    button.append(indicator);
    cell.append(button);
    head.append(cell);
    headers.push({ cell, button, indicator, label: c[0] });
    button.onclick = () => {
      const sameColumn = root.dataset.sortColumn === String(index);
      const direction = tableSortDirection(root.dataset.sortDirection, sameColumn);
      root.dataset.sortColumn = direction === "none" ? "" : String(index);
      root.dataset.sortDirection = direction;
      applySort();
    };
  });
  const th = el("thead");
  th.append(head);
  t.append(th);
  const body = el("tbody");
  rows.forEach((row, index) => {
    const tr = el("tr");
    tr.dataset.originalIndex = String(index);
    columns.forEach((c) => {
      const td = el("td"),
        value = c[1](row);
      if (c[2]?.sortValue) {
        const sortValue = c[2].sortValue(row);
        td.dataset.sortValue = sortValue == null ? "" : String(sortValue);
      }
      if (value instanceof Node) td.append(value);
      else td.textContent = value ?? "—";
      tr.append(td);
    });
    body.append(tr);
  });
  t.append(body);
  root.append(t);

  function applySort() {
    const column = root.dataset.sortColumn === "" || root.dataset.sortColumn === undefined
      ? -1 : Number(root.dataset.sortColumn);
    const direction = root.dataset.sortDirection || "none";
    const active = column >= 0 && column < columns.length
      && columns[column][2]?.sortable !== false && direction !== "none";
    if (!active && direction !== "none") {
      root.dataset.sortColumn = "";
      root.dataset.sortDirection = "none";
    }
    const ordered = [...body.rows];
    if (active) {
      ordered.sort((left, right) => {
        const a = tableSortValue(left.cells[column].dataset.sortValue ?? left.cells[column].textContent);
        const b = tableSortValue(right.cells[column].dataset.sortValue ?? right.cells[column].textContent);
        if (a === null || b === null)
          return a === b ? Number(left.dataset.originalIndex) - Number(right.dataset.originalIndex) : a === null ? 1 : -1;
        const comparison = typeof a === "number" && typeof b === "number"
          ? a - b : tableCollator.compare(String(a), String(b));
        return (direction === "ascending" ? comparison : -comparison)
          || Number(left.dataset.originalIndex) - Number(right.dataset.originalIndex);
      });
    } else ordered.sort((a, b) => Number(a.dataset.originalIndex) - Number(b.dataset.originalIndex));
    ordered.forEach((row) => body.append(row));
    headers.forEach(({ cell, button, indicator, label }, index) => {
      if (!button) return;
      const state = active && column === index ? direction : "none";
      cell.setAttribute("aria-sort", state);
      indicator.textContent = state === "ascending" ? "▲" : state === "descending" ? "▼" : "↕";
      button.setAttribute("aria-label", `${label} 열 정렬 · ${state === "ascending" ? "오름차순" : state === "descending" ? "내림차순" : "해제"}`);
      button.title = state === "ascending" ? "다시 누르면 내림차순" : state === "descending" ? "다시 누르면 정렬 해제" : "누르면 오름차순";
    });
  }
  applySort();
}
function facts(id, items) {
  $(id).replaceChildren(
    ...items.map(([name, value]) => {
      const n = el("div", undefined, "fact");
      n.append(el("span", name), el("strong", value ?? "—"));
      return n;
    }),
  );
}
function nav(view) {
  document.body.classList.toggle("workflow-mode", view === "workflow");
  document.querySelectorAll(".view").forEach((n) => (n.hidden = n.id !== view));
  document
    .querySelectorAll(".nav")
    .forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  $("page-title").textContent = document
    .querySelector(`[data-view="${view}"]`)
    .childNodes[0].textContent.trim();
}
document
  .querySelectorAll(".nav")
  .forEach((b) => (b.onclick = () => nav(b.dataset.view)));
