"""Shared UI job records and OS leases; a browser connection never owns a job."""
from pathlib import Path
import json
import os
import time


class Lease:
    def __init__(self,handle):self.handle=handle

    def release(self):
        if self.handle is None:return
        handle=self.handle;self.handle=None
        try:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally:handle.close()


def acquire_lease(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b')
    if handle.tell()==0:handle.write(b'0');handle.flush()
    try:
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return Lease(handle)
    except OSError:
        handle.close();return None


def save_job(path,job):
    from .observation_control import atomic_json
    # A short Windows reader can temporarily prevent os.replace().
    for attempt in range(10):
        try:
            atomic_json(path,job);return
        except PermissionError:
            if attempt==9:raise
            time.sleep(.02)


def load_jobs(home,instance_id):
    result=[]
    for file in sorted((Path(home)/'jobs').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)[-100:]:
        try:
            job=json.loads(file.read_text(encoding='utf-8'))
            if job['status'] in {'queued','running'}:
                lease=acquire_lease(file.with_suffix('.lock'))
                if lease is not None:
                    try:
                        # Re-read after taking ownership: the worker may have just completed.
                        job=json.loads(file.read_text(encoding='utf-8'))
                        if job['status'] in {'queued','running'}:
                            if job.get('owner_id'):
                                job.update(status='interrupted',error='실행 프로세스가 종료되었습니다. 계좌 처리일과 결과를 확인하세요. 자동 재실행하지 않습니다.')
                                save_job(file,job)
                            else:
                                job.update(status='unconfirmed',error='이전 버전 작업의 실행 여부를 확인할 수 없습니다. 다른 서버가 실행 중일 수 있으므로 계좌와 결과를 확인하세요.')
                    finally:lease.release()
            result.append(job)
        except (OSError,ValueError,KeyError):continue
    return result
