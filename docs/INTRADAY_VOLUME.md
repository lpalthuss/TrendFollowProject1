# 장중 거래량 검증

장중 누적 거래량을 과거 같은 시각의 평균 누적량과 비교한다. KIS WebSocket에서 이미 수신한 누적 거래량과 종료된 관측 기록을 사용하며 추가 REST 조회를 반복하지 않는다. 기존의 ‘전일 동시간 대비 비율’은 보조 관측값으로 유지하고 여러 거래일 평균 기준과 구분한다.

## 초기 연구 규칙

- 비교 시각: 마지막으로 완료된 분 경계. 10:15:20에는 10:15:00까지의 표본으로 비교한다. 그 뒤 20초 동안 늘어난 누적량을 앞 시각의 과거 평균과 비교하지 않는다.
- 분 경계의 표본: 그 시각 또는 이전에 수신한 가장 최근 누적량. 기본 최대 지연은 60초이며 미래 시세로 보간하거나 빈 구간을 채우지 않는다. 체결 시각이 분 경계와 정확히 일치하지 않으면 근사 표본이다.
- 기준: 종목별 최근 최대 **20개 수집 거래일** 중 해당 분의 표본이 최소 **5일** 있는 경우의 산술평균. 거래소 전체의 최근 20거래일을 모두 확보했다는 뜻은 아니다.
- 통과: 현재 분 경계 누적량 ÷ 과거 동시간 평균 ≥ **1.5배**.

기간·최소 일수·배수·표본 지연은 검증용 설정이며 영상의 확정 규칙은 아니다. 분 단위 비교이므로 현재 분이 끝나기 전의 거래량 급증은 다음 분 비교까지 반영되지 않을 수 있다. 가격 손절 즉시 감지는 그대로 유지한다.

## 실제 자료 준비

매 거래일 감시 종료 후 기록에서 표본을 가져온다. 전체 체결 저장과 요약 저장 모두 지원한다.

```powershell
python -m trendfollow intraday-volume-import --run-dirs outputs/intraday/live-2026-11-09-first --out data/intraday-volume/samples.csv
```

실측은 종료된 KIS 실행만 허용하며 해당 거래일 완료 후, 당일은 한국시간 16:10 이후 가져온다. 같은 파일에 새 날짜를 합치며 같은 종목·거래일·분은 중복 집계하지 않는다. 여러 실행이 겹치면 더 최근의 유효 표본을 선택하되 누적량 상충·감소는 거절한다. 짧게 관측한 날은 그 시간대에 실제로 남은 표본만 가져온다. 개장 시각부터 관측하지 않았어도 KIS의 누적량은 당일 누적량이지만, 놓친 시각의 표본은 복원하지 않는다.

접속 확인 전용 기록, 실행 중인 실측 기록, 합성·실측 혼합은 거절한다. 출처 해시는 이력 옆 metadata JSON에 저장한다. 실제 비교에는 여러 거래일의 수집이 필요하다. 현재 로컬 실측 자료에는 그 이력이 없어 자동으로 거래량 조건을 통과시키지 않는다.

## 기준 생성과 감시 계획 연결

```powershell
python -m trendfollow intraday-volume-profile --history data/intraday-volume/samples.csv --as-of 2026-11-13 --lookback 20 --min-sessions 5 --multiple 1.5 --max-sample-age-seconds 60 --out data/intraday-volume/profile-2026-11-13.json
```

위 기준일은 형식 예시다. 실제로 확보한 기준일까지의 자료로 생성한다. 더 촘촘한 비교가 필요하면 표본 지연을 5초 등으로 줄일 수 있다. 대신 해당 시각에 쓸 수 있는 거래일 수가 줄어들 수 있다.

다음 감시 계획 생성 시 기존 `intraday-prepare` 명령에 다음 옵션을 추가한다.

```text
--volume-profile data/intraday-volume/profile-2026-11-13.json
```

기준을 계획 안에 고정해 파일이 나중에 갱신돼도 기존 실행의 판단이 바뀌지 않는다. 계획 기준일 이후의 기준과 합성 기준의 실제 계획 사용은 거절한다. 기준 파일을 지정하지 않은 기존 계획은 계속 거래량 미확인 상태다. 현재 실측 계획과 모의계좌를 자동으로 교체하지 않는다.

## 신호 해석

- `volume_status`: `verified`(통과), `weak`(미달), `insufficient_history`, `zero_reference_volume`, `current_sample_unavailable`, `stale_or_invalid_quote`, `unavailable_same_time_history`.
- `volume_pass`: 통과 `true`, 실제 비교 후 미달 `false`, 비교 불가 `null`.
- `volume_minute`, `volume_sample_at`, `volume_current_cumulative`: 비교 분과 실제 표본 시각·현재 누적량.
- `volume_reference_sessions`, `volume_reference_mean`, `volume_ratio_same_time`: 기준 거래일 수·평균·비율. 필요한 최소 일수와 배수도 기록한다.

`entry_confirmed=true`는 전일 선별 조건을 사용한 기존 `entry_watch`와 동시간 거래량 조건을 모두 만족했다는 관측 결과다. RS·주봉 추세는 여전히 전일 값이며 실제 주문·체결·손익 확정을 뜻하지 않는다. 가격이 다시 저항선 아래로 내려오면 거래량이 강해도 진입 조건 충족 표시를 해제한다.

요약 저장에도 완료 분 표본을 포함해 전체 체결 저장과 동일한 판단을 재생한다. 재접속 때 분 표본도 비우며, 재접속 후 분 경계 이전 표본이 없으면 다음 유효 표본까지 보류한다. 종가 대조에는 `intraday_volume_verified_seen`, `intraday_entry_confirmed_seen`이 추가된다. 해당 항목은 관측 결과를 요약하며 보고서가 거래량 기준을 새로 계산하거나 주문하지는 않는다.

수집일 누락·표본 지연·관측 시간 차이와 기업행사 전후 거래량 단위의 비교 가능성은 실제 자료 검증에 남아 있다. 기준 JSON의 거래일 목록·표본 수와 원천 기록을 함께 확인한다.

## 합성 검증 예제

```powershell
python -m trendfollow intraday-replay --plan examples/intraday-demo-volume-plan.json --ticks examples/intraday-demo-volume-ticks.jsonl --out outputs/intraday/volume-replay-new
```

기준은 합성 5개 거래일의 표본이며, 분 경계 누적량 1,490주 ÷ 평균 1,000주 = 1.49배는 미달, 다음 분 3,100주 ÷ 평균 2,000주 = 1.55배는 통과다. 중간 시각 누적량이 증가해도 앞 분의 값을 바꾸지 않는다. 합성 자료는 실제 감시에 사용할 수 없고 결과는 `observation_only`이며 주문은 0건이다.
