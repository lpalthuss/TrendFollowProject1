import hashlib
from io import BytesIO
import json

import pytest

from trendfollow.operations import Operations
from trendfollow.r2_backup import AccountBackup, MANIFEST, checkpoint_saved


class MissingKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class Bucket:
    def __init__(self):
        self.objects = {}
        self.fail_upload = False

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise MissingKey()
        data = self.objects[Key]
        return {"Body": BytesIO(data), "ETag": hashlib.sha256(data).hexdigest()}

    def put_object(self, Bucket, Key, Body, **condition):
        if Key in self.objects:
            assert condition == {"IfMatch": self.get_object(Bucket, Key)["ETag"]}
        else:
            assert condition == {"IfNoneMatch": "*"}
        self.objects[Key] = Body
        return {"ETag": hashlib.sha256(Body).hexdigest()}

    def upload_fileobj(self, stream, bucket, key):
        if self.fail_upload:
            raise OSError("unavailable")
        self.objects[key] = stream.read()


def boot(root, bucket):
    root.mkdir(exist_ok=True)
    backup = AccountBackup(root, bucket, "test")
    backup.initialize()
    ops = Operations(root)
    ops.backup = backup
    return ops


def test_account_and_completed_close_survive_a_clean_restart(tmp_path):
    bucket = Bucket()
    app = boot(tmp_path / "first", bucket)
    account = app.create_workspace({"name": "테스트 계좌"})
    app.accounts.set_favorite(account["id"], "005930", True)
    state = app.path(account["state"])
    history = state.parent / "2026-11-20" / "orders.csv"
    history.parent.mkdir()
    with pytest.raises(RuntimeError), app.backup.transaction():
        history.write_text("symbol,quantity\n005930,4\n", encoding="utf8")
        state.write_text('{"cash": 12345}', encoding="utf8")
        (app.home / "auth-secret.bin").write_bytes(b"private")
        checkpoint_saved()
        raise RuntimeError("interrupted after completed day")
    other = boot(tmp_path / "reboot", bucket)
    assert other.workspace(account["id"]) == account
    assert other.accounts.favorites(account["id"]) == ["005930"]
    assert other.state(account)["cash"] == 12345
    assert (other.path(account["state"]).parent / "2026-11-20/orders.csv").read_bytes() == history.read_bytes()
    assert not (other.home / "auth-secret.bin").exists()


def test_partial_upload_and_stale_writer_preserve_last_complete_manifest(tmp_path):
    bucket = Bucket()
    app = boot(tmp_path / "first", bucket)
    row = app.create_workspace({"name": "before"})
    stale = boot(tmp_path / "stale", bucket)
    complete = bucket.objects[MANIFEST]
    bucket.fail_upload = True
    with pytest.raises(ValueError, match="R2"):
        app.rename_workspace(row["id"], "after")
    assert bucket.objects[MANIFEST] == complete
    bucket.fail_upload = False
    app.backup.publish()
    with pytest.raises(ValueError, match="R2"):
        stale.rename_workspace(row["id"], "stale")
    assert boot(tmp_path / "fresh", bucket).workspace(row["id"])["name"] == "after"


def test_invalid_restore_paths_never_publish_or_replace_local_files(tmp_path):
    bucket = Bucket()
    data = json.dumps({"outputs/ui/accounts/../../../.env": "0"*64}).encode()
    bucket.objects[MANIFEST] = data
    with pytest.raises(ValueError, match="R2"):
        boot(tmp_path, bucket)
    assert bucket.objects[MANIFEST] == data
    assert not (tmp_path / ".env").exists()
