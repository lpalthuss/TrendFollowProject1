const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

test("missing quotes show the specific observation reason", () => {
  const context = {};
  runInNewContext(
    readFileSync(
      join(__dirname, "../trendfollow/web/src/watch_status.js"),
      "utf8",
    ),
    context,
  );
  const status = (summary, quote, symbol = "005930") =>
    context.quoteAvailability(
      { summary },
      symbol,
      quote,
      Date.parse("2026-11-13T10:00:00+09:00"),
    );
  const active = {
    status: "running",
    connection_state: "active",
    subscription_acks: 1,
    subscriptions_requested: 1,
  };
  assert.equal(status({ ...active, rejected_symbol: "005930" }), "구독 거절");
  assert.equal(
    status({ ...active, connection_state: "reconnecting" }),
    "재연결 중",
  );
  assert.equal(status({ ...active, connection_state: "failed" }), "연결 끊김");
  assert.equal(status({ ...active, subscription_acks: 0 }), "구독 승인 미확인");
  assert.equal(status(active), "최근 체결 없음");
  assert.equal(
    status(active, { timestamp: "2026-11-13T09:58:00+09:00" }),
    "시세 지연",
  );
  assert.equal(
    status(active, { timestamp: "2026-11-13T09:59:55+09:00" }),
    "수신 정상",
  );
  assert.equal(
    status(active, {
      timestamp: "2026-11-13T09:59:55+09:00",
      price_basis_mismatch: true,
    }),
    "가격 기준 불일치",
  );
  assert.equal(
    status({ ...active, status: "completed", connection_state: "closed" }),
    "관측 종료",
  );
});
