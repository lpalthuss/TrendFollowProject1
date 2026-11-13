from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trendfollow.brokers import BrokerError
from trendfollow.connection_health import ConnectionHealth
from trendfollow.intraday import replay_ticks


def test_heartbeat_keeps_transport_alive_without_stock_trades():
    h=ConnectionHealth(120)
    h.connected(100,'2026-11-09T09:00:00+09:00')
    assert not h.expired(219) and h.expired(220)
    h.received(219,'2026-11-09T09:01:59+09:00','pingpong')
    assert not h.expired(338) and h.expired(339)
    state=h.snapshot(220)
    assert state['last_trade_message_at'] is None
    assert state['last_pingpong_at']=='2026-11-09T09:01:59+09:00'
    assert state['receive_silence_seconds']==1 and state['pingpongs_received']==1


def test_reconnect_arms_a_new_timer_and_keeps_last_global_message_times():
    h=ConnectionHealth(10)
    h.connected(0,'wall0');h.received(3,'trade','trade')
    assert h.expired(13)
    h.connected(14,'wall14')
    assert not h.expired(23)
    state=h.snapshot(23)
    assert state['last_trade_message_at']=='trade'
    assert state['connection_messages_received']==0 and state['messages_received']==1
    assert state['receive_silence_seconds']==9


@pytest.mark.parametrize('seconds',[0,-1,float('nan'),float('inf'),True])
def test_invalid_idle_timeout(seconds):
    with pytest.raises(ValueError):ConnectionHealth(seconds)


START=datetime.fromisoformat('2026-11-09T09:00:00+09:00')


def setup_runner(tmp_path,monkeypatch,socket_factory,start=START):
    import websocket
    import trendfollow.kis_stream as stream
    p=json.loads((Path(__file__).resolve().parents[1]/'examples/intraday-demo-plan.json').read_text())
    p.pop('synthetic')
    path=tmp_path/'plan.json';path.write_text(json.dumps(p))
    elapsed=[0.]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return start+timedelta(seconds=elapsed[0])
    monkeypatch.setattr(stream,'datetime',Clock)
    monkeypatch.setattr(stream,'time',SimpleNamespace(monotonic=lambda:elapsed[0],sleep=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds)))
    monkeypatch.setattr(stream.Credentials,'load',lambda *a:stream.Credentials('kis','paper','KEY','SECRET'))
    monkeypatch.setattr(stream.QuoteClient,'_send',lambda *a,**kw:({'approval_key':'APPROVAL'},{}))
    sockets=[]
    def connect(*a,**kw):
        s=socket_factory(len(sockets),elapsed,websocket)
        sockets.append(s)
        return s
    monkeypatch.setattr(websocket,'create_connection',connect)
    return stream,path,elapsed,sockets


class Socket:
    def __init__(self):self.sent=[];self.closed=False;self.pongs=[]
    def settimeout(self,value):self.timeout=value
    def send(self,value):self.sent.append(json.loads(value))
    def pong(self,value):self.pongs.append(value)
    def close(self):self.closed=True


def test_running_observation_switches_to_after_market_channel(tmp_path,monkeypatch):
    import websocket
    def factory(index,elapsed,websocket):
        class MarketSocket(Socket):
            acks=0
            trade_sent=False
            def recv(self):
                if len(self.sent)>self.acks:
                    symbol=self.sent[-1]['body']['input']['tr_key']
                    tr_id=self.sent[-1]['body']['input']['tr_id']
                    self.acks+=1
                    return json.dumps({'header':{'tr_id':tr_id,'tr_key':symbol},'body':{'rt_cd':'0'}})
                if index==0:
                    elapsed[0]+=3
                    raise websocket.WebSocketTimeoutException()
                if not self.trade_sent:
                    self.trade_sent=True
                    fields=['0']*47
                    for key,value in {0:'000001',1:'154001',2:'106',3:'2',4:'6',9:'100',13:'100',33:'20261109',41:'50',46:'3'}.items():fields[key]=value
                    return '0|H0UNCNT0|001|'+'^'.join(fields)
                raise KeyboardInterrupt()
        return MarketSocket()
    start=datetime.fromisoformat('2026-11-09T15:29:58+09:00')
    stream,path,elapsed,sockets=setup_runner(tmp_path,monkeypatch,factory,start)
    result=stream.watch(path,tmp_path/'out',seconds=650,control_file=tmp_path/'control.json')
    assert result['market']=='nxt_after' and result['quote_ticks']==1
    assert result['reconnections']==0 and len(sockets)==2
    assert {s['body']['input']['tr_id'] for s in sockets[0].sent}=={'H0STCNT0'}
    assert {s['body']['input']['tr_id'] for s in sockets[1].sent}=={'H0UNCNT0'}


