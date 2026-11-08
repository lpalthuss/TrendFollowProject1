# 장중 신호 관측

현재 단계는 KIS 실시간 체결 시세를 받아 잠정 신호를 터미널과 파일에 기록한다. 실제 주문·가상 체결·계좌 상태 변경은 하지 않는다. 기존 장마감 모의계좌와 별도 실행한다. 시간 청산은 TODO다.

## 실제 연결 ON/OFF

장중 관측을 켜고 이 명령에서 실행을 시작한다. 감시 계획은 미리 생성되어 있어야 한다.

```powershell
python -m trendfollow intraday-live --observation on --plan outputs/intraday/plan-2026-11-09.json --seconds 300 --out outputs/intraday/live-controlled-new
```

다른 터미널에서 아래 명령을 실행하면 같은 제어 파일을 사용하는 감시를 중단하고, 새 연결도 막는다. OFF·상태 확인에는 계획·출력 폴더·API 키가 필요하지 않다.

```powershell
python -m trendfollow intraday-live --observation off
python -m trendfollow intraday-live --observation status
```

공통 설정은 기본 `outputs/intraday/control.json`이다. 별도 경로를 사용하려면 시작·OFF·상태 확인에 동일한 `--control-file`을 지정한다. OFF는 같은 제어 파일에 등록된 실행을 모두 중단한다. 해당 설정과 실행 상태에는 API 키를 저장하지 않는다.

`--observation`을 생략하면 저장된 ON/OFF를 존중한다. 저장된 OFF이면 인증·접속키 발급·WebSocket 구독을 시작하지 않고 `skipped_observation_disabled`를 반환한다. 접속 확인용 `--probe`도 OFF를 우회하지 않는다. ON은 이 명령의 실행을 허용하며, 감시일·정규장 조건이 맞을 때 연결한다. 주말·장외에는 기존처럼 건너뛰며 다음 날 자동으로 시작하는 상주 서비스는 아니다. OFF로 종료한 뒤 다시 시작하려면 ON 명령에 계획과 새 출력 폴더를 지정한다.

실행 중에는 로컬 제어 파일을 확인해 OFF를 감지한다. 일반 수신 대기는 최대 1초이고 연결 종료·마지막 구간 저장 후 프로세스가 종료된다. 연결·인증 같은 진행 중인 네트워크 처리는 반환을 기다릴 수 있으므로 밀리초 단위 즉시 종료를 보장하지 않는다. OFF 전에 기록한 자료는 보존하고, 마지막 표시 상태는 판단 불가로 무효화한다. OFF 직후 ON으로 바뀌어도 기존 실행은 OFF 요청을 놓치지 않고 종료하며 새 ON 실행만 시작할 수 있다.

OFF 명령은 기본 10초 동안 등록 실행의 종료 확인을 기다린다. `--stop-wait-seconds`로 0~60초를 지정할 수 있다.

- `off_confirmed`: 등록된 대상 실행이 연결 종료를 확인했거나 실행 중인 대상이 없음.
- `stop_pending`: OFF 설정은 반영됐지만 일부 실행의 종료는 아직 확인되지 않음. 상태 확인 명령으로 다시 확인한다. 강제 프로세스 종료는 하지 않는다.
- `on_idle`: 설정은 ON이지만 등록된 미종료 실행이 없음.
- `on_active_or_unconfirmed`: 등록된 미종료 실행이 있음. 프로세스 비정상 종료로 종료 확인이 남은 경우도 포함한다.

실행 식별 정보와 종료 확인은 제어 파일 옆 `.workers` 폴더에 기록한다. 확인 없이 종료 완료로 추정하지 않는다. 이 제어 기능을 적용하기 전 구버전 프로세스나 다른 제어 파일로 실행한 연결은 해당 OFF 명령의 관리 대상이 아니므로 각각 Ctrl+C 또는 같은 제어 경로로 종료해야 한다.

이 스위치는 장중 수집만 제어한다. 장마감 분석·모의운용은 독립적으로 실행되며, 일부 관측 후 OFF했다면 종가 대조에서 이미 남은 기록을 부분 관측으로 사용할 수 있다.

2026-11-08 KIS 모의 WebSocket 실제 연결 후 별도 프로세스의 OFF로 감시 종료를 확인했다. 결과는 `stopped_by_off`, 연결 상태 `closed`, 프로세스 종료 코드 0, 제어 응답 `off_confirmed`였으며 주문은 0건이다. 현재 공통 설정은 OFF다. 거래일의 장시간 운용 검증을 대체하는 결과는 아니다.

## 구현 범위

