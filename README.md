# 추세추종 장마감 분석 도구

국내 주식의 일봉·완료 주봉을 분석하여 후보와 위험 예산을 CSV·JSON으로 출력한다. **이번 버전은 시세 조회와 계획 수량 계산까지이며 주문 기능은 없다.** 조사 배경과 영상 내용은 [RESEARCH.md](RESEARCH.md)에 있다.

## 시작

실제 장중 연결 시작은 `intraday-live --observation on --plan ... --out ...`, 종료는 다른 터미널에서 `intraday-live --observation off`, 확인은 `intraday-live --observation status`다. 생략 시 저장된 설정을 존중하며 OFF에서는 인증·연결·구독을 시작하지 않는다. 종료 확인과 제어 파일 범위는 [INTRADAY.md](INTRADAY.md)에 설명했다.

장중 관측과 종가 결과의 대조 명령 `intraday-close`는 [INTRADAY_CLOSE.md](INTRADAY_CLOSE.md)에 정리했다. 관측 OFF·기록 누락·부분 관측을 구분하고, 같은 저항선·고정 산업 동료 기준으로 종가 유지 여부를 계산한다.

셋업·인더스트리 액션을 적용한 RS/Mansfield RS 두 모의계좌의 시작 조건과 일별 실행 절차는 [PAPER_VALIDATION.md](PAPER_VALIDATION.md)에 정리했다.

장중 돌파·손절·산업 동반 상승의 잠정 상태를 KIS 실시간 시세로 관측·재생하는 절차는 [INTRADAY.md](INTRADAY.md)에 있다. 장중 경로는 주문·체결을 실행하지 않고, RS는 전일 값으로 고정하며 거래량 미확인 상태를 진입 확정으로 표시하지 않는다.

장중 판단은 기본 5초 간격이고 저장은 기본 요약이다. 보유 손절선 접촉은 시세 수신 즉시 별도 확인한다. `--evaluation-seconds`로 간격을, `--tick-storage all|summary`로 저장 방식을 선택한다.

모든 메시지가 기본 120초 동안 없으면 연결 무응답으로 판단해 재접속·재구독한다. `--idle-timeout`으로 기준을 조절하며, PINGPONG만 수신되는 연결과 개별 종목 시세 부재를 구분한다. 마지막 수신 시각과 복구 이력은 `summary.json`·`connection-events.jsonl`에 기록한다.

Python 3.11 이상. 프로젝트 폴더에서 PowerShell로 실행한다.

```powershell
cd D:\Coding2\TrendFollowProject1
python -m pip install -e ".[test]"
python -m trendfollow demo
```

API 없이 고정 시드의 **합성 데이터**로 전체 흐름을 실행한다. 한국 거래소 휴일 달력이나 실제 주가를 재현하는 데이터가 아니며 성과 검증 자료도 아니다.

결과는 `outputs/demo/candidates.csv`, `outputs/demo/report.json`이다. 예시 입력인 `prices.csv`, `universe.csv`, `account.json`도 같은 폴더에 생성한다. CSV는 Excel에서 열기 쉽게 UTF-8 BOM으로 저장한다.

## API 키 입력

프로젝트 루트의 `.env`를 편집한다. 파일은 이미 만들어 두었으며 `.gitignore`에서 제외했다. 키를 채팅이나 GitHub에 입력하지 않는다. `.env`는 평문 로컬 파일이므로 공유 폴더·공용 PC에 복사하지 않는다.

- KIS: `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ENV`
- 키움 REST: `KIWOOM_APP_KEY`, `KIWOOM_SECRET_KEY`, `KIWOOM_ENV`
- 환경: 모의 키는 `paper`, 실전 키는 `live`. 발급 환경과 일치시킨다.
- 계좌번호는 현재 시세 조회에 사용하지 않으므로 비워도 된다.
- OS 환경변수에 같은 이름이 있으면 `.env` 값보다 우선한다.

KIS의 Open API는 REST·WebSocket 방식으로 제공된다. 키움 OpenAPI+(OCX)와 키움 REST는 별도 서비스다. `openapi.kiwoom.com`의 App Key·Secret Key를 키움 REST 항목에 입력한다. 영웅문 ID·로그인 비밀번호를 넣지 않는다.

설정만 확인하는 명령은 네트워크를 호출하지 않는다. 출력에는 키·시크릿·토큰을 포함하지 않는다.

```powershell
python -m trendfollow check --provider kis
python -m trendfollow check --provider kiwoom
```

아래 명령은 실제 서버에 토큰 발급을 요청한다. 키의 환경을 사용하며 주문은 하지 않는다. 키움 토큰 재발급은 다른 세션에 영향을 줄 수 있으므로 반복 실행하지 않는다. 현재 토큰은 프로세스 메모리에만 보관한다.

```powershell
python -m trendfollow check --provider kis --connect
python -m trendfollow check --provider kiwoom --connect
```

