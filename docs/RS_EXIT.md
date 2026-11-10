# RS 약화 청산

선택한 상대강도 지표가 약화 기준을 연속 충족하면 장마감에 청산을 결정하고 **다음 거래일 시가에 전량 모의 청산**한다. 기본은 비활성이며 기존 가격 손절·20일 저가 채널 트레일링은 계속 작동한다. 실제 주문 기능과 시간 청산은 추가하지 않았다.

## 초기 비교 규칙

- `rs_method=percentile`: 기존 유니버스 내 126거래일 수익률 순위가 **50 미만으로 2거래일 연속**이면 청산.
- `rs_method=mansfield`: 기존에 선택한 벤치마크로 계산한 Mansfield RS가 **0 미만으로 완료 주봉 2개 연속**이면 청산.
- 기준과 같은 값(50 또는 0)은 약화가 아니다. 약하지 않은 새 관측이면 연속 횟수를 초기화한다.
- 자료 누락·계산 불가는 약화로 세지 않고 연속 횟수를 초기화한다. 기존 가격 누락 처리와 가격 손절은 별도다.

수치와 연속 횟수는 영상의 확정 규칙이 아니라 비교 검증용 가설이며 변경할 수 있다. 순위 RS는 분석 유니버스 범위에 영향을 받는다. 진입 기준과 청산 기준을 별도로 두며, 최초 예제는 RS 진입 순위 70 이상·청산 50 미만이다.

Mansfield 값은 완료 주봉 기준이므로 같은 주봉 값의 일별 반복을 새 관측으로 세지 않는다. 진입보다 오래된 주봉 값도 진입 후 약화 횟수에 포함하지 않는다. 완성된 주봉이 처음 반영된 장마감에 한 번 판단하며, 확인 횟수 2는 이 경우 2거래일이 아니라 서로 다른 2개 주봉이다.

## 설정

```json
{
  "rs_exit_enabled": true,
  "rs_exit_percentile": 50,
  "rs_exit_mansfield": 0,
  "rs_exit_confirmations": 2
}
```

위 항목은 설정 파일의 `rules` 안에 둔다. 지표는 해당 계좌의 `rs_method`를 따르며 그 지표의 청산 기준만 사용한다. Mansfield의 KOSPI200·KOSDAQ150 ETF 또는 KOSPI·KOSDAQ 지수 선택과 기존 계산식은 그대로다. 일별 스캔 결과의 약화 표시는 보유 포지션에 대한 연속 확인 완료나 체결을 의미하지 않는다.

별도 비교 예제:

- `examples/config-setup-rs-exit.json`: RS 순위 + 셋업·산업 동반 상승 + RS 청산.
- `examples/config-setup-mansfield-rs-exit.json`: 시장별 KOSPI/KOSDAQ Mansfield RS + 셋업·산업 동반 상승 + RS 청산.

새 예제의 모의계좌는 아직 시작하지 않았다. 기존 계좌는 자동 전환하지 않으며, 설정 변경 감지에 따라 활성 설정으로 비교할 때는 별도 상태 파일을 사용한다.

```powershell
python -m trendfollow industry-sync --refresh
python -m trendfollow daily --config examples/config-setup-rs-exit.json --universe data/industries/universe.csv --data-dir data/market/history --state outputs/paper-setup-rs-exit/state.json --verify-provider kiwoom
python -m trendfollow daily --config examples/config-setup-mansfield-rs-exit.json --universe data/industries/universe.csv --data-dir data/market/history-mansfield --state outputs/paper-setup-mansfield-rs-exit/state.json --verify-provider kiwoom
```

기존처럼 거래일 장마감 확정 후 순서대로 실행한다. 장중 WebSocket의 ON/OFF와 독립적이다. 이 규칙은 장마감 스캔·백테스트·로컬 모의운용 경로이며, 실시간 시세로 RS를 다시 계산해 장중 매도하는 기능은 아니다.

## 청산 처리와 기록

연속 횟수 충족 시 보유 포지션에 `rs_exit_pending`을 저장한다. 지표 값·기준·관측일·결정일·확인 횟수를 함께 보존하며, 재실행과 재개 시 횟수와 미체결 지시를 이어간다. 한 번 청산이 결정되면 이후 값이 회복해도 지시를 자동 취소하지 않는다.

다음 거래일 거래정지·시세 누락·거래량 0 또는 단일 가격 봉에서는 체결을 가정하지 않는다. 청산 사유를 유지하고 다음 가능한 시가에 처리한다. 시가가 가격 손절선 이하이면 `gap_stop`을 우선 사유로 사용하되 RS 청산 결정 이력도 남긴다. 이미 보유분을 일중 가격 손절로 청산했다면 같은 날 종가에 새 RS 청산을 만들지 않는다. RS 청산 후 같은 종목의 당일 신규 진입은 차단한다.

- `signals.csv`: `rs_exit_value`, `rs_exit_threshold`, `rs_exit_status`, `rs_exit_data_ready`, `rs_exit_observation_date`로 개별 관측을 확인한다.
- `state.json`: 보유 종목의 `rs_weak_count`, `rs_last_observation`, 확인 후 `rs_exit_pending`.
- `events.csv`: `rs_exit_scheduled`와 그 근거.
- `orders.csv`: RS 청산의 `side=sell`, 체결 또는 보류 상태·사유.
- `trades.csv`: `rs_weakness_percentile` 또는 `rs_weakness_mansfield` 청산 사유와 RS 결정 근거. 갭 손절이 먼저면 주 사유는 `gap_stop`이다.

청산 수수료·슬리피지와 거래 성과에 따른 유닛 처리는 기존 모의운용 규칙을 따른다. 보유가 끝나지 않은 마지막 거래일에 강제 종가 청산하지 않는다. 최초 계좌 준비는 보유 0개에서 시작하므로 RS 약화 값이 있다고 즉시 가상 매도하지 않는다.

검증은 합성·통제 사례의 다음 시가 체결, 일별·주별 확인 횟수, 누락·동일 기준값 처리, 미체결 유지, 가격 손절 우선순위, 재실행·재개와 미래 값 비참조를 대상으로 했다. 새 전략의 시장 전체 수익성을 검증한 결과는 아니다. 시간 청산은 TODO로 유지한다.