- 전일 확정 셋업의 저항선 위에 현재가가 있는지 관측하고, 다시 내려오면 상태 변화를 기록한다. 최초 수신 때 이미 저항선 위에 있다면 실제 돌파 순간을 관측했다고 주장하지 않는다.
- 전일 기준 상승 파동 대비 허용 조정 저가를 장중에 침범하면 해당 세션의 셋업을 무효화한다.
- `--paper-state`로 전달한 전일 계좌의 보유 종목만 손절선을 감시한다. 당일 누적 저가의 손절선 접촉을 기록하며, 이것이 실제 체결을 뜻하지는 않는다.
- 감시 산업의 전체 동료 종목을 포함하고 대상 종목 자신은 산업 통계에서 제외한다. 동료별 소속 시장의 ETF 대비 당일 초과수익과 상승 종목 비율을 계산한다.
- RS·Mansfield RS와 주봉 추세는 **전일 확정 값으로 고정**한다. 실시간 RS 재계산과 RS 약화 청산은 아직 구현하지 않았다.
- 과거 같은 시간대의 여러 거래일 누적 거래량 자료가 없으므로 `entry_confirmed`는 항상 `false`다. KIS가 제공하는 전일 동시간 누적량 대비 비율은 관측만 한다. 이는 기존 일평균 대비 1.5배 조건을 장중에 검증한 것이 아니다.

`entry_watch=true`는 전일 선별 조건과 현재 돌파·산업 동반 상승을 만족하는 관찰 상태다. 매수 지시나 전체 진입 조건 확정이 아니다. 셋업 조정 거래량·변동성은 기준일 전체 봉을 포함해 다음 세션용으로 다시 계산한다. 전일에 이미 돌파해 소비된 셋업은 새 진입 셋업으로 재사용하지 않는다.

시장 상황별 투자 유닛 기능은 장마감 경로에서 사용할 수 있지만, 장중 감시는 아직 수량을 배분하지 않는다. 시장 유닛 활성 설정은 감시 계획 생성 시 거절하며 기존 비활성 비교 설정을 사용한다.

## 준비 및 실행

선택 의존성 설치:

```powershell
python -m pip install -e ".[intraday]"
```

매 거래일 전에 최신 확정 신호 파일·가격·산업 구성 자료를 준비하고 다음 감시일을 명시한다. 아래는 현재 로컬 자료로 생성한 2026-11-09 감시 계획 예다. 디지털대성은 기능 확인을 위한 감시 대상이며 매수 추천이 아니다. 해당 산업 동료와 KOSPI200·KOSDAQ150 ETF를 포함해 구독은 20개다.

```powershell
python -m trendfollow intraday-prepare --signals outputs/paper-setup-rs/2026-11-06/signals.csv --prices data/market/history/clean-prices.csv --universe data/industries/universe.csv --session 2026-11-09 --symbols 068930 --config examples/config-setup.json --out outputs/intraday/plan-2026-11-09.json --paper-state outputs/paper-setup-rs/state.json
```

11월 6일 신호와 11월 8일에 관찰한 현재 산업 분류를 사용하는 초기 관측 계획이다. 분류의 과거 시점 정확성이나 장중 성과를 검증한 것이 아니다. 다른 날짜에는 해당 거래일 자료로 새 계획을 만든다. 실제 휴장일 여부는 이 명령이 판별하지 않는다. 입력 파일 해시를 기록하고 기존 계획 파일을 덮어쓰지 않는다.

장중에 먼저 5분 동안 확인:

```powershell
python -m trendfollow intraday-live --plan outputs/intraday/plan-2026-11-09.json --seconds 300 --out outputs/intraday/live-2026-11-09-first
```

`.env`의 `KIS_ENV`와 해당 키를 사용한다. KRX 정규장 시각인 09:00~15:30 및 계획 거래일을 확인한다. 다른 날짜·주말·장외 실행은 건너뛴다. 장기 수집 시 실행 시간을 늘릴 수 있으며, 출력 폴더는 실행마다 새로 지정한다. 예약 작업은 등록하지 않았다. Ctrl+C로 종료하면 수집 결과를 남긴다.

### 판단 간격·저장량 조절

기본은 `--evaluation-seconds 5 --tick-storage summary`다. 시세는 수신할 때마다 최신 값·누적 저가·누적 거래량을 갱신하지만, 돌파·산업 동반 상승 판단과 그 상태 출력은 5초 간격으로 처리한다. 보유 종목의 손절선 접촉은 매 시세 수신 즉시 별도의 `holding_stop_state`로 기록·출력한다. 시세 신선도 만료와 연결 단절도 상태에 반영한다. 연결 단절 시에는 5초를 기다리지 않고 기존 판단을 무효화한다.

```powershell
python -m trendfollow intraday-live --plan outputs/intraday/plan-2026-11-09.json --seconds 300 --evaluation-seconds 5 --tick-storage summary --out outputs/intraday/live-summary-new
```