## 시세 수집

`examples/universe.csv`의 종목과 벤치마크를 수집한다. 예시의 069500은 KOSPI 자체가 아닌 **ETF를 사용하는 벤치마크 대용 예시**다. 투자 후보 추천이나 시장 전체 유니버스가 아니다. 사용자 관심 종목을 추가하고 적합한 벤치마크를 선택한다. RS는 입력한 유니버스 안에서 계산하므로 소수 예시 종목으로 시장 전체 상위 백분위를 해석하지 않는다.

```powershell
python -m trendfollow fetch --provider kis --universe examples/universe.csv --start 2025-01-01 --end 2026-11-06 --out data/kis-prices.csv
python -m trendfollow fetch --provider kiwoom --universe examples/universe.csv --start 2025-01-01 --end 2026-11-06 --out data/kiwoom-prices.csv
```

각 수집 명령은 별도 원천 파일을 만든다. KIS는 날짜 구간을 뒤로 이동하며 최대 100개씩, 키움은 연속조회 키로 수집한다. 페이지 한도·진행 불가·API 오류 시 실패하며 부분 수집 결과로 기존 CSV를 덮지 않는다. 원천·환경·가격 조정 방식은 같은 이름의 `.metadata.json`에 기록한다. 기본 요청 간격은 1.1초다.

시세 연결은 공식 스펙·샘플을 참고하고 모의 HTTP 응답으로 테스트했다. 2026-11-08 두 증권사의 실전용 키로 인증·일봉 수집도 확인했다. 일부 과거 가격 차이가 남아 있으며 상세 결과는 [LIVE_VALIDATION.md](LIVE_VALIDATION.md)에 기록했다. 장기 이력의 정확성·완전성을 모두 검증한 것은 아니다. 두 원천 파일을 임의 혼합하지 않는다. 주문용 원주가·호가단위로의 변환은 아직 구현하지 않았다.

## 분석

`examples/account.json`의 순자산·현금·보유 종목을 실제 기준 시점에 맞춘다. 예시 금액은 가상 금액이다. 기존 보유 종목은 `symbol`, `sector`, `quantity`, `current_price`, `stop_price`를 모두 기입한다. 순자산과 가격은 같은 평가 시점을 사용해야 한다.

```powershell
python -m trendfollow scan --prices data/kis-prices.csv --universe examples/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config.json --units 1 --out outputs/kis
```

CSV 가격 필드: `date,symbol,open,high,low,close,volume`. 날짜는 `YYYY-MM-DD`, 주가는 원, 거래량은 주 단위다. 종목코드의 앞자리 0을 유지한다. 벤치마크도 같은 CSV에 넣는다. 모든 OHLC는 서로 일관된 가격 조정 기준을 사용한다.

주요 출력:

- `signal`, `reasons`: 필터 통과 여부와 탈락 사유
- `rs_percentile`, `excess_return`: 입력 유니버스 내 순위·벤치마크 대비 수익률
- `reference_price`, `stop_price`, `atr`, `atr_pct`: 참고 가격과 손절 기준
- `quantity`, `cash_required`, `planned_risk`: 순차 배분한 계획 수량·금액·손실 예산
- `allocation_reason`: 수량 제한의 주된 원인. 통과 신호여도 0주가 될 수 있다.

## 현재 규칙과 가정

아래 숫자는 영상 전략의 완전한 재현이 아닌 **검증용 기준선 가설**이다. `examples/config.json`으로 변경할 수 있다.

- 완료 주봉 종가가 30주 평균 위, 30주 평균은 4주 전보다 상승
- 종가가 **당일을 제외한** 직전 55거래일 고가 돌파
- 거래량은 직전 20거래일 평균의 1.5배 이상
- 직전 20거래일 평균 거래대금 10억 원 이상
- 126거래일 수익률의 유니버스 내 백분위 70 이상, 벤치마크 대비 수익률 양수
- 최초 손절 기준: 참고 종가 - Wilder ATR(14) × 2. 손절 폭 15% 초과는 제외

유닛은 1~5이며 기본 1이다. 기본 최대 거래 위험은 순자산의 1%로 설정했고, 1유닛에서는 그 1/5인 0.2%가 위험 예산이다. 영상의 2% 예시를 기본 운용 위험으로 자동 채택하지 않았다. 기본 계좌 위험 합계는 4%, 종목 비중은 25%, 업종 비중은 40%, 현금 유보는 10%, 과거 거래대금 대비 참여 비율은 0.1%다.

비용률 0.3%, 진입·청산 슬리피지 각각 0.2%는 **검증용 가정**이다. 현재 세율·증권사 수수료 실측값이 아니므로 계좌와 상품에 맞춰 수정한다. 비용률은 수수료·세금 등의 합계이며 슬리피지는 별도다. 현금 계산에도 전체 비용 예산을 보수적으로 유보한다.

