"""Local cross-process observation switch. Never kills unrelated processes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

DEFAULT_CONTROL = 'outputs/intraday/control.json'


class ObservationStopped(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(uuid.uuid4().hex[:12]+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()


@contextmanager
def control_lock(path):
    """OS locks are released if a writer exits; no stale lock-directory guessing."""
    path = Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_name(path.name+'.lock').open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        deadline = time.monotonic()+5
        while True:
            try:
                handle.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline:
                    raise ValueError('장중 제어 설정 잠금을 얻지 못했습니다. 잠시 후 재시도하세요.') from None
                time.sleep(.05)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def read_control(path=DEFAULT_CONTROL):
    path = Path(path)
    if not path.exists():return {'version':1,'enabled':True,'stop_epoch':'initial'}
    try:value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):
        raise ValueError('장중 제어 파일을 읽을 수 없습니다. 감시를 중단합니다.') from None
    if value.get('version')!=1 or not isinstance(value.get('enabled'),bool) or not isinstance(value.get('stop_epoch'),str):
        raise ValueError('장중 제어 설정 형식이 유효하지 않습니다.')
    return value


def worker_directory(path):
    path = Path(path)
    return path.with_name(path.name+'.workers')


def workers(path):
    result=[]
    for file in sorted(worker_directory(path).glob('*.json')):
        try:
            value=json.loads(file.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or not isinstance(value.get('worker_id'),str) or not isinstance(value.get('connection_closed'),bool):raise ValueError()
            result.append(value)
        except (ValueError,OSError):
            result.append({'worker_id':file.stem,'phase':'unconfirmed','connection_closed':False})
    return result


def control_status(path=DEFAULT_CONTROL):
    value=read_control(path)
    pending=[w for w in workers(path) if not w.get('connection_closed',False)]
    status=('on_active_or_unconfirmed' if pending else 'on_idle') if value['enabled'] else ('stop_pending' if pending else 'off_confirmed')
    return {'status':status,'enabled':value['enabled'],'control_file':str(Path(path).resolve()),
            'active_or_unconfirmed':pending,'orders_sent':0}


def set_observation(enabled, path=DEFAULT_CONTROL, wait_seconds=10):
    import math
    if not isinstance(enabled,bool) or isinstance(wait_seconds,bool) or not math.isfinite(wait_seconds) or not 0<=wait_seconds<=60:
        raise ValueError('종료 확인 대기는 0~60초여야 합니다.')
    with control_lock(path):
        value=read_control(path)
        value.update(enabled=enabled,updated_at=utc_now())
        if not enabled:value['stop_epoch']=uuid.uuid4().hex
        atomic_json(path,value)
        targets={w['worker_id'] for w in workers(path) if not w.get('connection_closed',False)}
    deadline=time.monotonic()+wait_seconds
    while True:
        current=read_control(path)
        pending=[w for w in workers(path) if w.get('worker_id') in targets and not w.get('connection_closed',False)]
        if enabled or current['enabled'] or not pending or time.monotonic()>=deadline:break
        time.sleep(.1)
    status=(('on_enabled' if current['enabled'] else 'superseded_by_off') if enabled
            else 'superseded_by_on' if current['enabled'] else 'stop_pending' if pending else 'off_confirmed')
    return {'status':status,'enabled':current['enabled'],'control_file':str(Path(path).resolve()),
            'pending_workers':pending,'orders_sent':0}


class ControlledObservation:
    poll_seconds=.25

    def __init__(self,path,output):
        self.path=Path(path);self.output=str(Path(output).resolve())
        self.worker_id=uuid.uuid4().hex[:16]
        self.marker=worker_directory(path)/(self.worker_id+'.json')
        self.socket=None
        self.registered=False
        self.last_check=self.last_heartbeat=float('-inf')
        self.phase='starting'

    def publish(self,closed=False,reason=None):
        value={'worker_id':self.worker_id,'pid':os.getpid(),'output':self.output,
               'phase':self.phase,'updated_at':utc_now(),'connection_closed':closed}
        if reason:value['reason']=reason
        atomic_json(self.marker,value)

    def start(self):
        with control_lock(self.path):
            value=read_control(self.path)
            if not value['enabled']:return False
            if not self.path.exists():atomic_json(self.path,value)
            self.epoch=value['stop_epoch']
            self.publish()
            self.registered=True
        self.check(force=True)
        return True

    def check(self,force=False):
        now=time.monotonic()
        if force or now-self.last_check>=self.poll_seconds:
            if not self.path.exists():raise ValueError('장중 제어 파일이 없어 감시를 중단합니다.')
            value=read_control(self.path)
            self.last_check=now
            if not value['enabled'] or value['stop_epoch']!=self.epoch:
                self.phase='stopping';self.publish(reason='observation_off')
                raise ObservationStopped()
        if now-self.last_heartbeat>=5:
            self.publish();self.last_heartbeat=now

    def set_socket(self,socket):
        self.socket=socket
        self.phase='connected'
        self.publish()

    def finish(self):
        if not self.registered:return
        if self.socket is not None:
            try:self.socket.close()
            except Exception:
                self.phase='close_unconfirmed';self.publish(reason='socket_close_failed');return
        self.phase='closed';self.publish(closed=True)