간격은 0 초과이며 계획의 시세 신선도 한도(현재 60초) 이하여야 한다. 판단 간격 안에서 발생했다 사라지는 짧은 돌파는 포착되지 않을 수 있다. 손절선 접촉은 별도 즉시 경로로 처리하며 이 간격 제한을 받지 않는다.

손절 기록의 시각은 수신 시세에서 접촉을 탐지한 시각이다. 누적 저가로 이전 접촉을 확인한 경우 실제 접촉 순간을 직접 관측한 것으로 보지 않는다.

- `summary`: 판단 시점마다 종목별 최신 시세를 묶어 저장한다. 손절 상태가 바뀐 원인 체결과 시세 만료 시각·연결 단절·셋업 및 가격 기준 불일치 상태도 보존한다. 요약 시점의 판단과 즉시 손절 기록을 재생할 수 있지만 모든 체결이나 판단 간격 안의 가격 경로를 복원하지는 못한다.
- `all`: 수신한 정규화 체결을 모두 저장한다. 판단 주기는 동일하므로 원시세 저장 여부가 판단 빈도를 바꾸지 않는다. 초기 실측·체결 단위 분석이 필요할 때 선택한다.

정상 종료·Ctrl+C·연결 오류 종료 시 마지막 미완료 간격도 저장하고 판단한다. `summary.json`의 `decision_evaluations`, `journal_records`, `evaluation_seconds`, `tick_storage`로 판단 횟수와 기록 방식을 확인한다. 저장 파일에는 설정 버전을 기록하며 예전 전체 체결 기록도 재생할 수 있다.

이 설정은 로컬 계산·출력·저장량을 줄인다. 서버의 WebSocket 전송량을 줄이거나 종목마다 추가 REST 조회를 수행하는 기능은 아니다. 데이터가 없는 동안의 짧은 수신 타임아웃은 로컬 대기 시간 설정이며 서버 조회 요청을 새로 만들지 않는다.

휴장 중 접속·구독 응답만 확인:

```powershell
python -m trendfollow intraday-live --plan outputs/intraday/plan-2026-11-09.json --seconds 15 --probe --out outputs/intraday/probe-new
```

`--probe`는 장중 전략 신호를 평가하지 않는다. 2026-11-08 KIS 모의 환경에서 접속키 발급과 **20개 구독 승인**을 확인했으며 체결 시세는 0개였다. 이는 거래일의 실제 체결 패킷 수신·시각·가격 정확성 검증을 대체하지 않는다.

## 관측 품질과 기록

KIS 공식 예제의 최대 40개 구독을 적용한다. 전체 산업 동료를 포함했을 때 초과하면 계획 생성을 거절한다. 여러 프로세스로 동일 계정 한도를 우회하는 기능은 없다. 작은 산업을 선택하거나 제공자 한도·다른 수집 방식을 추가 검토해야 한다.

산업 동료와 각각의 비교 ETF가 모두 신선해야 산업 판단을 확정한다. 현재 신선도 한도는 60초, 동료 커버리지는 100%다. 거래가 드물어 시세가 오래되거나 누락되면 산업 상태를 `null`(판단 불가)로 기록하며 약세로 단정하지 않는다. 기본 산업 조건은 다른 동료 최소 3개, 상승 비율 60% 이상, 평균 초과수익 양수다.

과거 시각·역순 누적 거래량·지연 시세는 최신 값으로 덮어쓰지 않는다. 다음 거래일 시세를 과거 계획에 넣지 않는다. 장중 원시세의 전일 종가와 셋업 입력의 기준 종가가 0.1%를 넘게 다르면 가격 기준 불일치로 대상 신호를 차단한다. 이 검사는 전체 과거 수정주가·기업행사를 검증한 것은 아니다.

연결이 끊기면 시세 캐시를 비우고 재구독한다. 다시 신선한 시세가 모일 때까지 판단을 보류하며, 연결 실패가 3회 누적되면 종료한다. 접속키·API 키·원본 구독 제어 응답은 파일과 터미널에 출력하지 않는다. KRX 체결 시세 `H0STCNT0`만 구독하고 계좌 주문체결 채널은 구독하지 않는다.

### 연결 무응답 감지

`--idle-timeout 120`이 기본이다. 구독 요청 전송을 마친 뒤 수신 감시를 시작하며, 체결·구독 응답·KIS `PINGPONG` 중 어떤 메시지든 수신하면 무응답 시간을 초기화한다. KIS `PINGPONG`에는 동일 내용을 Pong 프레임으로 응답한다. 클라이언트 주기 Ping이나 TCP keepalive 옵션을 추가로 설정하는 기능은 아니다.

```powershell
python -m trendfollow intraday-live --plan outputs/intraday/plan-2026-11-09.json --seconds 300 --idle-timeout 120 --out outputs/intraday/live-health-new
```