배분은 RS 순서, 동률이면 종목코드 순서다. 여러 후보가 현금·계좌 위험·업종 한도를 공유한다. 기존 보유 종목의 위험은 현재 평가액에서 손절까지의 비용 포함 손실로 계산한다. 보유 종목의 손절 기준이 이미 침범되면 신규 계획을 차단하고 청산 검토를 우선한다. 보유 종목의 추가 매수는 현재 차단한다. 피라미딩은 아직 구현하지 않았다.

미체결이 있으면 업종별 예약 위험을 정확히 반영할 수 없으므로 신규 수량 전체를 차단한다. 잔고·미체결 동기화 후 다시 분석한다. 수량을 계산했다고 손절 체결이나 최대 손실이 보장되지는 않는다.

## 점진적 베팅 이벤트

`examples/events.json`은 형식 예시다. 검증한 실제 거래 이벤트 파일을 지정하면 `--units` 대신 시간순 재생 결과를 적용한다. 해당 분석일 다음 날 00:00 KST 이전 이벤트만 사용한다. timestamp에는 시간대가 필요하다.

```powershell
python -m trendfollow scan --prices data/kis-prices.csv --universe examples/universe.csv --account examples/account.json --as-of 2026-11-06 --events examples/events.json --out outputs/events
```

`success_3r`는 거래별 최초 한 번만 증액한다. `loss_closed`는 성공 이력이 없는 거래에서만 감액하고, `neutral_closed`는 변화가 없다. 성공 후 손실 청산을 다시 실패로 세지 않는 정책을 명시적으로 선택했다. 동일 event_id는 중복 적용하지 않고, 이미 청산한 거래의 새 이벤트는 거절한다. 자동 가격 감시·성공 이벤트 생성·주문 연동은 아직 없다.

## 검증과 한계

```powershell
python -m pytest -q
```

미래 가격 행 배제, 전일 고가 돌파, 누락·중복 데이터 거절, Wilder 계산, 공동 위험·현금·업종 배분, 유닛 중복 이벤트, 앞자리 0 보존, HTTP 페이지 처리·비밀값 비출력을 검증한다.

분석은 사용자가 지정한 **장 종료 거래일** 기준이다. CLI는 당일 한국시간 16:10 전과 미래 기준일을 거절한다. 실제 거래소 휴장·데이터 확정 시간을 자동 판별하지는 않는다. 주봉은 금요일 날짜가 기준일 이하인 것만 사용하므로 금요일 휴장인 주는 보수적으로 다음 주에 반영한다. 거래정지의 0원 OHLC나 누락 세션은 임의 보정하지 않는다.

포트폴리오 연구 백테스트, 20일 저가 채널 트레일링, 갭 손절과 로컬 모의 계좌가 추가되었다. 업종 동반 강세 자동 판정, 재량적 셋업 패턴, 피라미딩, 당시 유니버스·기업행동의 완전한 복원은 아직 없다. 실제 운영 단계별 결과는 [PROGRESS.md](PROGRESS.md)에 있다.

## 전체 연구 흐름

```powershell
# 현재 공식 마스터를 사용한 유니버스. --refresh를 붙이면 다시 다운로드한다.
python -m trendfollow market-universe --out data/market

# 공개 차트의 연구용 가격. 증권사 주문용 원주가가 아니다.
python -m trendfollow research-data --universe data/market/universe.csv --out data/market/history --start 2020-01-01 --end 2026-11-06

# 독립 원천 대조. 가격 0.1%, 거래량 5%는 판정용 허용오차이며 정확성 보증이 아니다.
python -m trendfollow audit --primary data/kiwoom-prices.csv --reference data/independent/naver-prices.csv --out outputs/audit/kiwoom-naver

# 현재 생존·최근 유동성으로 선택한 종목의 탐색 비교. 실전 수익성 검증으로 해석하지 않는다.
python -m trendfollow backtest --prices data/market/history/prices.csv --universe data/market/universe.csv --start 2021-01-01 --split 2024-01-01 --end 2026-11-06 --config examples/config.json --out outputs/backtest-market

# 장마감 데이터 갱신 → 키움 후보 시세 대조 → 로컬 모의 계좌 처리
python -m trendfollow daily --config examples/config.json
```

유니버스는 보통주·외국주식 구분, 정지·관리·정리매매·저유동성·우선주·SPAC 상태를 검사한다. 최근 거래대금 대용값(마스터 기준가 × 전일 거래량) 10억 원 이상을 사전 조건으로 사용하고, 이후 실제 과거 20일 평균 거래대금도 검사한다. 시장 전체를 무조건 포함하는 방식이 아니며, 현재 시점의 유동성으로 과거를 선택하는 편향이 있다. 업종은 마스터가 제공하는 가장 세부적인 0이 아닌 지수 업종코드를 사용한다.

