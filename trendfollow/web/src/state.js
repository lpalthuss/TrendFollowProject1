"use strict";
const $ = (id) => document.getElementById(id),
  el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
let boot,
  snapshot,
  workspace,
  revision = "",
  draft,
  watchData,
  polling = false,
  loading = false,
  loadSequence = 0;
let guidance = null,
  currentJobs = [],
  flowStep = 1,
  firstJobs = true;
let recoveryInfo = null,
  recoveryRows = null,
  recoveryKey = "",
  revisionOffset = 0,
  replayWorkspace = null;
const primedRuns = new Set();
const seen = new Set(),
  finished = new Set();
const number = (v, d = 0) =>
  v === null || v === undefined || !Number.isFinite(Number(v))
    ? "—"
    : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d });
const percent = (v) =>
  v === null || v === undefined ? "—" : number(v * 100, 1) + "%";
const flag = (v, unknown = "미확인") =>
  el(
    "span",
    v === true ? "통과" : v === false ? "미통과" : unknown,
    `pill ${v === true ? "yes" : v === false ? "no" : "warn"}`,
  );
const labels = {
  filled: "모의 체결",
  rejected: "취소·미체결",
  queued: "대기",
  running: "진행 중",
  completed: "완료",
  failed: "실패",
  success_3r: "최초 3R 달성",
  rs_exit_scheduled: "RS 약화 청산 예약",
  pyramiding_review: "추가매수 검토",
  campaign_lot_opened: "매수 차수 기록",
  campaign_risk: "종가 위험 점검",
  signal_state: "장중 잠정 상태",
  holding_stop_state: "장중 손절 접촉",
  analyze: "후보 분석",
  paper: "일별 모의 처리",
  daily: "데이터 갱신·일별 처리",
  industry: "산업 갱신",
  prepare_intraday: "감시 계획",
  revision_check: "변경 내역 대조",
  revision_continue: "정정 반영·계속 운용",
  revision_replay: "정정 재계산 계좌",
  intraday_close: "장중·종가 대조",
  volume_import: "거래량 이력 누적",
  volume_profile: "동시간 거래량 기준",
  unconfirmed: "실행 여부 미확인",
  interrupted: "중단 확인 필요",
};