무응답 기준은 0 초과의 유한한 값이어야 한다. 120초는 공식 heartbeat 주기를 보장하는 수치가 아니라 초기 운영 설정이다. 실제 거래일 메시지 간격을 확인한 후 조정한다. 체결이 없는 저유동 종목이 있어도 다른 종목 시세나 heartbeat가 들어오면 연결 전체를 재접속하지 않는다. 개별 종목 시세는 기존의 60초 신선도 기준으로 별도 제외한다.

설정 시간 동안 모든 메시지가 없으면 수신 타임아웃을 처리할 때 무응답을 감지한다. 정상 감시의 로컬 수신 대기는 최대 1초이며, 이 대기는 추가 서버 조회 요청을 만들지 않는다. 무응답 측정은 시스템 시각 대신 단조 증가 시계를 사용한다. 구독 응답만 오고 실제 체결은 없는 경우도 구분해 기록한다.

무응답 시 현재 연결을 닫고 시세 캐시와 표시 중인 판단을 즉시 무효화한다. 기존 오류 처리와 같이 2초·4초 대기 후 재접속·전체 재구독하며, 무응답을 포함한 연결 오류가 누적 3회면 기록을 남기고 종료한다. 재접속할 때 무응답 타이머와 현재 연결의 구독 승인 수를 초기화한다. 중단·시세 만료·재접속은 시세 재생 기록에도 반영된다.

`summary.json`은 수신 중 약 5초 주기로 연결 상태를 갱신하고 종료 시 최종 값을 저장한다. 확인 항목은 다음과 같다.

- `last_message_at`, `last_pingpong_at`, `last_trade_message_at`: 실행 전체의 마지막 메시지·KIS heartbeat·체결 메시지 수신 시각. 체결이 없으면 체결 시각은 `null`이다.
- `connection_messages_received`, `receive_silence_seconds`: 현재 연결의 수신 수와 마지막 수신(또는 수신 감시 시작) 이후 경과 초. 재접속 후 이전 연결의 수신 시각으로 타이머를 계산하지 않는다.
- `connection_state`: `awaiting_messages`, `active`, `unresponsive`, `reconnecting`, 종료 후 `closed` 또는 `failed`.
- `idle_reconnections`, `last_disconnect_reason`: 무응답으로 발생한 연결 공백 수와 마지막 단절 원인.

`connection-events.jsonl`에는 연결·무응답·연결 공백·PINGPONG 응답 이력을 남긴다. 체결마다 별도의 연결 로그를 쓰지는 않는다. 무응답·heartbeat만 수신·재접속 후 과거 시세 배제·반복 실패 종료는 모의 소켓으로 검증했다. 실제 장중 장시간 연결 안정성과 heartbeat 간격은 추가 관측이 필요하다.

- `plan.json`: 실행에 사용한 감시 계획 사본.
- `summary.json`: 환경, 접속 단계, 구독 승인 수, 시세 수신 수, 연결 상태.
- `connection-events.jsonl`: 연결·무응답·연결 공백·heartbeat 응답 이력과 마지막 수신 시각.
- `ticks.jsonl`: 선택 방식에 따른 전체 체결 또는 주기별 최신 시세 묶음, 즉시 손절 판단에 필요한 기록, 연결 단절·시각·판단 주기 설정.
- `events.jsonl`: 돌파·손절·산업·진입 관찰 상태 변화와 근거. 같은 상태는 실행 내에서 중복 알리지 않는다.

## 휴장 중 재생 검증

아래 합성 예제는 저항선 상향 돌파와 재하락, 손절 접촉, 시세 만료를 포함한다. 실제 시장 시세·성과가 아니며 합성 계획은 실시간 구독에 사용할 수 없다.

```powershell
python -m trendfollow intraday-replay --plan examples/intraday-demo-plan.json --ticks examples/intraday-demo-ticks.jsonl --out outputs/intraday/demo-new
```

실제 수집한 `plan.json`과 `ticks.jsonl`도 같은 명령으로 재생할 수 있다. 감시일 시세뿐 아니라 타임아웃·재접속 기록도 반영한다. 장중 관측과 종가의 돌파·산업 유지 여부, 손절 접촉·공백을 자동 대조하는 명령은 [INTRADAY_CLOSE.md](INTRADAY_CLOSE.md)에 있다. 관측 OFF·기록 부재는 신호 없음과 구분한다.

공식 계약·예제: [KIS 실시간 KRX 체결 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/ccnl_krx/ccnl_krx.py), [접속키·구독·PINGPONG·구독 한도](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/kis_auth.py), [실전·모의 접속 주소](https://apiportal.koreainvestment.com/apiservice?keyword=%ED%95%9C%EA%B5%AD%ED%88%AC%EC%9E%90%EC%A6%9D%EA%B6%8C).
