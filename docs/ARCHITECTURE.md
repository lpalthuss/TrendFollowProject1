# 소스 구조와 호환 경로

전략·계좌 처리 동작을 유지하면서 운용 서비스와 계산을 분리했다. 실행 위치는 기존 프로젝트 루트이며 `examples/`, `data/`, `outputs/`, `.env`의 위치와 설정 형식은 변경하지 않았다.

## Python

- `config.py`: 전략 Rules와 검증. 기존 `strategy.Rules` import도 유지한다.
- `indicators.py`: ATR·완료 주봉 추세·수익률·거래량·Mansfield 공통 계산.
- `signals.py`: 공통 진입 조건과 손절 제한·RS 청산·추가매수 검토 정책 연결.
- `strategy.py`: 단일 기준일 후보 선별과 이유 표시.
- `backtest.py`: 시계열 특징 구성과 연구용 체결 시뮬레이션. 선별과 같은 지표·조건을 사용한다.
- `workflow.py`: 저장된 계좌의 일별 처리와 입출력 검증.
- `operations/accounts.py`: 계좌 목록·설정 저장과 경로 검증.
- `operations/queries.py`: 화면 조회와 운용 안내.
- `operations/jobs.py`: 작업 제출·공유 기록·실행 상태 관리.
- `operations/actions.py`: 데이터 갱신·분석·모의 처리·정정 복구 실행 연결.
- `operations/intraday.py`: 장중 프로세스와 관측 제어.
- `operations/__init__.py`: 기존 Operations 메서드를 유지하는 조합 진입점. 시각은 주입 가능한 clock으로 받는다.
- `ui_service.py`: 기존 Operations import 경로의 호환 진입점.
- `ui_server.py`: HTTP 라우팅과 정적 파일 제공.

조회·계좌 저장·작업 관리 코드는 Operations를 통해 협력한다. 실제 전략 계산과 계좌 처리는 화면 코드에 포함하지 않는다.

## 프로세스 간 보호

공유 작업 기록은 작업 소유자의 OS lease를 확인한다. 다른 UI 서버를 실행했다고 살아 있는 작업을 중단 처리하지 않는다. 종가 계좌 처리는 state 경로의 OS 잠금 안에서 읽기·검증·계산·저장을 수행하며 UI·CLI가 같은 계좌를 동시에 변경하지 못한다. 계좌 목록 생성·재계산 계좌 등록도 레지스트리 잠금으로 보호한다. OS는 종료된 프로세스의 잠금을 해제한다. 잠금 대기에는 제한 시간이 있고 실패 시 재시도를 안내한다. 잠금 파일을 수동 삭제해서 우회하지 않는다.

## 브라우저

`web/src/`에는 상태·API·화면 구성·시장 후보·설정·알림·운용 순서·정정 복구 등을 나눈 소스가 있다. `web/manifest.json`이 결합 순서를 지정한다. `python -m trendfollow.assets`가 `/app.js` 한 파일을 생성한다. 배포·브라우저에서는 이 파일만 읽으므로 여러 스크립트의 로딩 순서에 의존하지 않는다. 생성 파일과 소스 일치는 통합 검증과 CI에서 검사한다. 필터는 Node 테스트에서도 같은 번들을 읽는다.

운용에는 npm이 필요 없다. Node와 Prettier는 개발 검증·서식 정리에만 사용한다. Python 배포 패키지에도 번들·원본·manifest를 포함한다.

## 보존되는 기록

문서 이동은 운용 경로를 변경하지 않는다. 계좌·입력 스냅샷·설정·지문은 자동 변환하지 않는다. 데이터 정정은 기존 [정정 검토 절차](DATA_REVISIONS.md)를 따른다. 문서의 과거 계좌 수치는 자동 갱신되는 현재 상태로 취급하지 않는다.
