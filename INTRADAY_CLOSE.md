# 장중 관측과 종가 결과 자동 대조

`intraday-close`는 장중에 기록된 가격·산업 상태를 감시일 종가와 대조한다. 주문·가상 체결·계좌 상태 변경은 하지 않는다. 같은 시간대 거래량 검증·장중 RS 재계산·손익 평가나 완성된 전략 진입 신호를 확정하는 기능은 아니다.

## 실제 거래일 실행

장마감 산업 자료와 일봉을 갱신한 뒤, 한국시간 16:10 이후 실행한다. 실제 미래 날짜나 당일 16:10 전에는 종가 대조를 거절한다. 별도 명령으로 결과를 자동 계산하며 예약 실행이나 `daily`에서의 자동 호출은 등록하지 않았다.

```powershell
python -m trendfollow intraday-close --plan outputs/intraday/live-2026-11-09-first/plan.json --run-dir outputs/intraday/live-2026-11-09-first --prices data/market/history/prices.csv --peer-returns data/industries/peer-returns.csv --out outputs/intraday/close-2026-11-09
```

일봉 파일에는 감시 대상과 비교 ETF의 기준일·감시일 가격이 있어야 한다. 산업 수익률 파일도 감시일 자료를 사용한다. `--peer-returns`를 생략하거나 자료가 부족하면 종가 산업 판단은 `null`이다. 실제 관측에 사용한 계획 사본을 사용하며 다른 계획의 실행은 섞을 수 없다.

새 출력 폴더에 `comparison.csv`·`comparison.json`을 저장하고 입력 파일 해시를 기록한다. 기존 출력 폴더나 입력·계좌 파일은 덮어쓰지 않는다. 실제 관측과 저장 시세 재생 결과 모두 입력으로 사용할 수 있다. 새 재생 결과에는 계획 사본과 원본 시세 경로도 남긴다.

## 대조 항목

- `intraday_breakout_seen`: 저장된 관측에서 저항선 위에 있었는지. 실제 상향 통과 순간을 관측했다는 뜻은 아니다.
- `close_above_resistance`, `breakout_outcome`: 같은 저항선을 종가에도 넘었는지. `held_at_close`는 가격 유지, `not_held_at_close`는 종가 미유지, `close_only`는 기록 구간에서는 보지 못했으나 종가에는 저항선 위였다는 뜻이다.
- `close_setup_healthy`: 감시일 전체 저가가 허용 조정 범위를 지켰는지. 종가가 저항선 위라는 이유만으로 셋업 건전성까지 충족했다고 보지 않는다.
- `intraday_industry_action_seen`, `close_industry_action`, `industry_outcome`: 산업 동반 상승 관측과 종가 유지 여부. **계획에 고정된 동료 목록**을 사용하고 자신은 제외한다. 동료별 고정 비교 ETF를 사용하며 현재 산업 분류 변경 수는 별도로 기록한다.
- `intraday_stop_contact_seen`, `close_stop_contact`: 보유 손절선 접촉 관측과 감시일 전체 저가의 접촉 여부.
- `close_stop_contact_without_observation`: 일봉에서는 손절 접촉이 있지만 관측 기록에 없었는지. 접촉이 관측 시간 밖에 발생했을 수도 있으므로 감시 중 누락이라고 단정하지 않는다.

기업행사 등으로 기준일 종가가 계획의 전일 종가와 0.1%를 초과해 다르면 가격 기준 불일치로 가격·손절 대조를 보류한다. 일봉 누락·가격 기준 문제는 `close_data_status`, 산업 자료 부족은 동료 커버리지에서 확인한다. 일봉 고가·저가만으로 장중 돌파·손절 접촉 시각을 알아낼 수는 없다.

## 관측 OFF·기록 부재

관측하지 않은 날은 OFF를 명시한다.

```powershell
python -m trendfollow intraday-close --plan outputs/intraday/plan-2026-11-09.json --observation off --prices data/market/history/prices.csv --peer-returns data/industries/peer-returns.csv --out outputs/intraday/close-off-2026-11-09
```

위 `intraday-close --observation off`는 **보고서에 관측하지 않았음을 선언하는 옵션**이다. 실제 연결을 종료하려면 `intraday-live --observation off`를 사용한다. 보고서의 명시적 OFF는 실행 기록을 읽지 않고 종가 조건만 계산한다.

대조 명령에서 `--observation`을 생략하면 기존 실행 기록이 있을 때 그 기록을 사용하고, 기록이 없으면 공통 제어 파일의 ON/OFF를 따른다. 다른 제어 경로라면 대조 명령에도 같은 `--control-file`을 지정한다. 일부 시간 관측 후 실제 연결을 OFF했어도 이전 기록을 없었던 것으로 처리하지 않고 부분 관측으로 대조한다. 인증 단계에서 OFF되어 시세가 전혀 없었던 실행은 관측 OFF로 표시한다. 장마감 분석·모의운용은 이 설정으로 중단하지 않는다.

관측 상태는 다음처럼 구분한다.

- `disabled`: 명시적 관측 OFF.
- `missing_recordings`: 관측 폴더나 필요한 기록이 없음.
- `probe_only`: 접속·구독 확인만 수행함.
- `no_target_quotes`: 대상 종목의 유효 시세가 없음.
- `recorded`: 대상 시세가 기록됨. 하루 전체 관측을 보장하지는 않음.

비교할 수 없는 경우 관측 신호와 요약의 돌파 건수는 `null`이며 신호 0개로 집계하지 않는다. 관측했으나 해당 상태가 판단 불가였던 경우도 `null`이다. 종가 자료 누락은 돌파 실패·산업 약세가 아닌 `unknown_close`로 처리한다.

## 부분 관측·공백

`first_quote_at`, `last_quote_at`, `quote_gap_seconds`, `connection_gap_count`, `partial_session`을 함께 확인한다. 시작·종료와 종목 시세 간격은 기존 신선도 기준으로 평가하며, 짧은 테스트나 중단·연결 공백이 있었던 실행은 부분 관측으로 표시한다. 연결 이력 파일이 없으면 연결 공백 횟수는 `null`이다.

`recorded_quote_samples`는 저장된 유효 시세 시각의 개수이지 전체 체결 수가 아니다. 전체 체결 저장과 요약 저장 모두 대조할 수 있지만, 요약에 남지 않은 짧은 돌파는 복원하지 않는다. ‘기록 구간에서 못 봄’을 ‘하루 종일 신호가 없었음’으로 해석하지 않는다.

## 합성 자료 실행 예제

```powershell
python -m trendfollow intraday-replay --plan examples/intraday-demo-plan.json --ticks examples/intraday-demo-ticks.jsonl --out outputs/intraday/close-demo-replay-new
python -m trendfollow intraday-close --plan examples/intraday-demo-plan.json --run-dir outputs/intraday/close-demo-replay-new --prices examples/intraday-demo-close-prices.csv --peer-returns examples/intraday-demo-close-peers.csv --out outputs/intraday/close-demo-report-new
```

합성 예제에서는 장중 가격 돌파가 종가에는 유지되지 않고, 산업 동반 상승은 유지되며, 손절 접촉은 관측된 것으로 나온다. 일부 시간만 기록했으므로 부분 관측으로 표시한다. 합성 계획만 미래 날짜 제한을 적용하지 않고 결과에 `synthetic_comparison`을 명시한다. 실제 미래 종가를 확인한 결과가 아니다.
