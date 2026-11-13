"""KIS regular and NXT after-market quote subscriptions. No account channels."""
from datetime import datetime
from contextlib import ExitStack
import json
import math
from pathlib import Path
import re
import time

from .brokers import BrokerError, Credentials, QuoteClient
from .intraday import IntradayMonitor, KST, finite, stamp
from .intraday_sampling import ObservationSession, validate_sampling
from .connection_health import ConnectionHealth, ReceiveIdleTimeout
from .krx_sessions import observation_market, require_previous_close
from .observation_control import (DEFAULT_CONTROL, ControlledObservation, ObservationStopped,
                                  control_status, set_observation)

WS_URL = {'live':'ws://ops.koreainvestment.com:21000/tryitout',
          'paper':'ws://ops.koreainvestment.com:31000/tryitout'}


def subscription(approval, symbol, market='krx'):
    tr_id = 'H0UNCNT0' if market == 'nxt_after' else 'H0STCNT0'
    return {'header':{'approval_key':approval,'custtype':'P','tr_type':'1','content-type':'utf-8'},
            'body':{'input':{'tr_id':tr_id,'tr_key':symbol}}}


def parse_trades(raw):
    """Official 47-field H0STCNT0 schema, including batched trades."""
    parts = raw.split('|',3)
    if len(parts) != 4 or parts[0] != '0' or parts[1] not in {'H0STCNT0','H0UNCNT0'}:
        raise ValueError('지원하지 않는 KIS 실시간 시세 패킷입니다.')
    try:
        count = int(parts[2]); fields = parts[3].split('^')
        if not 1 <= count <= 1000 or len(fields) != 47*count:
            raise ValueError()
        ticks = []
        for i in range(count):
            f = fields[i*47:(i+1)*47]
            if parts[1] == 'H0UNCNT0' and f[46] != '3':
                continue  # Integrated feed can contain non-after trades.
            day = datetime.strptime(f[33]+f[1],'%Y%m%d%H%M%S').replace(tzinfo=KST)
            price, change = finite(f[2],True), abs(finite(f[4]))
            if f[3] in {'1','2'}: previous = price-change
            elif f[3] in {'4','5'}: previous = price+change
            elif f[3] == '3' and change == 0: previous = price
            else: raise ValueError()
            volume = finite(f[13]); prior_volume = finite(f[41] or 0)
            if volume < 0 or prior_volume < 0: raise ValueError()
            ticks.append({'symbol':f[0],'timestamp':day.isoformat(),'price':price,
                'previous_close':finite(previous,True),'day_low':finite(f[9],True),
                'cumulative_volume':volume,'market':'nxt_after' if parts[1]=='H0UNCNT0' else 'krx',
                'volume_vs_previous_same_time':volume/prior_volume if prior_volume > 0 else None})
        return ticks
    except (ValueError,IndexError,OverflowError):
        raise ValueError('KIS 체결 패킷의 필드·수치·거래일 형식이 다릅니다.') from None


def watch(plan_path=None, output=None, env_file='.env', seconds=300, probe=False,
          evaluation_seconds=5, tick_storage='summary', idle_timeout=120,
          control_file=DEFAULT_CONTROL, observation=None, stop_wait_seconds=10):
    if observation=='status':return control_status(control_file)
    if observation=='off':return set_observation(False,control_file,stop_wait_seconds)
    if observation not in {None,'on'}:raise ValueError('관측 설정은 on/off/status여야 합니다.')
    if plan_path is None or output is None:raise ValueError('관측 시작에는 --plan과 --out이 필요합니다.')
    if observation=='on':set_observation(True,control_file)
    worker=ControlledObservation(control_file,output)
    try:
        if not worker.start():
            return {'status':'skipped_observation_disabled','control_file':str(worker.path.resolve()),'orders_sent':0}
        return _watch(plan_path,output,env_file,seconds,probe,evaluation_seconds,tick_storage,idle_timeout,worker)
    except ObservationStopped:
        return {'status':'stopped_by_off','orders_sent':0}
    finally:worker.finish()


