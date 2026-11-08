from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from trendfollow.intraday import IntradayMonitor, replay_ticks
from trendfollow.intraday_sampling import ObservationSession, validate_sampling


def plan():
    return json.loads((Path(__file__).resolve().parents[1]/'examples/intraday-demo-plan.json').read_text())


START = datetime.fromisoformat('2026-11-09T09:00:00+09:00')


def tick(symbol='000001',price=106,elapsed=0,volume=100,low=100):
    return dict(symbol=symbol,timestamp=(START+timedelta(seconds=elapsed)).isoformat(),
                price=price,previous_close=100,day_low=min(low,price),cumulative_volume=volume,
                volume_vs_previous_same_time=2.0)


def session(storage='summary',p=None):
    journal,events=[],[]
    monitor=IntradayMonitor(p or plan())
    sampler=ObservationSession(monitor,START.isoformat(),journal.append,events.extend,storage=storage)
    sampler.reset_connection(START.isoformat())
    for symbol in ['069500','000002','000003','000004']:
        t=tick(symbol,100 if symbol=='069500' else 102)
        sampler.tick(t,t['timestamp'])
    return sampler,journal,events


def test_heavy_ticks_do_not_run_industry_calculation_but_stop_is_immediate(monkeypatch):
    s,journal,events=session()
    original=s.monitor.evaluate
    calls=[]
    def evaluate(now):
        calls.append(now)
        return original(now)
    monkeypatch.setattr(s.monitor,'evaluate',evaluate)
    for i in range(1000):
        t=tick(elapsed=i/250,volume=100+i,low=94 if i==500 else 100)
        s.tick(t,t['timestamp'])
    assert calls==[]
    stop=[e for e in events if e['kind']=='holding_stop_state' and e['holding_stop_breached'] is True]
    assert len(stop)==1 and stop[0]['timestamp']==(START+timedelta(seconds=2)).isoformat()
    s.clock((START+timedelta(seconds=5)).isoformat())
    assert len(calls)==1
    assert s.monitor.last['000001']['holding_stop_breached'] is True
    snapshots=[r for r in journal if r['kind']=='quote_snapshot']
    assert len(snapshots)==1


def exercise(storage):
    s,journal,events=session(storage)
    for i in range(1000):
        t=tick(elapsed=(i+1)/100,volume=i+101,low=94 if i==449 else 100)
        s.tick(t,t['timestamp'])
    # Timeout staleness and a disconnect are also replayable without full ticks.
    s.clock((START+timedelta(seconds=71)).isoformat())
    s.reset_connection((START+timedelta(seconds=72)).isoformat())
    s.finish((START+timedelta(seconds=73)).isoformat())
    return s,journal,events


def test_summary_reduces_storage_and_replays_same_sampled_decisions_and_fast_stops(tmp_path):
    p=tmp_path/'plan.json';p.write_text(json.dumps(plan()))
    results={}
    journals={}
    for storage in ['all','summary']:
        s,journal,events=exercise(storage)
        journals[storage]=journal
        source=tmp_path/f'{storage}.jsonl'
        source.write_text(''.join(json.dumps(r)+'\n' for r in journal))
        out=tmp_path/storage
        replay_ticks(p,source,out)
        replayed=[json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
        assert replayed==events
        results[storage]=events
    assert results['summary']==results['all']
    size=lambda records:len(json.dumps(records).encode())
    assert size(journals['summary']) < size(journals['all'])/10
    assert len(journals['summary']) < 30
    assert len(journals['all']) > 1000


@pytest.mark.parametrize('reason',['setup','basis'])
def test_snapshot_preserves_between_interval_invalidations_without_held_position(tmp_path,reason):
    p=plan();p['targets']['000001']['holding_stop']=None
    s,journal,events=session(p=p)
    t=tick(elapsed=1,low=89 if reason=='setup' else 100)
    if reason=='basis':t['previous_close']=50
    s.tick(t,t['timestamp'])
    rebound=tick(price=110,elapsed=2,volume=101)
    s.tick(rebound,rebound['timestamp'])
    s.finish((START+timedelta(seconds=3)).isoformat())
    assert not any(r['kind']=='critical_tick' for r in journal)
    config=tmp_path/'plan.json';config.write_text(json.dumps(p))
    source=tmp_path/'ticks.jsonl';source.write_text(''.join(json.dumps(r)+'\n' for r in journal))
    replay_ticks(config,source,tmp_path/'out')
    replayed=[json.loads(line) for line in (tmp_path/'out'/'events.jsonl').read_text().splitlines()]
    assert replayed==events
    field='setup_invalidated' if reason=='setup' else 'price_basis_mismatch'
    assert replayed[-1][field] is True


def test_final_partial_interval_flushes_and_finish_is_idempotent():
    s,journal,events=session()
    t=tick(elapsed=1);s.tick(t,t['timestamp'])
    s.finish((START+timedelta(seconds=2)).isoformat())
    assert journal[-1]['kind']=='quote_snapshot'
    assert events[-1]['entry_watch'] is True
    n=len(journal)
    s.finish((START+timedelta(seconds=3)).isoformat())
    assert len(journal)==n


@pytest.mark.parametrize('seconds,storage',[(0,'all'),(-1,'summary'),(float('nan'),'summary'),(61,'summary'),(True,'summary'),(5,'none')])
def test_invalid_sampling_options(seconds,storage):
    with pytest.raises(ValueError):validate_sampling(seconds,storage,60)


@pytest.mark.parametrize('storage',['all','summary'])
def test_live_runner_flushes_last_interval_on_interrupt_and_replays(tmp_path,monkeypatch,storage):
    import websocket
    import trendfollow.kis_stream as stream
    p=plan();p.pop('synthetic')
    config=tmp_path/'plan.json';config.write_text(json.dumps(p))
    elapsed=[0]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return START+timedelta(seconds=elapsed[0])
    monkeypatch.setattr(stream,'datetime',Clock)
    monkeypatch.setattr(stream.time,'sleep',lambda *a:None)
    monkeypatch.setattr(stream.Credentials,'load',lambda *a:stream.Credentials('kis','paper','KEY','SECRET'))
    monkeypatch.setattr(stream.QuoteClient,'_send',lambda *a,**kw:({'approval_key':'PRIVATE'},{}))
    fields=['0']*47
    for index,value in {0:'000001',1:'090001',2:'100',3:'3',4:'0',9:'94',13:'100',33:'20261109',41:'50'}.items():
        fields[index]=value
    class Socket:
        def settimeout(self,value):assert value==1
        def send(self,value):pass
        def recv(self):
            elapsed[0]+=1
            if elapsed[0]==1:return '0|H0STCNT0|001|'+'^'.join(fields)
            raise KeyboardInterrupt()
        def close(self):pass
    monkeypatch.setattr(websocket,'create_connection',lambda *a,**kw:Socket())
    root=tmp_path/'live'
    result=stream.watch(config,root,seconds=10,tick_storage=storage,control_file=tmp_path/'control.json')
    assert result['status']=='interrupted' and result['quote_ticks']==1 and result['orders_sent']==0
    assert result['decision_evaluations']==2  # connection gap and partial final interval
    events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
    immediate=[e for e in events if e['kind']=='holding_stop_state' and e['holding_stop_breached'] is True]
    assert immediate[0]['timestamp']==(START+timedelta(seconds=1)).isoformat()
    replay_ticks(root/'plan.json',root/'ticks.jsonl',tmp_path/'replay')
    replayed=[json.loads(line) for line in (tmp_path/'replay'/'events.jsonl').read_text().splitlines()]
    assert replayed==events
