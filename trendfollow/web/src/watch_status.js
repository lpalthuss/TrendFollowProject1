function quoteAvailability(run, symbol, quote, now = Date.now()) {
  const summary = run?.summary;
  if (!summary) return "관측 기록 없음";
  if (summary.rejected_symbol === symbol) return "구독 거절";
  if (summary.status === "completed" || summary.connection_state === "closed")
    return "관측 종료";
  if (
    summary.status === "starting" &&
    summary.connection_state === "disconnected"
  )
    return "연결 준비 중";
  if (summary.connection_state === "reconnecting") return "재연결 중";
  if (
    ["disconnected", "failed", "unresponsive"].includes(
      summary.connection_state,
    )
  )
    return "연결 끊김";
  if (summary.connection_state !== "active") return "연결·구독 확인 중";
  if (summary.subscription_acks < summary.subscriptions_requested && !quote)
    return "구독 승인 미확인";
  if (!quote) return "최근 체결 없음";
  if (quote.price_basis_mismatch) return "가격 기준 불일치";
  const age = now - Date.parse(quote.timestamp);
  if (!Number.isFinite(age) || age < -2000 || age > 60000) return "시세 지연";
  return "수신 정상";
}