def _watch(plan_path, output, env_file, seconds, probe,evaluation_seconds,tick_storage,idle_timeout,worker):
    """Bounded run, sanitized logs, reconnect gaps require all new quotes."""
    try:
        import websocket
    except ImportError:
        raise ValueError('장중 기능 의존성을 설치하세요: python -m pip install -e ".[intraday]"') from None
    if not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise ValueError('수집 시간은 0 초과 86400초 이하여야 합니다.')
    plan_bytes = Path(plan_path).read_bytes()
    plan = json.loads(plan_bytes)
    monitor = IntradayMonitor(plan)
    validate_sampling(evaluation_seconds,tick_storage,plan['max_quote_age_seconds'])
    health = ConnectionHealth(idle_timeout)
    if plan.get('synthetic'):
        raise ValueError('합성 감시 계획은 재생 전용이며 실시간 구독에 사용할 수 없습니다.')
    now = datetime.now(KST)
    if not probe and (now.weekday() >= 5 or str(now.date()) != plan['session']):
        return {'status':'skipped_outside_planned_session','orders_sent':0}
    if not probe and observation_market(now) is None:
        return {'status':'skipped_outside_observation_session','orders_sent':0}
    if not probe:
        require_previous_close(plan['session'], plan['as_of'])
    root = Path(output); root.mkdir(parents=True,exist_ok=False)
    (root/'plan.json').write_bytes(plan_bytes)
    summary = {'mode':'connection_probe' if probe else 'live_observation_only',
        'provider':'kis','quote_ticks':0,
        'market':observation_market(now) if not probe else 'krx',
        'watch_mode':plan.get('watch_mode','industry'),
        'subscriptions_requested':len(plan['subscriptions']),
        'subscription_acks':0,'reconnections':0,'idle_reconnections':0,
        'orders_sent':0,'status':'starting','connection_state':'disconnected'}
    summary.update(evaluation_seconds=evaluation_seconds,tick_storage=tick_storage)
    import hashlib
    summary['plan_sha256'] = hashlib.sha256(plan_bytes).hexdigest()
    sampler = None
    def save():
        summary.update(health.snapshot(time.monotonic()))
        if sampler:
            summary['latest_target_quotes'] = {
                symbol: {**quote, 'planned_previous_close':plan['targets'][symbol]['previous_close'],
                         'price_basis_mismatch':symbol in monitor.basis_mismatch}
                for symbol,quote in monitor.latest_target_quotes.items()
            }
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    ws = None
    try:
        worker.check(force=True)
        summary['stage'] = 'credentials'
        credentials = Credentials.load('kis',Path(env_file))
        summary['environment'] = credentials.environment
        summary['stage'] = 'approval_request'
        client = QuoteClient(credentials)
        body,_ = client._send('POST','/oauth2/Approval',json={
            'grant_type':'client_credentials','appkey':credentials.app_key,'secretkey':credentials.secret})
        worker.check(force=True)
        approval = body.get('approval_key')
        if not isinstance(approval,str) or not approval:
            raise BrokerError('KIS 웹소켓 접속키 발급 결과를 확인할 수 없습니다.')
        summary['approval_issued'] = True
        deadline = time.monotonic()+seconds
        with ExitStack() as stack:
            ticks = stack.enter_context((root/'ticks.jsonl').open('w',encoding='utf-8'))
            events = stack.enter_context((root/'events.jsonl').open('w',encoding='utf-8'))
            connection_events = stack.enter_context((root/'connection-events.jsonl').open('w',encoding='utf-8'))
            def record(sink,value):
                sink.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n');sink.flush()
            def emit(values):
                for value in values:
                    record(events,value)
                    print(json.dumps(value,ensure_ascii=False,allow_nan=False),flush=True)
            def connection_event(kind, reason=None):
                value = {'kind':kind,'timestamp':datetime.now(KST).isoformat(),
                    **health.snapshot(time.monotonic()),'connection_state':summary['connection_state']}
                if reason is not None: value['reason'] = reason
                record(connection_events,value)
            last_health_save = time.monotonic()
            def refresh_health():
                nonlocal last_health_save
                if time.monotonic()-last_health_save >= 5:
                    save()
                    last_health_save = time.monotonic()
            if not probe:
                sampler = ObservationSession(monitor,datetime.now(KST).isoformat(),
                    lambda value: record(ticks,value),emit,evaluation_seconds,tick_storage)
                def finish_sampling():
                    at=datetime.now(KST).isoformat()
                    try:worker.check(force=True)
                    except ObservationStopped:
                        summary['status']='stopped_by_off'
                        sampler.reset_connection(at)
                    except ValueError:
                        sampler.reset_connection(at)
                    sampler.finish(at)
                stack.callback(finish_sampling)
            while time.monotonic() < deadline:
                wall_now = datetime.now(KST)
                if not probe and (str(wall_now.date()) != plan['session'] or
                                  wall_now.hour >= 20):
                    break
                current_market = observation_market(wall_now) if not probe else 'krx'
                if current_market is None:
                    worker.check(force=True)
                    time.sleep(min(1,max(0,deadline-time.monotonic())))
                    continue
                try:
                    worker.check(force=True)
                    summary['stage'] = 'websocket_connect'
                    ws = websocket.create_connection(WS_URL[credentials.environment],timeout=5,redirect_limit=0)
                    worker.set_socket(ws)
                    worker.check(force=True)
                    if not probe: ws.settimeout(min(1,evaluation_seconds,idle_timeout))
                    summary['stage'] = 'quote_subscription'
                    summary['market'] = current_market
                    if sampler: sampler.reset_connection(datetime.now(KST).isoformat())
                    acknowledged = set()
                    pending = iter(plan['subscriptions'])
                    awaiting = next(pending)
                    summary['subscription_acks'] = 0
                    summary['connection_state'] = 'awaiting_messages'
                    ws.send(json.dumps(subscription(approval,awaiting,current_market)))
                    health.connected(time.monotonic(),datetime.now(KST).isoformat())
                    connection_event('connected')
                    save()
                    while time.monotonic() < deadline:
                        worker.check()
                        if not probe and observation_market(datetime.now(KST)) != current_market:
                            summary['connection_state'] = 'market_transition'
                            connection_event('market_transition')
                            break
                        try:
                            raw = ws.recv()
                        except websocket.WebSocketTimeoutException:
                            worker.check(force=True)
                            at = datetime.now(KST).isoformat()
                            if sampler: sampler.clock(at)
                            refresh_health()
                            if time.monotonic() >= deadline: break
                            if health.expired(time.monotonic()):
                                summary['connection_state'] = 'unresponsive'
                                connection_event('unresponsive','receive_idle_timeout')
                                raise ReceiveIdleTimeout()
                            continue
                        worker.check()
                        if not raw: raise websocket.WebSocketConnectionClosedException()
                        if not isinstance(raw,str):
                            raise BrokerError('지원하지 않는 KIS 이진 응답입니다.')
                        if raw.startswith('{'):
                            if sampler: sampler.clock(datetime.now(KST).isoformat())
                            control = json.loads(raw)
                            header = control.get('header',{})
                            health.received(time.monotonic(),datetime.now(KST).isoformat(),
                                'pingpong' if header.get('tr_id')=='PINGPONG' else 'message')
                            summary['connection_state'] = 'active'
                            refresh_health()
                            if header.get('tr_id') == 'PINGPONG':
                                ws.pong(raw.encode('utf-8'))
                                connection_event('pingpong_replied')
                                continue
                            response = control.get('body',{})
                            if response.get('rt_cd') != '0':
                                code = response.get('msg_cd')
                                if isinstance(code,str) and re.fullmatch(r'[A-Z]{4}\d{4,5}',code):
                                    summary['kis_error_code'] = code
                                summary['failure_reason'] = ('subscription_limit' if code == 'OPSP0008' else 'subscription_rejected')
                                if awaiting is not None:
                                    summary['rejected_symbol'] = awaiting
                                raise BrokerError('KIS 실시간 구독이 거절됐습니다.')
                            tr_id = subscription(approval,awaiting,current_market)['body']['input']['tr_id'] if awaiting is not None else None
                            if awaiting is not None and header.get('tr_id') == tr_id and header.get('tr_key') == awaiting:
                                acknowledged.add(awaiting)
                                summary['subscription_acks'] = len(acknowledged)
                                awaiting = next(pending,None)
                                save()
                                if awaiting is not None:
                                    time.sleep(.1)
                                    worker.check()
                                    ws.send(json.dumps(subscription(approval,awaiting,current_market)))
                            if probe and len(acknowledged) == len(plan['subscriptions']):
                                summary['status'] = 'subscriptions_acknowledged_no_tick_validation'
                                return summary
                            continue
                        parsed = parse_trades(raw)
                        health.received(time.monotonic(),datetime.now(KST).isoformat(),'trade')
                        summary['connection_state'] = 'active'
                        refresh_health()
                        for tick in parsed:
                            received = datetime.now(KST).isoformat()
                            summary['quote_ticks'] += 1
                            # Probe mode never feeds another trading day's data into a plan.
                            if not probe:
                                sampler.tick(tick,received)
                    if awaiting is not None and (probe or observation_market(datetime.now(KST)) == current_market):
                        summary['failure_reason'] = 'subscription_incomplete'
                        raise BrokerError('KIS 실시간 구독 승인이 완료되지 않았습니다.')
                    summary['status'] = 'completed' if summary['quote_ticks'] else 'no_trade_ticks_received'
                except (websocket.WebSocketException,OSError,ReceiveIdleTimeout) as error:
                    summary['reconnections'] += 1
                    summary['status'] = 'connection_gap'
                    reason = 'receive_idle_timeout' if isinstance(error,ReceiveIdleTimeout) else 'connection_error'
                    if reason == 'receive_idle_timeout': summary['idle_reconnections'] += 1
                    summary['last_disconnect_reason'] = reason
                    summary['connection_state'] = 'reconnecting'
                    connection_event('connection_gap',reason)
                    at = datetime.now(KST).isoformat()
                    if sampler: sampler.reset_connection(at)
                    save()
                    if ws is not None: ws.close(); ws = None
                    if summary['reconnections'] >= 3:
                        raise BrokerError('KIS 실시간 연결에 반복 실패했습니다. 기록을 보존하고 종료합니다.') from None
                    time.sleep(min(2**summary['reconnections'],max(0,deadline-time.monotonic())))
                finally:
                    if ws is not None: ws.close(); ws = None
        return summary
    except ObservationStopped:
        summary['status']='stopped_by_off'
        return summary
    except (BrokerError,ValueError,KeyError,TypeError):
        summary['status'] = 'failed'
        summary.setdefault('failure_reason', {
            'credentials':'credentials_error', 'approval_request':'approval_error',
        }.get(summary.get('stage'), 'connection_error' if summary.get('last_disconnect_reason') else 'observation_failed'))
        raise BrokerError('KIS 장중 감시를 완료하지 못했습니다. 기존 모의계좌는 변경하지 않았습니다.') from None
    except KeyboardInterrupt:
        summary['status'] = 'interrupted'
        return summary
    finally:
        if ws is not None: ws.close()
        summary['connection_state'] = 'failed' if summary['status']=='failed' else 'closed'
        if sampler:
            summary.update(decision_evaluations=sampler.evaluations,journal_records=sampler.records)
        save()