백테스트는 종가 신호의 다음 거래일 시가에서 수량을 다시 계산한다. 진입 갭이 5%를 넘거나 시가가 손절 아래이면 진입을 거절한다. 갭 하락은 손절가보다 낮은 시가로 청산하고, 하루 단일 가격인 봉은 체결을 확신할 수 없어 보수적으로 미체결 처리한다. 일중 손절이 3R 성공보다 우선하고, 성공은 종가 도달로 판정하며 거래별 한 번만 반영한다. 최초 손절과 20일 저가 채널 중 높은 선을 다음 날부터 적용하고, +3R 성공 시 손절선을 진입가 이상으로 올린다. 기간 말 보유 종목은 강제 매도하지 않는다. 종료 시점의 평가손익을 실현손익과 구분한다.

`daily`는 한국시간 16:10 이후 실행한다. 주말·기준일 벤치마크가 없는 날은 건너뛴다. 후보의 최근 약 300일 일봉을 키움과 대조한 뒤 허용오차를 통과한 종목만 다음 거래일 계획에 넣는다. 그 밖의 전체 시장 일봉을 전부 증권사로 대조했다는 의미는 아니다. 같은 날짜의 재실행은 중복 반영하지 않으며, 설정·유니버스나 이미 처리한 과거 데이터가 바뀌면 계좌를 보존하고 검토를 요구한다. 누락한 거래일이 여러 개이면 `--as-of YYYY-MM-DD`로 하나씩 순서대로 처리한다.

첫 실행은 지정일 장 종료 시점의 가상 현금과 다음 날 후보만 저장한다. 이후 실행부터 실제 다음 거래일 OHLC로 모의 체결을 계산한다. 실제 계좌와 자금은 연결하지 않는다. 가상 원금은 `--capital`로 정하며, 기존 모의 계좌의 원금·설정 변경은 새 `--state` 파일로 시작한다. 초기값은 예시 1,000만 원이다.

실행 명령과 모의 계좌는 준비되어 있지만 자동 실행 일정은 등록하지 않았다. 컴퓨터나 작업이 꺼져 있으면 실행되지 않는다. 가격 정정·기업행동으로 과거 수정주가가 바뀐 경우에는 자동으로 보유 수량을 조정하지 않고 중단한다.

## RS와 Mansfield RS를 나란히 확인

최종 방식은 아직 선택하지 않았다. 기존 설정은 `rs_method: "percentile"`을 유지하며, 분석 파일에는 두 지표와 각 방식의 후보 통과 여부를 함께 출력한다.

- `rs_percentile`: 최근 `rs_days`(기본 126거래일) 수익률의 분석 유니버스 내 백분위. 기존 방식은 RS 70 이상과 같은 기간 벤치마크 초과수익 양수를 함께 요구한다.
- `mansfield_rs`: 완료된 주봉의 `종목 종가 / 벤치마크 종가`를 구하고, `((현재 상대가격 / 상대가격의 52주 SMA) - 1) × 100`으로 계산한다. 기본 후보 조건은 **0 초과**다. 상위 백분위가 아니라 상대가격의 평균 대비 괴리율이다.
- `mansfield_change_1w`: Mansfield 값의 직전 완료 주 대비 변화량(퍼센트포인트). 현재는 관찰용이며 상승 여부를 후보 조건에 추가하지 않았다.
- `signal_rs`, `signal_mansfield`: 동일한 추세·돌파·거래량·유동성·손절 조건에 각각의 RS 조건을 적용한 결과다. Mansfield 방식은 기존 126일 초과수익 조건을 추가로 요구하지 않는다.
- `signal`, `allocation_score`: 이번 실행에서 선택한 방식의 통과 여부와 배분 순서 점수다. RS 방식은 백분위가 높은 순서, Mansfield 방식은 값이 높은 순서로 배분한다.

Mansfield 기간은 `mansfield_weeks`(기본 52), 기준값은 `min_mansfield`(기본 0)다. 배율은 ×100으로 고정했다. 52개 완료 주가 필요하고, 주의 마지막 벤치마크 거래일에 종목 가격이 없으면 그 주를 유효한 주봉으로 대체하지 않는다. 불완전한 현재 주는 사용하지 않는다. 금요일 휴장인 주는 기존 규칙처럼 다음 주에 반영하는 보수적 방식이다. 계산 불가 값은 CSV에서 빈칸·JSON에서 null로 표시하고, Mansfield 방식은 해당 후보를 제외한다. 기존 RS 방식은 Mansfield 계산 불가를 이유로 후보를 제외하지 않는다.

동일 날짜·동일 계좌 가정으로 두 방식의 후보와 계획 수량을 각각 비교한다. 입력 데이터는 이미 저장된 파일을 사용하므로 API 키나 네트워크 호출이 필요 없다.

```powershell
python -m trendfollow compare-rs --prices data/market/history/clean-prices.csv --universe data/market/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config.json --out outputs/rs-comparison
```