def test_complete_silence_reconnects_resubscribes_and_stops_after_three_failures(tmp_path,monkeypatch):
    def factory(index,elapsed,websocket):
        class Silent(Socket):
            def recv(self):
                elapsed[0]+=1
                raise websocket.WebSocketTimeoutException()
        return Silent()
    stream,path,elapsed,sockets=setup_runner(tmp_path,monkeypatch,factory)
    out=tmp_path/'out'
    with pytest.raises(BrokerError):stream.watch(path,out,seconds=60,idle_timeout=3,control_file=tmp_path/'control.json')
    state=json.loads((out/'summary.json').read_text())
    assert state['status']=='failed' and state['connection_state']=='failed'
    assert state['idle_reconnections']==state['reconnections']==3
    assert state['last_disconnect_reason']=='receive_idle_timeout'
    assert state['messages_received']==0 and state['last_message_at'] is None
    assert len(sockets)==3 and all(s.closed and len(s.sent)==1 for s in sockets)
    history=[json.loads(line) for line in (out/'connection-events.jsonl').read_text().splitlines()]
    assert sum(e['kind']=='unresponsive' for e in history)==3
    assert state['orders_sent']==0
    # Error exit still flushes the journal into a deterministic replay.
    replay_ticks(out/'plan.json',out/'ticks.jsonl',tmp_path/'replay')
    assert (tmp_path/'replay'/'events.jsonl').read_bytes()==(out/'events.jsonl').read_bytes()


def test_periodic_pingpong_does_not_reconnect_even_without_any_trades(tmp_path,monkeypatch):
    ping=json.dumps({'header':{'tr_id':'PINGPONG'},'body':{}})
    def factory(index,elapsed,websocket):
        class Heartbeats(Socket):
            def recv(self):
                elapsed[0]+=2
                if elapsed[0]>10:raise KeyboardInterrupt()
                return ping
        return Heartbeats()
    stream,path,elapsed,sockets=setup_runner(tmp_path,monkeypatch,factory)
    out=tmp_path/'out'
    result=stream.watch(path,out,seconds=60,idle_timeout=3,control_file=tmp_path/'control.json')
    assert result['status']=='interrupted' and result['reconnections']==0
    assert result['quote_ticks']==0 and result['last_trade_message_at'] is None
    assert result['pingpongs_received']==len(sockets[0].pongs)>0
    assert result['last_message_at']==result['last_pingpong_at']
    assert all(p==ping.encode() for p in sockets[0].pongs)
    for file in out.iterdir():
        text=file.read_text(encoding='utf-8')
        assert 'APPROVAL' not in text and 'SECRET' not in text


def test_after_silence_new_connection_never_reuses_old_quotes(tmp_path,monkeypatch):
    def factory(index,elapsed,websocket):
        class Recovering(Socket):
            calls=0
            def recv(self):
                self.calls+=1;elapsed[0]+=1
                if index==0:
                    if self.calls==1:
                        fields=['0']*47
                        for k,v in {0:'000001',1:'090001',2:'106',3:'2',4:'6',9:'100',13:'100',33:'20261109',41:'50'}.items():fields[k]=v
                        return '0|H0STCNT0|001|'+'^'.join(fields)
                    raise websocket.WebSocketTimeoutException()
                if self.calls==1:return json.dumps({'header':{'tr_id':'PINGPONG'},'body':{}})
                raise KeyboardInterrupt()
        return Recovering()
    stream,path,elapsed,sockets=setup_runner(tmp_path,monkeypatch,factory)
    out=tmp_path/'out'
    result=stream.watch(path,out,seconds=60,idle_timeout=6,control_file=tmp_path/'control.json')
    assert result['idle_reconnections']==1 and len(sockets)==2
    assert result['quote_ticks']==1 and result['pingpongs_received']==1
    assert sockets[0].closed and len(sockets[1].sent)==1
    journal=[json.loads(line) for line in (out/'ticks.jsonl').read_text().splitlines()]
    assert [r for r in journal if r['kind']=='quote_snapshot'][-1]['quotes']=={}
    events=[json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
    states=[e for e in events if e['kind']=='signal_state']
    assert any(e['breakout_present'] is True for e in states)
    assert states[-1]['entry_watch'] is None and states[-1]['breakout_present'] is None


def test_subscription_limit_preserves_ack_count_and_sanitized_error(tmp_path,monkeypatch):
    def factory(index,elapsed,websocket):
        class Limited(Socket):
            def recv(self):
                count=len(self.sent)
                symbol=self.sent[-1]['body']['input']['tr_key']
                return json.dumps({'header':{'tr_id':'H0STCNT0','tr_key':symbol},
                    'body':{'rt_cd':'0' if count <= 41 else '1',
                            'msg_cd':'OPSP0000' if count <= 41 else 'OPSP0008',
                            'msg1':'PRIVATE_SERVER_TEXT'}})
        return Limited()
    stream,path,elapsed,sockets=setup_runner(tmp_path,monkeypatch,factory)
    plan=json.loads(path.read_text());plan['subscriptions'].extend(f'{i:06d}' for i in range(100,260))
    path.write_text(json.dumps(plan))
    out=tmp_path/'out'
    with pytest.raises(BrokerError):
        stream.watch(path,out,seconds=60,probe=True,control_file=tmp_path/'control.json')
    raw=(out/'summary.json').read_text()
    state=json.loads(raw)
    assert state['status']=='failed' and state['failure_reason']=='subscription_limit'
    assert state['subscriptions_requested']==165 and state['subscription_acks']==41
    assert state['kis_error_code']=='OPSP0008' and state['rejected_symbol']==plan['subscriptions'][41]
    assert len(sockets)==1 and len(sockets[0].sent)==42 and sockets[0].closed
    assert 'PRIVATE_SERVER_TEXT' not in raw and 'APPROVAL' not in raw
