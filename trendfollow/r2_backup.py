"""Publish completed paper-account files to a private R2 bucket as one manifest."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import TemporaryDirectory, TemporaryFile, mkdtemp
from threading import RLock

PREFIX = "trendfollow/v1/"
MANIFEST = PREFIX + "current.json"
ROOTS = (
    "outputs/ui/workspaces.json", "outputs/ui/accounts", "outputs/ui/favorites",
    "outputs/ui/market-analyses", "outputs/ui/analyses", "outputs/ui/candidate-universes",
    "outputs/ui/close-comparisons", "outputs/ui/intraday", "outputs/ui/volume",
    "outputs/paper", "outputs/paper-setup-rs", "outputs/paper-setup-mansfield",
    "data/market", "data/industries",
)
ACTIVE = ContextVar("account_backup", default=None)


def eligible(name):
    path = PurePosixPath(name)
    return (name == path.as_posix() and not any(c in name for c in ("\\", ":"))
            and not any(part.startswith(".") for part in path.parts)
            and any(path == PurePosixPath(root) or PurePosixPath(root) in path.parents for root in ROOTS)
            and (path.suffix in {".csv", ".json", ".gz"}))


def durable_change(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        backup = getattr(self.context, "backup", None)
        if backup is None or ACTIVE.get() is backup:
            return method(self, *args, **kwargs)
        with backup.transaction():
            return method(self, *args, **kwargs)
    return wrapped


def checkpoint_saved():
    backup = ACTIVE.get()
    if backup is not None:
        backup.publish()


def connect(root, account_id, bucket, access_key, secret_key):
    if not all((bucket, access_key, secret_key)) or not re.fullmatch("[0-9a-fA-F]{32}", account_id):
        raise ValueError("R2 설정을 확인하세요. 계정 ID와 버킷 이름, 접근 키 두 개가 필요합니다.")
    try:
        import boto3
    except ImportError:
        raise ValueError("R2 설치 구성요소가 없습니다. requirements.txt로 앱 의존성을 설치하세요.") from None
    client = boto3.client("s3", region_name="auto",
                          endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                          aws_access_key_id=access_key, aws_secret_access_key=secret_key)
    return AccountBackup(root, client, bucket)


class AccountBackup:
    def __init__(self, root, client, bucket):
        self.root = Path(root).resolve()
        self.client = client
        self.bucket = bucket
        self.etag = None
        self.files = {}
        self.cache = {}
        self.lock = RLock()
        self.error = ""

    def _local(self, name):
        path = self.root / name
        if not eligible(name) or path.resolve() != path:
            raise ValueError("R2에 허용되지 않은 자료 경로가 있습니다.")
        return path

    def initialize(self):
        # Only a missing key denotes a fresh bucket. Access or transport failures
        # must never cause an empty local workspace to replace a valid backup.
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=MANIFEST)
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") != "NoSuchKey":
                raise ValueError("R2 연결에 실패했습니다. 버킷 이름과 접근 권한을 확인하세요.") from exc
            self.publish()
            return
        try:
            with obj["Body"] as body:
                files = json.loads(body.read())
            if not isinstance(files, dict):
                raise ValueError("Invalid manifest")
            with TemporaryDirectory(dir=self.root) as tmp:
                stage = Path(tmp)
                for name, digest in files.items():
                    dest = self._local(name)
                    if not re.fullmatch("[0-9a-f]{64}", digest):
                        raise ValueError("Invalid digest")
                    if dest.is_file() and self._digest(dest) == digest:
                        continue
                    stream = self.client.get_object(Bucket=self.bucket, Key=PREFIX + "files/" + digest)["Body"]
                    local = stage / name
                    local.parent.mkdir(parents=True, exist_ok=True)
                    with stream, gzip.GzipFile(fileobj=stream) as src, local.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    if self._digest(local) != digest:
                        raise ValueError("Invalid contents")
                for src in stage.rglob("*"):
                    if src.is_file():
                        dest = self.root / src.relative_to(stage)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        src.replace(dest)
                # Local-only files must not reappear in a later backup, but may
                # contain work from before this deployment first enabled R2.
                recovery = None
                for path in self.paths():
                    relative = path.relative_to(self.root)
                    if relative.as_posix() not in files:
                        if recovery is None:
                            recovery = Path(mkdtemp(prefix=".r2-local-", dir=self.root))
                        target = recovery / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        path.replace(target)
            self.etag = obj["ETag"]
            self.files = files
        except Exception as exc:
            raise ValueError("R2 계좌 백업을 복원하지 못했습니다. 연결과 저장 자료를 확인하세요.") from exc

    def paths(self):
        for name in ROOTS:
            base = self.root / name
            for path in ([base] if base.is_file() else base.rglob("*")):
                if path.is_file() and eligible(path.relative_to(self.root).as_posix()):
                    yield self._local(path.relative_to(self.root).as_posix())

    @staticmethod
    def _digest(path):
        with path.open("rb") as src:
            return hashlib.file_digest(src, "sha256").hexdigest()

    def publish(self):
        with self.lock:
            try:
                files = {}
                known = set(self.files.values())
                for path in self.paths():
                    name = path.relative_to(self.root).as_posix()
                    stat = path.stat()
                    stamp = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
                    cached = self.cache.get(name)
                    digest = cached[1] if cached and cached[0] == stamp else self._digest(path)
                    self.cache[name] = (stamp, digest)
                    if digest not in known:
                        with TemporaryFile() as spool:
                            with path.open("rb") as src, gzip.GzipFile(fileobj=spool, mode="wb", compresslevel=1, mtime=0) as out:
                                actual = hashlib.sha256()
                                while chunk := src.read(1024 * 1024):
                                    actual.update(chunk)
                                    out.write(chunk)
                            if actual.hexdigest() != digest:
                                raise ValueError("File changed during backup")
                            spool.seek(0)
                            self.client.upload_fileobj(spool, self.bucket, PREFIX + "files/" + digest)
                        known.add(digest)
                    files[name] = digest
                if files != self.files or self.etag is None:
                    condition = {"IfMatch": self.etag} if self.etag else {"IfNoneMatch": "*"}
                    body = json.dumps(files, sort_keys=True).encode()
                    try:
                        result = self.client.put_object(Bucket=self.bucket, Key=MANIFEST, **condition, Body=body)
                    except Exception:
                        # The write can succeed while its acknowledgement is lost.
                        result = self.client.get_object(Bucket=self.bucket, Key=MANIFEST)
                        with result["Body"] as remote:
                            if remote.read() != body:
                                raise
                    self.etag, self.files = result["ETag"], files
                self.error = ""
            except Exception as exc:
                self.error = "R2 백업 미완료 · 연결 또는 다른 앱의 변경 여부를 확인한 뒤 다시 저장하세요."
                raise ValueError(self.error) from exc

    @contextmanager
    def transaction(self):
        with self.lock:
            if self.error:
                raise ValueError(self.error)
            token = ACTIVE.set(self)
            try:
                yield
                self.publish()
            finally:
                ACTIVE.reset(token)