확인 파일은 `outputs/rs-comparison/comparison.csv`다. 두 지표와 `quantity_rs`, `quantity_mansfield`를 나란히 보여준다. 수량은 동일한 계좌를 각 방식에 **독립적으로** 적용한 결과이므로 합산하지 않는다. 방식별 상세 탈락 사유는 `percentile/candidates.csv`, `mansfield/candidates.csv`에서 확인한다. 비교 실행은 모의 계좌 상태를 변경하지 않는다.

한 방식만 분석할 때는 `scan --rs-method percentile` 또는 `scan --rs-method mansfield`를 사용한다. 백테스트에서는 각각 `examples/config.json`, `examples/config-mansfield.json`을 지정하고 다른 `--out` 경로에 저장한다. Mansfield 모의 계좌를 별도로 시작하려면 아래처럼 새 상태 경로를 사용한다. 기존 계좌의 전략을 중간에 바꾸지 않는다.

```powershell
python -m trendfollow daily --config examples/config-mansfield.json --state outputs/paper-mansfield/state.json
```

이는 실행 방법 예시이며, 현재 Mansfield 모의 계좌는 자동으로 시작하지 않았다. 기존 RS 모의 계좌는 그대로 이어서 사용할 수 있다.

### Mansfield의 비교 기준 선택

`rules.mansfield_benchmark`로 Mansfield의 기준만 바꿀 수 있다.

- `"universe"`: 기존 `universe.csv`의 `benchmark`를 사용한다. 현재 시장 유니버스는 KOSPI 종목에 KOSPI200 ETF(069500), KOSDAQ 종목에 KOSDAQ150 ETF(229200)를 지정했다. 원 지수 자체와는 다르다.
- `"market_index"`: `universe.csv`의 `market` 값에 따라 **KOSPI 종목은 KOSPI 종합지수, KOSDAQ 종목은 KOSDAQ 종합지수 자체**를 사용한다. `market` 열이 필요하며 코드는 `KOSPI`, `KOSDAQ`이다.

`rs_percentile`의 순위 계산과 기존 126일 초과수익 필터의 벤치마크는 그대로다. 두 방식의 결과 파일에는 `benchmark`와 `mansfield_benchmark`를 따로 표시한다. 지수 자료가 없으면 ETF로 자동 대체하지 않고 수집 필요를 알린다.

저장된 주식 일봉에 지수 일봉만 추가해 별도 파일로 저장한다. 원본 주식 파일은 보존된다. 지수 가격은 네이버 공개 차트의 **지수 포인트** 단위이며, 종목 가격의 원 단위와 구분한다. Mansfield는 상대가격을 자신의 평균으로 나누므로 일관된 단위 배율은 상쇄된다. 지수 거래량은 이 계산이나 종목 유동성 필터에 사용하지 않는다.

```powershell
python -m trendfollow index-data --prices data/market/history/clean-prices.csv --out data/market/history/prices-with-indices.csv --end 2026-11-06

python -m trendfollow compare-rs --prices data/market/history/prices-with-indices.csv --universe data/market/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config.json --mansfield-benchmark market_index --out outputs/rs-comparison-index
```

지수 기준 Mansfield용 설정은 `examples/config-mansfield-index.json`이다. 백테스트에도 이 설정과 지수가 포함된 가격 파일을 사용하면 된다. 기존 방식은 `examples/config-mansfield.json`에 보존했다. 종합지수 기준 모의 운용을 별도로 시작하는 명령은 다음과 같다. 이 모의 계좌는 아직 자동으로 시작하지 않았다.

```powershell
python -m trendfollow daily --config examples/config-mansfield-index.json --data-dir data/market/history-index --state outputs/paper-mansfield-index/state.json
```

`daily`는 해당 설정을 읽어 종합지수 일봉도 수집하고 휴장·누락을 확인한다. 후보 주식과 기존 ETF 벤치마크는 증권사와 대조하지만, 종합지수는 별도 공개 차트 원천으로 사용한다. 주식 API에 지수 코드를 전달하거나 지수 거래량을 주식 거래량과 비교하지 않는다. 지수 원천을 증권사와 교차 검증했다고 주장하지 않는다.

## 파동 기반 셋업과 트리거

새로운 `rules.entry_method: "setup"`을 추가했다. 기존 기본값 `"breakout"`은 55일 고가 돌파 방식이다. 셋업 방식은 55일 고가를 진입 조건으로 사용하지 않으며, 선행 상승 파동의 고점을 저항선으로 사용한다. 두 방식 모두 기존의 주봉 추세·RS 또는 Mansfield·유동성·위험 관리를 적용한다. 30주 이동평균은 여전히 큰 추세를 근사하는 별도 가정이며, 조정이 30주여야 한다는 뜻은 아니다.

### 확정한 원칙과 구현 가정

