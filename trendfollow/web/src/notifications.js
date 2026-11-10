function collectNotifications(rows, kind, deliver = true) {
  for (const r of rows) {
    const key = [
      workspace,
      kind,
      r.symbol,
      r.date || r.timestamp,
      r.kind || r.order_intent || r.status,
    ].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    if (
      deliver &&
      typeof Notification !== "undefined" &&
      Notification.permission === "granted"
    )
      new Notification(`TrendFollow · ${kind}`, {
        body: `${r.symbol || ""} ${labels[r.kind] || r.order_intent || ""} · ${r.date || r.timestamp || ""}`,
      });
  }
}
function markExisting() {
  collectNotifications(snapshot.pending, "종가 매수 계획", false);
  collectNotifications(
    (snapshot.logs.events || []).filter((r) => r.kind === "rs_exit_scheduled"),
    "종가 청산 신호",
    false,
  );
}
$("notify").onclick = async () => {
  if (typeof Notification === "undefined")
    return toast("이 브라우저는 알림을 지원하지 않습니다.", true);
  const p = await Notification.requestPermission();
  toast(
    p === "granted"
      ? "이 페이지가 열려 있는 동안 새 신호를 알립니다."
      : "알림을 허용하지 않았습니다.",
  );
};
