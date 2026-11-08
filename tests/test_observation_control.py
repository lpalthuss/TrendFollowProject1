from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from trendfollow.observation_control import (ControlledObservation, ObservationStopped,
    control_status, read_control, set_observation, workers)
from trendfollow.kis_stream import watch


def test_off_prevents_credentials_and_network_even_for_probe(tmp_path,monkeypatch):
    import trendfollow.kis_stream as stream
    path=tmp_path/'c.json';set_observation(False,path)
    monkeypatch.setattr(stream.Credentials,'load',lambda *a:pytest.fail('credentials loaded while OFF'))
    for probe in [False,True]:
        result=watch('missing-plan.json',tmp_path/'out',probe=probe,control_file=path)
        assert result['status']=='skipped_observation_disabled'
    assert not (tmp_path/'out').exists()


def test_off_on_pulse_still_stops_existing_worker(tmp_path):
    path=tmp_path/'c.json';w=ControlledObservation(path,tmp_path/'out')
    assert w.start()
    try:
        pending=set_observation(False,path,wait_seconds=0)
        assert pending['status']=='stop_pending'
        set_observation(True,path)
        with pytest.raises(ObservationStopped):w.check(force=True)
    finally:w.finish()
    assert workers(path)[0]['connection_closed'] is True
    # A new worker may start after ON, while the old one must exit.
    new=ControlledObservation(path,tmp_path/'new')
    assert new.start();new.finish()


def test_malformed_or_deleted_control_fails_closed(tmp_path):
    path=tmp_path/'c.json';w=ControlledObservation(path,tmp_path/'out')
    assert w.start()
    try:
        path.write_text('{bad')
        with pytest.raises(ValueError):w.check(force=True)
        path.unlink()
        with pytest.raises(ValueError):w.check(force=True)
    finally:w.finish()


def test_status_reports_unconfirmed_worker_instead_of_false_shutdown(tmp_path):
    path=tmp_path/'c.json';w=ControlledObservation(path,tmp_path/'out');assert w.start()
    class FailedClose:
        def close(self):raise OSError('failed')
    w.set_socket(FailedClose());w.finish()
    result=set_observation(False,path,wait_seconds=0)
    assert result['status']=='stop_pending'
    assert result['pending_workers'][0]['phase']=='close_unconfirmed'
    assert control_status(path)['enabled'] is False
    w.socket=None;w.finish()


def test_off_stops_another_process(tmp_path):
    path=tmp_path/'c.json';closed=tmp_path/'closed'
    script='''
from pathlib import Path
import sys,time
from trendfollow.observation_control import ControlledObservation,ObservationStopped
w=ControlledObservation(sys.argv[1],sys.argv[2])
class Socket:
    def close(self):Path(sys.argv[2]).write_text('closed')
try:
    if not w.start():raise SystemExit(2)
    w.set_socket(Socket())
    while True:
        w.check(force=True)
        time.sleep(.05)
except ObservationStopped:pass
finally:w.finish()
'''
    child=subprocess.Popen([sys.executable,'-c',script,str(path),str(closed)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if any(w.get('phase')=='connected' for w in workers(path)):break
            if child.poll() is not None:pytest.fail(child.communicate()[1].decode(errors='replace'))
            time.sleep(.05)
        assert workers(path)
        result=set_observation(False,path,wait_seconds=3)
        assert result['status']=='off_confirmed' and not result['pending_workers']
        assert child.wait(timeout=3)==0 and closed.read_text()=='closed'
    finally:
        if child.poll() is None:child.terminate();child.wait(timeout=3)


def test_live_off_closes_socket_and_flushes_unknown_state(tmp_path,monkeypatch):
    import websocket
    import trendfollow.kis_stream as stream
    examples=Path(__file__).resolve().parents[1]/'examples'
    p=json.loads((examples/'intraday-demo-plan.json').read_text());p.pop('synthetic')
    plan=tmp_path/'p.json';plan.write_text(json.dumps(p))
    control=tmp_path/'c.json'
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.fromisoformat('2026-11-09T09:00:00+09:00')
    monkeypatch.setattr(stream,'datetime',Clock)
    monkeypatch.setattr(stream.Credentials,'load',lambda *a:stream.Credentials('kis','paper','KEY','SECRET'))
    monkeypatch.setattr(stream.QuoteClient,'_send',lambda *a,**kw:({'approval_key':'PRIVATE'},{}))
    monkeypatch.setattr(stream.time,'sleep',lambda *a:None)
    class Socket:
        closed=False
        def send(self,value):pass
        def settimeout(self,value):pass
        def recv(self):
            assert set_observation(False,control,wait_seconds=0)['status']=='stop_pending'
            raise websocket.WebSocketTimeoutException()
        def close(self):self.closed=True
    socket=Socket();monkeypatch.setattr(websocket,'create_connection',lambda *a,**kw:socket)
    out=tmp_path/'out'
    result=watch(plan,out,control_file=control,observation='on')
    assert result['status']=='stopped_by_off' and socket.closed and result['reconnections']==0
    assert set_observation(False,control,wait_seconds=0)['status']=='off_confirmed'
    events=[json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
    assert [e for e in events if e['kind']=='signal_state'][-1]['entry_watch'] is None


def test_off_during_approval_never_opens_websocket(tmp_path,monkeypatch):
    import websocket
    import trendfollow.kis_stream as stream
    examples=Path(__file__).resolve().parents[1]/'examples'
    p=json.loads((examples/'intraday-demo-plan.json').read_text());p.pop('synthetic')
    plan=tmp_path/'p.json';plan.write_text(json.dumps(p));control=tmp_path/'c.json'
    monkeypatch.setattr(stream.Credentials,'load',lambda *a:stream.Credentials('kis','paper','KEY','SECRET'))
    def approve(*a,**kw):
        set_observation(False,control,wait_seconds=0)
        return {'approval_key':'PRIVATE'},{}
    monkeypatch.setattr(stream.QuoteClient,'_send',approve)
    monkeypatch.setattr(websocket,'create_connection',lambda *a,**kw:pytest.fail('connection opened after OFF'))
    result=watch(plan,tmp_path/'out',probe=True,control_file=control,observation='on')
    assert result['status']=='stopped_by_off' and not control_status(control)['active_or_unconfirmed']


@pytest.mark.parametrize('wait',[-1,61,float('nan'),True])
def test_invalid_wait(wait,tmp_path):
    with pytest.raises(ValueError):set_observation(False,tmp_path/'c.json',wait_seconds=wait)
