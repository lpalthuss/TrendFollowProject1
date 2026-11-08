"""Publish the research CSV/manifest pair with rollback and interrupted-write recovery."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

JOURNAL='.research-update.json'
FILES=('prices.csv','manifest.json')


@contextmanager
def publication_lock(root):
    with (root/'.research-publication.lock').open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        deadline=time.monotonic()+5
        while True:
            try:
                handle.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline:raise ValueError('연구 데이터 게시가 진행 중입니다. 잠시 후 다시 실행하세요.')
                time.sleep(.05)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


def write_json(path,value):
    with path.open('w',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.flush();os.fsync(handle.fileno())


def clean_stage(stage):
    # Only our known files in our verified child directory; never recursively delete.
    for name in [*FILES,*('prior-'+name for name in FILES),'journal.json','restore-prices.csv','restore-manifest.json']:
        (stage/name).unlink(missing_ok=True)
    stage.rmdir()


def recover_locked(root):
    journal=root/JOURNAL
    if not journal.exists():return
    record=json.loads(journal.read_text(encoding='utf-8'))
    stage=(root/record['stage']).resolve()
    if (stage.parent!=root.resolve() or not stage.name.startswith('.research-stage-')
            or set(record['previous'])!=set(FILES)):
        raise ValueError('연구 데이터 복구 기록을 확인하세요. 기존 파일은 변경하지 않습니다.')
    # Validate every backup before replacing any current file.
    if any(record['previous'][name] and not (stage/('prior-'+name)).is_file() for name in FILES):
        raise ValueError('연구 데이터 복구 백업이 부족합니다. 수동 확인이 필요합니다.')
    for name in FILES:
        target=root/name
        if record['previous'][name]:
            restore=stage/('restore-'+name)
            shutil.copyfile(stage/('prior-'+name),restore)
            os.replace(restore,target)
        else:target.unlink(missing_ok=True)
    journal.unlink()
    try:clean_stage(stage)
    except OSError:pass  # Restoration is complete; leftover backup cleanup can be retried manually.


def recover_research(root):
    root=Path(root)
    with publication_lock(root):recover_locked(root)


def require_published_research(prices_path):
    if (Path(prices_path).parent/JOURNAL).exists():
        raise ValueError('연구 데이터 게시가 미완료입니다. 수집을 재실행해 복구한 뒤 분석하세요.')


def publish_research(root,prices,manifest,on_publish=None):
    root=Path(root)
    with publication_lock(root):
        recover_locked(root)
        stage=root/('.research-stage-'+uuid.uuid4().hex)
        stage.mkdir()
        assert stage.resolve().parent==root.resolve()
        try:
            with (stage/'prices.csv').open('w',encoding='utf-8',newline='') as handle:
                prices.to_csv(handle,index=False);handle.flush();os.fsync(handle.fileno())
            with (stage/'prices.csv').open('rb') as handle:
                manifest['prices_sha256']=hashlib.file_digest(handle,'sha256').hexdigest()
            write_json(stage/'manifest.json',manifest)
            previous={name:(root/name).exists() for name in FILES}
            for name in FILES:
                if previous[name]:shutil.copyfile(root/name,stage/('prior-'+name))
            write_json(stage/'journal.json',dict(stage=stage.name,previous=previous))
            os.replace(stage/'journal.json',root/JOURNAL)
            try:
                for name in FILES:os.replace(stage/name,root/name)
                if on_publish is not None:on_publish()
                (root/JOURNAL).unlink()
            except BaseException:
                # If rollback also fails, preserve journal/backups for the next run.
                recover_locked(root)
                raise
        finally:
            if stage.exists() and not (root/JOURNAL).exists():
                try:clean_stage(stage)
                except OSError:pass  # Do not misreport a committed publication as failed.
