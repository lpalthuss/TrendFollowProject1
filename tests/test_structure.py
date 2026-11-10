"""Regression checks for refactoring boundaries and shared process state."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from trendfollow import assets
from trendfollow.config import Rules
from trendfollow.operations import Operations
from trendfollow.strategy import Rules as LegacyRules
from trendfollow.ui_service import Operations as LegacyOperations
from trendfollow.observation_control import control_lock


def test_atomic_status_write_retries_temporary_reader_contention(tmp_path, monkeypatch):
    from trendfollow.observation_control import atomic_json

    target = tmp_path / "status.json"
    target.write_text('{"old": true}', encoding="utf-8")
    replace = Path.replace
    attempts = []

    def sharing_error_once(source, destination):
        attempts.append(destination)
        if len(attempts) == 1:
            assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
            raise PermissionError("temporary Windows reader")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", sharing_error_once)
    atomic_json(target, {"new": True})
    assert len(attempts) == 2
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_public_imports_remain_identical():
    assert LegacyRules is Rules
    assert LegacyOperations is Operations


def test_browser_bundle_matches_sources_and_rejects_stale_copy(tmp_path):
    source = Path(assets.__file__).with_name("web")
    shutil.copytree(source, tmp_path / "web")
    web = tmp_path / "web"
    assets.check(web)
    with (web / "src/state.js").open("a", encoding="utf-8") as f:
        f.write("\n// changed source\n")
    with pytest.raises(ValueError, match="stale"):
        assets.check(web)


def test_browser_manifest_rejects_path_escape(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"filter": "../secret.js", "browser": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="manifest"):
        assets.bundle(tmp_path)


def test_separate_processes_preserve_all_created_accounts(tmp_path):
    # Release both writers together; registry reads deliberately overlap without
    # the shared lock, making lost updates visible in the final registry.
    script = """
import json,sys,time
from pathlib import Path
from trendfollow.operations import Operations
from trendfollow.operations.accounts import WorkspaceStore
root=Path(sys.argv[1]);ops=Operations(root)
original=WorkspaceStore.registry_rows
def slow_read(self):
    rows=original(self);time.sleep(.04);return rows
WorkspaceStore.registry_rows=slow_read
print('ready',flush=True);sys.stdin.readline()
try:
    for i in range(3):ops.create_workspace({'name':sys.argv[2]+str(i)})
finally:ops.close()
"""
    children = []
    try:
        for name in ["first", "second"]:
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(tmp_path), name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            children.append(child)
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, (stdout, stderr)
        rows = json.loads(
            (tmp_path / "outputs/ui/workspaces.json").read_text(encoding="utf-8")
        )
        assert {r["name"] for r in rows} == {
            name + str(i) for name in ["first", "second"] for i in range(3)
        }
        assert len({r["id"] for r in rows}) == 6
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()


def test_account_lock_blocks_separate_process_until_owner_releases(tmp_path):
    path = tmp_path / "state.json"
    script = """
import sys
from trendfollow.observation_control import control_lock
print('ready',flush=True)
with control_lock(sys.argv[1]):print('acquired',flush=True)
"""
    child = None
    try:
        with control_lock(path):
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert child.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.2)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        assert stdout.strip() == "acquired"
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait()
