# 초기 손절 폭 제한 ON/OFF 비교

비교할 것은 신호일 `2 × ATR(14) ÷ 종가`가 15%를 초과하면 진입을 제외하는 규칙이다. 상승률이 15%를 초과하는지를 판단하는 필터는 아니다. 나머지 RS·셋업·인더스트리 액션·손절가·청산·위험 예산 조건은 유지한다.

`rules.stop_limit_enabled`의 기본값은 `true`로 기존 설정과 동작을 유지한다. `false`이면 폭 제한만 해제하며 손절가를 임의로 당기거나 없애지 않는다. 손절가가 0 이하이거나 기준 가격 이상인 유효하지 않은 진입은 계속 차단한다.

초기 손절가는 두 방식 모두 신호일 종가 − 2ATR다. 수량은 같은 계좌 위험 예산을 주당 손실 위험으로 나누고 현금·보유·산업·유동성 한도를 적용한다. 같은 주가에서 손절 거리가 넓어지면 위험 예산 기준 수량은 작아진다. OFF에서도 전체 손실 위험을 무제한으로 허용하는 것은 아니다. 실제 갭과 비용에 따른 손실 차이는 기존 모의운용 규칙을 따른다.

## 같은 날짜 후보·수량 비교

```powershell
python -m trendfollow compare-stop-limit --prices data/market/history/clean-prices.csv --universe data/industries/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config-setup-rs-exit.json --out outputs/stop-limit-comparison/new-run
```

공통 지표를 한 번 계산하고 제한 여부만 바꿔 독립적으로 수량을 배분한다. 기준 배수와 15% 값 자체는 변경하지 않는다. 설정에서 `max_stop_fraction`을 바꾸면 두 방식의 공통 비교 한계도 해당 값이 된다. 기존 출력 폴더는 덮어쓰지 않는다.

- `comparison.csv`: 종목별 `stop_fraction`, `stop_limit_exceeded`, ON/OFF 신호·수량·계획 위험·탈락 이유.
- `candidates-on.csv`, `candidates-off.csv`: 각 방식의 전체 결과.
- `comparison.json`: 두 설정·공통 위험 예산과 후보·배분 개수.

계좌는 두 방식에 각각 독립 적용하므로 수량을 합쳐 매수하면 안 된다. OFF에서 추가 후보가 생기면 동일한 현금·위험 예산을 함께 사용하므로 기존 후보의 배분량도 달라질 수 있다. 이는 같은 자금 제약 아래 비교한 결과다.

실측 연구 자료인 2026-11-06의 702종목을 RS 순위·셋업·산업 동반 상승·RS 청산 설정으로 비교했을 때 최종 후보는 ON/OFF 모두 0개였다. 제한 해제 시 새로 진입 가능한 종목도 없었다. 해당 날짜는 다른 진입 조건에서 이미 걸러졌으므로 이 결과로 성과 우열이나 필터의 유용성을 판단할 수 없다. 원본 결과는 `outputs/stop-limit-comparison/2026-11-06-rs`에 있다.

Mansfield 방식도 동일 명령에 해당 설정과 지수 포함 가격 파일을 지정해 비교할 수 있다. 지표 방식 자체를 바꾼 결과와 제한 ON/OFF의 효과를 섞지 않는다.

## 기간별 성과 비교

기존 `backtest` 명령에 `--stop-limit on` 또는 `--stop-limit off`를 추가한다. 같은 가격·유니버스·설정·자금·기간·분할일을 지정하고 출력 폴더만 구분한다. 별도 예제 설정을 사용하는 대신 같은 설정 파일을 그대로 두고 플래그만 변경할 수 있다.

```text
--stop-limit on  --out outputs/backtest-stop-limit-on
--stop-limit off --out outputs/backtest-stop-limit-off
```

훈련·보류 기간과 고정·점진 유닛 각각의 수익률·최대 낙폭·거래 수·평균 R 등을 비교한다. 필요하면 체결 내역으로 회전율과 평균 손실도 분석한다. 점진 유닛은 거래 성패가 다음 투입량에 영향을 주므로 후보 수량 차이와 전체 포트폴리오 경로 차이를 구분한다. 스캔 비교만으로 손실이나 수익성을 검증한 것으로 보지 않는다.

현재 새 셋업·산업 전략에는 과거 시점의 산업 동료 자료가 충분하지 않다. 해당 자료가 없는 기간의 전체 백테스트는 기존처럼 거절한다. 당일 산업 분류를 과거로 채워 성과를 만들지 않는다. 산업별 거래일 기록을 확보한 후 두 방식의 기간 비교가 필요하다.

## 모의계좌 비교 설정

- 기존 RS 청산 예제: `examples/config-setup-rs-exit.json` — 제한 기본 ON.
- 같은 설정에서 제한만 OFF: `examples/config-setup-rs-exit-no-stop-limit.json`.
- Mansfield RS 청산 예제: `examples/config-setup-mansfield-rs-exit.json` — 제한 기본 ON.
- 같은 설정에서 제한만 OFF: `examples/config-setup-mansfield-rs-exit-no-stop-limit.json`.

기존 계좌를 자동 전환하지 않는다. OFF를 모의운용에 적용할 때는 새 상태 파일을 사용한다. 장중 계획의 전일 선별 조건도 같은 설정을 반영한다. 제한 해제는 장중 시세 관측 ON/OFF와 별개다.
