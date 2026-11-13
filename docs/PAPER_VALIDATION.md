# 셋업·인더스트리 액션 모의운용

준비일: 2026-11-08. 시작 기준일: 2026-11-06 장마감.

두 계좌는 각각 가상자금 10,000,000원, 1유닛으로 독립 시작한다. 셋업·산업 동반 상승·위험관리 조건은 같으며 RS 선별 방식만 비교한다. 기존 `outputs/paper/state.json` 계좌는 그대로 보존한다. KIS 모의투자 계좌에 주문하는 방식이 아니라 프로젝트 내부의 가상 체결이다.

- RS: `examples/config-setup.json`, 상태 `outputs/paper-setup-rs/state.json`, 데이터 `data/market/history/prices.csv`.
- Mansfield RS: `examples/config-setup-mansfield-index.json`, 상태 `outputs/paper-setup-mansfield/state.json`, 데이터 `data/market/history-mansfield/prices.csv`. 비교 지수는 종목 시장에 따라 KOSPI 또는 KOSDAQ이다.
- 산업 자료: 두 방식 모두 `data/industries/universe.csv`와 해당 파일이 가리키는 산업 동료 수익률 이력을 사용한다.

최초 실행은 기존 데이터를 사용하는 기준점 설정이다. 11월 8일에 관찰한 현재 산업 분류와 11월 6일 수익률을 사용하므로 당시 산업 구성을 복원한 과거 검증이나 전향적 운용 성과로 보지 않는다. 첫날 신호는 다음 거래일 진입 대기이며, 첫날 종가에 체결하지 않는다. 신호가 없으면 현금을 유지한다.

## 초기 확인 결과

두 계좌 모두 초기화에 성공했다. 각 계좌는 현금 10,000,000원, 보유 0종목, 대기 신호 0개다. 702종목 중 구조상 셋업 준비 8개·돌파 1개였으나, 최종 매수 조건을 모두 통과한 종목은 없었다. 모든 종목에 넓은 산업 동료 자료를 적용했다. 기존 모의계좌의 파일 해시가 변경되지 않았음을 확인했다.

운용·셋업·산업·상대강도 관련 기존 테스트 59개가 통과했다. 두 계좌의 설정 해시와 입력 지문·시작 결과는 `outputs/paper-validation/startup.json`에 저장했다. 이는 미래 거래일 체결 검증이 완료됐다는 뜻은 아니다.

## 다음 거래일부터 실행

프로젝트 폴더에서 한국시간 장마감 데이터 확인을 위해 16:10 이후 실행한다. 매 거래일 순서대로 실행하며 여러 거래일을 건너뛰어 한 번에 처리하지 않는다. 프로그램은 미처리 거래일이 여러 개면 처리를 거절한다.

UI의 `누락 거래일 포함 가격 갱신 + 종가 모의 처리`는 별도 일괄 실행이다. 선택 날짜까지 가격을 한 번 수집하고, 미처리 거래일을 오래된 순서로 모의 처리한다. 파동 셋업의 산업 동료 기록이 빠졌다면 해당 거래일보다 먼저 관찰해 저장한 산업 구성과 과거 일봉으로 수익률을 재구성한다. `data/industries/reconstruction-latest.json`에 사용한 스냅샷과 유효 가격 수를 남긴다. 당시의 거래 가능 상태나 중간 산업 구성 변경은 확인할 수 없으므로 재구성 기록은 근사치다. 재구성에 필요한 과거 산업 구성 또는 시세가 없으면 계좌 처리를 시작하지 않는다. 처리 도중 오류가 나면 완료된 날짜까지의 계좌 상태를 보존하고 실패 날짜를 표시한다. CLI `daily`는 기존처럼 날짜를 하나씩 명시해야 한다.

과거에 이미 처리한 날짜의 가격·거래량이 달라져 중단되면 같은 수집 버튼을 반복하지 않는다. 화면 아래 정정 보고서에서 이전 값과 새 값을 검토한다. 보유·대기 종목의 가격 변경이나 행 누락이 없어 계속 가능한 보고서라면 검토 확인 후 `정정 반영 후 선택 날짜까지 처리`로 보고서 날짜부터 선택한 날짜까지 이어 처리한다. 이 경로는 저장된 수집 자료를 사용하므로 전 종목 가격을 다시 받지 않는다. 종가 정정이 포함되면 명시적 가격 검토 확인이 필요하다.

```powershell
python -m trendfollow industry-sync --refresh
python -m trendfollow daily --config examples/config-setup.json --universe data/industries/universe.csv --data-dir data/market/history --state outputs/paper-setup-rs/state.json --verify-provider kiwoom
python -m trendfollow daily --config examples/config-setup-mansfield-index.json --universe data/industries/universe.csv --data-dir data/market/history-mansfield --state outputs/paper-setup-mansfield/state.json --verify-provider kiwoom
```

후보 시세 교차 확인은 기존 검증에서 선택한 키움 REST를 유지한다. KIS 모의 키의 인증·3종목 최근 일봉 접근도 별도로 확인했지만, 과거 KIS와 타 원천의 가격 차이는 해결되지 않았다. KIS를 비교 원천으로 시험하려면 `--verify-provider kis`로 바꿀 수 있다. 원천 간 차이가 허용오차를 넘으면 후보는 승인되지 않는다. 토큰 발급 성공과 후보 데이터 정합성 승인은 다른 검사다.

두 모의계좌의 가격 파일은 분리한다. Mansfield 계좌는 실제 지수 행이 추가로 필요하다. 최초 계좌 입력도 `daily` 수집과 같은 원본 행을 보존하며, 잘못된 OHLC는 계산 단계에서 격리한다. 정제본으로 초기화한 뒤 원본으로 바꾸면서 과거 데이터 변경으로 오인되는 일을 피한다.

## 매일 확인할 기록

각 계좌의 `YYYY-MM-DD` 폴더에서 다음을 확인한다.

- `signals.csv`: 종목별 셋업·RS·산업 조건과 최종 신호.
- `broker-audit.json`: 교차 확인 원천, 승인·탈락 종목과 데이터 차이. 최초 오프라인 기준점 설정에는 생성하지 않는다.
- `orders.csv`, `trades.csv`: 다음 거래일 진입·취소·손절·청산.
- `equity.csv`, `events.csv`, `state.json`: 현금·보유·위험 유닛과 다음 거래일 대기 신호.

같은 날짜 재실행은 추가 체결 없이 `already_processed`여야 한다. 주말 기본 실행은 `skipped_weekend`가 정상이다. 산업 분류·전략 설정 변경이나 과거 일봉·산업 수익률 정정이 탐지되면 기존 계좌를 덮어쓰지 않는다. 오류 원인을 검토하고 필요할 경우 별도 상태 파일로 새 비교를 시작한다.

현재는 수동 실행이며 예약 작업은 등록하지 않았다. 일별 미래 자료가 쌓이기 전에는 전향적 체결 정합성이나 성과가 확인됐다고 평가하지 않는다. 과거 산업 이력이 없는 날짜를 임의로 채워 새 전략 전체를 과거 재생하지 않는다. 시장 상황별 유닛 조절·추가매수·시간 및 RS 약화 청산은 이번 비교에 포함되지 않는다.