영상에서 확인한 원칙은 상승 → 건전한 조정 → 돌파의 순서, 상승 폭의 약 50% 이내 반납, 돌파 거래량과 업종 동반 강세다. 아래 파동 식별·축소·업종 판단 수치는 **프로그램을 재현 가능하게 만들기 위한 검증용 가정**이며 영상의 확정 공식이 아니다.

1. **선행 상승 파동**: 저점에서 고점까지 상승 폭이 기본 4ATR 이상이면 상승 파동 후보로 본다(`setup_min_rise_atr`). 고점에서 종가가 기본 2ATR 이상 물러나야 조정을 확인한다(`setup_reversal_atr`). 확인 후에만 해당 고점이 셋업 저항선이 된다. 파동의 고점 날짜와 실제 조정 확인 날짜를 따로 저장한다. 미래 봉을 확인하고 과거 신호를 소급 생성하지 않는다.
2. **조정 깊이**: `(고점 - 조정 저점) / (고점 - 상승 시작 저점) <= 0.5`. 100→150 상승의 허용 하단은 125이며 75가 아니다. 조정 한도를 넘거나 트리거 봉에서 한도를 깨면 셋업을 무효화한다.
3. **기간**: 정확히 N일 또는 N주를 요구하지 않는다. 조정 기간은 `setup_base_bars`로 관찰한다. 축소를 비교할 수 있도록 최소 네 개의 조정 봉이 필요하지만 이는 전·후반 각각 두 개 이상의 관측치를 확보하기 위한 하한이다. 조정의 최대 기간 제한은 없다.
4. **조정 중 축소**: 트리거 당일을 제외한 조정 구간을 전반부·후반부로 나눈다. 후반 평균 거래량 / 전반 평균 거래량, 후반 평균 TR/종가 / 전반 평균 TR/종가가 각각 0.8 이하이면 축소로 본다. 이는 매물 소화·변동성 축소의 대용 지표이며, 실제 매물 소화나 여러 번의 VCP 수축을 확인했다는 뜻은 아니다. 반으로 나누는 방법·0.8 기준은 변경 가능한 가정이다.
5. **트리거**: 종가가 셋업 저항선을 처음 넘어야 하며 기존 거래량 기준(직전 20일 평균의 기본 1.5배)도 통과해야 한다. 한 셋업의 첫 돌파는 다른 필터에서 탈락해도 소비된 것으로 처리한다. 다음 신호에는 새 상승·조정 과정이 필요하다.
6. **업종 동반 강세**: 후보 자신을 제외하고 같은 업종의 당일 유효 종목이 기본 3개 이상 필요하다. 그중 60% 이상이 상승하고, 동료 종목의 평균 당일 벤치마크 초과수익이 양수여야 한다. 동일 날짜의 종가로 계산하므로 신호는 다음 거래일에 실행한다. 업종 전체의 모든 종목이 아닌 현재 연구 유니버스에 포함된 동료 기준이며, 기관·외국인 매수 자체를 확인한 것은 아니다.

무거래량 봉이나 확인 가능한 거래일 누락을 통과해 파동을 연결하지 않는다. 기본 손절은 여전히 2ATR이며, 영상의 재량적 셋업·파동 해석이나 피라미딩 전체를 완성한 것은 아니다.

### 비교와 실행

`examples/config-setup.json`은 수익률 순위 RS + 셋업, `examples/config-setup-mansfield-index.json`은 종합지수 기준 Mansfield + 셋업 설정이다. 기존 설정·모의 계좌는 자동 변경하지 않았다.

```powershell
# 같은 계좌를 두 진입 방식에 각각 적용해 비교한다. 두 방식의 수량을 합산하지 않는다.
python -m trendfollow compare-entry --prices data/market/history/clean-prices.csv --universe data/market/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config-setup.json --out outputs/entry-comparison

# 셋업 진입 조건은 동일하게 두고 RS / Mansfield만 비교할 수도 있다.
python -m trendfollow compare-rs --prices data/market/history/prices-with-indices.csv --universe data/market/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config-setup-mansfield-index.json --out outputs/setup-rs-comparison

# 새로운 셋업 모의 계좌를 시작하는 방법. 기존 계좌와 상태 경로를 분리한다.
python -m trendfollow daily --config examples/config-setup.json --state outputs/paper-setup/state.json
```

`outputs/entry-comparison/comparison.csv`에서 `signal_breakout`, `signal_setup`, 방식별 수량·탈락 사유를 비교한다. 셋업 단계는 `setup_phase`, `setup_ready`, `setup_trigger`, `setup_entry`로 구분한다. `setup_entry`는 구조·축소·돌파 조건이며, 최종 `signal_setup`에는 주봉·RS·돌파 거래량·업종·손절 조건까지 포함된다. `setup_invalidated`, `setup_retracement`, `setup_resistance`, `setup_wave_start`, `setup_peak_date`, `setup_confirmed_at`, 거래량·변동성 축소비와 `sector_*` 열로 근거를 확인한다.

