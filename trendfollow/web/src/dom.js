function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = error ? "error" : "";
  $("toast").hidden = false;
  setTimeout(() => ($("toast").hidden = true), 6500);
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
