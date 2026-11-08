# 진입 시 손절가와 수량 확정

이번 구현은 초기 손절과 최초 수량만 다룬다. 새 셋업 추가매수와 기존·추가분 위험 합산은 후속 작업이다. 증권사 주문은 연결하지 않는다.

## 선택 가능한 세 방식

모의 체결가를 P, 신호일에 확정된 ATR을 A라고 할 때:

- `fixed`: 손절 폭 = P × 8%, 손절가 = P × 0.92.
- `atr`: 손절 폭 = 2 × A, 손절가 = P − 2 × A.
- `hybrid`(C): 손절 폭 = max(P × 8%, 2 × A). 저변동성에서는 8%, 고변동성에서는 2ATR을 사용한다. 경계값이 같으면 fixed로 기록한다.

예를 들어 체결가 10,000원, ATR 200원이면 fixed 9,200원·atr 9,600원·hybrid 9,200원이다. ATR 600원이면 fixed 9,200원·atr 8,800원·hybrid 8,800원이다. 고정 손절 폭과 ATR 배수는 설정할 수 있다. C의 수익성 우위는 아직 결정되지 않았다.

## 확정 시점과 수량

종가 스캔 결과는 다음 거래일을 위한 예상값이다. `estimated_entry_price`는 기준 가격에 진입 슬리피지를 반영한다. `initial_stop_reference: entry_fill`이면 이 예상 체결가로 손절가와 수량을 계산한다.

모의운용에서는 다음 거래일 시가와 진입 슬리피지로 체결가를 정한 뒤 손절가·수량을 다시 계산하고 확정한다. ATR은 신호일의 확정값을 사용하며 진입일 고가·저가·종가를 참조하지 않는다. 기존의 5% 초과 갭 상승·거래 불가·신호일 예상 손절가 이하 시가에 대한 진입 취소 조건도 유지한다. 따라서 세 방식에서 체결되는 종목이 반드시 같지는 않다.

주당 계획 위험은 `체결가 × (1 + 진입 수수료율) − 손절가 × (1 − 청산 슬리피지율) × (1 − 청산 수수료율)`이다. 현재 총 비용률의 절반을 진입·청산에 각각 적용한다. 계좌 위험 예산을 주당 위험으로 나눈 수량에 현금·종목·산업·거래대금·전체 위험 한도를 함께 적용하고 정수로 내림한다. 손절 폭이 커지면 동일 위험 예산에서 수량이 줄어든다. 실제 갭 하락과 체결 차이는 이 계획 위험을 초과할 수 있다.

보유 수량을 이후 ATR 증가만으로 다시 조절하지 않는다. 초기 손절가는 `initial_stop_price`, 방식·선택된 분기는 `initial_stop_method`·`initial_stop_branch`, 당시 ATR은 `signal_atr`, 초기 계획 위험은 `initial_risk`에 보존한다. 청산 후에도 거래 기록에 남는다. 실제 관리 손절가인 `stop_price`는 기존 추적 손절과 +3R 규칙으로 올릴 수 있지만 내리지 않는다. 진입 당일 종가에 추적 손절이 올라갈 수도 있으므로 fixed는 **초기** 8% 손절 방식이다.

## 설정과 호환성

기존 설정의 기본은 `initial_stop_method: atr`, `initial_stop_reference: signal_close`다. 이는 신호일 종가에서 2ATR을 뺀 기존 손절가를 유지한다. 새 비교 방식의 atr는 체결가 기준이므로 기존 ATR 방식과 기준 가격이 다를 수 있다. 기존 모의계좌를 자동으로 전환하지 않는다. 새 방식을 모의운용하려면 새 상태 파일을 사용해야 한다.

비교 설정은 다음과 같다. 셋업·RS·인더스트리 액션·RS 약화 청산 등 다른 조건은 동일하고 `initial_stop_method`만 다르다.

- `examples/config-initial-stop-fixed.json`
- `examples/config-initial-stop-atr.json`
- `examples/config-initial-stop-hybrid.json`

세 설정은 모두 `initial_stop_reference: entry_fill`, `fixed_stop_fraction: 0.08`, `stop_atr: 2`, `stop_limit_enabled: false`다. 마지막 값은 앞서 논의한 15% 제한 미적용 비교를 위한 선택이다. 기존 기본 15% 제한 설정은 유지한다. 제한을 켜면 신호일과 진입 시점 모두 검사하며 손절가를 15%에 억지로 맞추지 않는다. 손절가가 0 이하이거나 시가 이상이면 제한 OFF에서도 진입하지 않는다.

## 종가 기준 계획 비교

PowerShell에서 동일 가격·산업 유니버스·계좌를 세 방식에 독립 적용한다. 출력 폴더는 기존 폴더를 덮어쓰지 않도록 새 경로를 사용한다.

```powershell
python -m trendfollow compare-initial-stops --prices data/market/history/prices.csv --universe data/industries/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config-initial-stop-hybrid.json --out outputs/initial-stop-comparison/2026-11-06
```

`comparison.csv`와 방식별 `candidates-fixed.csv`, `candidates-atr.csv`, `candidates-hybrid.csv`, 설정·집계인 `comparison.json`을 저장한다. 세 수량은 각각 같은 계좌를 사용한 별도 시나리오이므로 합산하지 않는다. 당일 스캔 차이만으로 수익률 우열을 판단하지 않는다.

기존 `scan`, `paper`, `daily`에도 각 비교 설정을 전달할 수 있다. Mansfield 비교는 세 설정의 RS 관련 항목을 동일하게 변경해서 사용한다.

## 과거 모의 비교

`backtest --initial-stop-method fixed|atr|hybrid`를 지정하면 공통 설정에서 손절 방식과 `entry_fill` 기준만 명시적으로 바꿔 실행한다. 각 실행의 입력 기간·유니버스·기타 설정·15% 제한 여부를 같게 맞추고 출력 폴더를 분리한다. 먼저 `train-fixed`, `holdout-fixed` 결과를 비교해 점진적 유닛 효과와 손절 방식 효과를 구분한다. 여기서 결과 이름의 fixed는 **유닛 운용 모드**이며 초기 손절 방식 이름과는 별개다.

현재 산업 동료 이력이 없는 과거 구간에 대해서는 인더스트리 액션을 적용한 전체 전략 성과를 검증할 수 없다. 과거 산업 구성을 임의로 채워 결과를 만들지 않는다. 충분한 과거 이력 또는 전향적 모의운용이 필요하다.