축소 기준과 동료 수·업종 상승 비율은 설정 파일에 명시했다. 후보 수를 늘리려고 실제 결과를 본 뒤 임의로 완화하지 않았다. 과거 당시 업종·상장폐지·종목 목록 문제가 남아 있어 이 규칙의 수익성 검증은 별도 작업이다.

## 인더스트리 액션용 산업 분류와 동료 종목군

산업 목록은 프로그램이 수집한다. 사용자가 종목별 목록을 직접 작성할 필요는 없다. 이 기능의 목적은 분류표 표시가 아니라 **셋업·트리거의 인더스트리 액션 검증**이다.

현재 자동 원천은 네이버가 공개 제공하는 산업 분류다. WiseIndex/WICS 직접 접속은 확인 시 실패했으므로 이 자료를 WICS 공식 분류와 동일하다고 표기하지 않는다. KRX 업종 지수 구성종목과의 교차 확인, 기업 사업보고서 개별 검토, 과거 시점 산업 구성 이력 복원도 완료된 것으로 취급하지 않는다. 시장 구분·보통주·거래 상태 등은 KIS 공개 마스터와 대조한다.

### 두 유니버스의 역할

- **매수 후보 유니버스**: 기존처럼 유동성·거래 가능 조건을 적용한 종목군이다.
- **산업 동료 유니버스**: 최근 거래대금 하한 없이 산업에 속하는 더 넓은 보통주·외국주식 종목군이다. 관리·정지·우선주·SPAC 등의 상태 조건과 유효한 KRX 시세를 검사한다. 산업 동반 상승 계산은 이 종목군으로 수행한다.

같은 산업이면 KOSPI와 KOSDAQ 종목을 함께 묶는다. 각 동료의 초과수익은 그 동료가 속한 시장의 기존 벤치마크와 비교한다. 모든 동료의 수익률을 KOSPI 벤치마크 하나로 비교하지 않는다. 후보 자신은 집계에서 제외하며, 당일 거래량·시세·거래 가능 상태가 유효한 동료만 포함한다. 그룹 내 최소 동료 수·상승 비율 기준은 기존 `sector_min_peers`, `sector_min_breadth` 설정을 사용한다.

### 수집과 분석

장마감 데이터 수집 명령:

```powershell
python -m trendfollow industry-sync
```

현재 산업 분류·구성종목과 KRX 기준의 공개 시세를 저장하고, 최근 지수 거래일을 확인해 산업 동료 수익률의 날짜를 기록한다. 이는 과거 날짜를 지정해 당시 분류를 조회하는 API가 아니다. 분류 관찰 시각과 수익률 거래일은 서로 다를 수 있다. 예를 들어 일요일에 관찰한 현재 분류를 금요일 당시의 확정 분류라고 주장하지 않는다.

확인 파일:

- `data/industries/summary.json`: 원천, 관찰 시각, 수익률 거래일, 수집 범위·누락·미완료 검증
- `data/industries/snapshots/YYYY-MM-DD/memberships.csv`: 전체 산업별 구성종목과 출처
- `data/industries/universe.csv`: 산업 분류를 연결한 매수 후보 유니버스
- `data/industries/peer-returns.csv`: 동료 종목군의 날짜별 수익률 기록
- `data/industries/unclassified.csv`, 날짜별 `conflicts.csv`: 미분류·상충 분류 기록

미분류 종목들을 공통 UNKNOWN 산업으로 묶지 않는다. 종목별 별도 키를 사용하며 동료 수를 확보하지 못한 것으로 처리한다. 상충 분류도 임의로 하나를 골라 사용하지 않는다.

기존 방식과 비교:

```powershell
python -m trendfollow compare-entry --prices data/market/history/clean-prices.csv --universe data/industries/universe.csv --account examples/account.json --as-of 2026-11-06 --config examples/config-setup.json --out outputs/industry-entry-comparison
```

이 결과의 `sector_action_scope=industry_snapshot`은 더 넓은 산업 동료 자료를 사용했다는 뜻이다. `missing_industry_snapshot`은 해당 날짜의 동료 자료가 없어 동반 상승을 확인할 수 없다는 뜻이며, 기존 후보군 집계로 조용히 대체하지 않는다. 지수의 구성종목이나 시장 전체를 빠짐없이 확보했다는 의미는 아니다.

새 산업 분류로 모의 계좌를 시작할 때는 기존 계좌와 상태 파일을 분리한다. 매 거래일 장마감에는 먼저 산업 자료를 갱신한다.

```powershell
python -m trendfollow industry-sync --refresh
python -m trendfollow daily --config examples/config-setup.json --universe data/industries/universe.csv --state outputs/paper-industry/state.json
```

산업 코드가 실제로 변경되면 전략의 산업 위험 한도와 동료 구성이 달라지므로 기존 계좌의 변경을 탐지한다. 단순 관찰 시각·출처 설명 변경만으로 계좌를 초기화하지 않는다. 동일 거래일의 과거 동료 수익률이 정정되면 이미 처리한 데이터 변경으로 감지한다. 해당 거래일의 산업 스냅샷이 없으면 `daily`는 갱신을 요구한다.

