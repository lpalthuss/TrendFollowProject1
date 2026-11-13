let toastTimer;
function toast(message, error = false) {
  const root = $("toast");
  clearTimeout(toastTimer);
  root.replaceChildren(
    el("strong", error ? "실행 실패" : "실행 결과"),
    el("span", message),
  );
  root.className = error ? "error" : "success";
  root.hidden = false;
  toastTimer = setTimeout(() => (root.hidden = true), 10000);
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
function table(id, columns, rows, empty = "해당 기록이 없습니다.") {
  const root = $(id);
  root.replaceChildren();
  if (!rows.length) {
    root.append(el("div", empty, "empty"));
    return;
  }
  const t = el("table"),
    head = el("tr");
  columns.forEach((c) => head.append(el("th", c[0])));
  const th = el("thead");
  th.append(head);
  t.append(th);
  const body = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    columns.forEach((c) => {
      const td = el("td"),
        value = c[1](row);
      if (value instanceof Node) td.append(value);
      else td.textContent = value ?? "—";
      tr.append(td);
    });
    body.append(tr);
  });
  t.append(body);
  root.append(t);
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