### 시장 추세에 따른 신규 진입 유닛 상한

시장 유닛 기능은 기본적으로 꺼져 있다. 별도 예제 `examples/config-setup-market-units.json`과 `examples/config-setup-mansfield-market-units.json`에서 활성화했다. 두 예제는 종목의 시장에 따라 실제 KOSPI/KOSDAQ 지수를 사용한다. `market_units_benchmark=universe`를 선택하면 기존 유니버스의 벤치마크 ETF를 사용한다. Mansfield RS 비교 기준과는 독립적인 설정이다.

초기 연구 규칙은 시장 종가가 60거래일 단순이동평균(당일 포함)을 **초과**하면 강세 상한 5유닛, 그 이하는 약세 상한 1유닛이다. 실제 신규 진입 유닛은 `min(거래 성과 유닛, 시장 상한)`이다. 5유닛 체계에서 거래당 최대 위험 1%일 때 1유닛은 계좌의 0.2% 계획 위험이다. 강세가 됐다고 성과 유닛을 자동으로 올리지 않는다. 이 상한 정책과 숫자는 검증용 가설이며 영상에서 확정한 매매 규칙이 아니다.

설정 항목은 `market_units_enabled`, `market_units_benchmark`, `market_units_ma_days`, `market_units_strong_cap`, `market_units_weak_cap`이다. 상한은 0으로도 지정할 수 있다. 비교 시세가 없으면 오류를 내며, 해당 거래일 시세나 이동평균 계산 구간이 부족하면 상한 0으로 신규 진입을 막는다. 누락된 지수 종가를 이전 날짜 값으로 채우지 않는다.

`signals.csv`와 스캔 결과의 `market_unit_benchmark`, `market_unit_close`, `market_unit_ma`, `market_unit_regime`, `market_unit_cap`에서 판단 근거를 확인한다. 스캔 수량 배분의 `effective_units`는 적용 유닛이며, 모의운용 체결 기록에도 적용 유닛과 시장 상태를 남긴다. 최종 종목 신호와 시장 상한에 따른 수량 제한은 별도다. 신호가 있어도 상한 0이면 주문 계획은 거절된다.

종가에 결정한 시장 상한을 다음 거래일 시가 진입에 적용한다. 다음 날 종가를 미리 참조하지 않는다. 기존 보유분 강제 축소나 추가매수는 하지 않고, 가격 손절·트레일링은 기존대로 작동한다. 기존 모의계좌의 전략을 자동 전환하지 않는다. 비교 실행 시 새 상태 파일을 지정해야 한다.

```powershell
python -m trendfollow daily --config examples/config-setup-market-units.json --universe data/industries/universe.csv --data-dir data/market/history-market-units --state outputs/paper-setup-market-units/state.json --verify-provider kiwoom
```

앞서 설명한 대로 장마감 산업 자료를 먼저 갱신하고 실행한다. 지수 데이터는 활성 설정에 맞춰 자동 수집한다. 새 예제 계좌는 아직 시작하지 않았다.

### 세부 사업군 보완

`examples/industry-overrides.csv` 형식으로 일부 종목을 보완할 수 있다. `symbol,industry_code,industry_name,valid_from,reason,source_url`을 기록한다. 같은 사업군으로 묶을 종목에는 시장 구분 없이 동일한 `industry_code`를 지정한다.

```powershell
python -m trendfollow industry-sync --overrides examples/industry-overrides.csv
```

수정 적용일이 아직 오지 않았으면 적용하지 않는다. 표준 분류는 `standard_industry_code/name`에 남기고, 수동 보완은 `CUSTOM:` 그룹과 수정 근거로 구분한다. 한 종목에는 한 개의 주된 인더스트리 액션 그룹을 사용하며, 여러 테마를 중복 집계하는 기능은 아직 없다. 사업보고서 URL 등을 근거로 사용자가 확인한 수정만 입력하고 프로그램이 주력 사업을 임의 추정해 분류하지 않는다.

현재 스냅샷만으로 과거 산업 동반 상승을 백테스트할 수 없다. 산업 동료 수익률이 없는 거래일을 포함한 `backtest`·모의 계좌 재생은 거절하며, 실제 당시 산업 구성 이력은 별도로 확보해야 한다.

API 근거: [KIS 주식 일봉 공식 예제](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/inquire_daily_itemchartprice), [KIS 토큰 발급 예제](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/auth/auth_token), [키움 공식 예제·스펙](https://github.com/Kiwoom-Securities/Kiwoom-REST-API), [키움 REST 소개](https://openapi.kiwoom.com/intro). 키움 `au10001`, `ka10081`은 연결된 스펙 MCP에서 계약과 공식 예제를 확인했다.
