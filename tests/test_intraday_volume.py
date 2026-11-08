import json
from pathlib import Path

import pandas as pd
import pytest

from trendfollow.intraday import IntradayMonitor,replay_ticks,validate_plan
from trendfollow.intraday_sampling import ObservationSession
from trendfollow.intraday_volume import build_reference,import_runs,read_samples

EXAMPLES=Path(__file__).resolve().parents[1]/'examples'


def plan():
    return json.loads((EXAMPLES/'intraday-demo-volume-plan.json').read_text())


def records():
    return [json.loads(line) for line in (EXAMPLES/'intraday-demo-volume-ticks.jsonl').read_text().splitlines()]


def test_completed_minute_does_not_use_later_current_volume():
    m=IntradayMonitor(plan());events=[]
    for record in records():
        events.extend(m.update(record['tick'],record['received_at']))
        if record['received_at'].endswith('09:01:10+09:00'):
            assert m.volume_samples['000001']['cumulative_volume']==1490
            assert m.last['000001']['volume_pass'] is False
    confirmed=[e for e in events if e['entry_confirmed']]
    assert len(confirmed)==1
    assert confirmed[0]['timestamp']=='2026-11-09T09:02:00+09:00'
    assert confirmed[0]['volume_ratio_same_time']==1.55 and confirmed[0]['volume_reference_sessions']==5
    assert m.last['000001']['volume_pass'] is True and m.last['000001']['entry_confirmed'] is False


@pytest.mark.parametrize('mode',['insufficient','zero','stale','missing'])
def test_volume_unavailable_is_not_verified(mode):
    p=plan()
    if mode=='insufficient':p['volume_reference']['min_sessions']=6
    if mode=='zero':
        for row in p['volume_reference']['symbols']['000001'].values():row['mean_cumulative_volume']=0
    if mode=='missing':p.pop('volume_reference');p['volume_confirmation']='unavailable_same_time_history'
    m=IntradayMonitor(p)
    for r in records():m.update(r['tick'],r['received_at'])
    if mode=='stale':m.evaluate('2026-11-09T09:03:06+09:00')
    state=m.last['000001']
    assert state['volume_pass'] is None and state['entry_confirmed'] is False


def test_first_quote_after_boundary_requires_a_current_sample():
    m=IntradayMonitor(plan())
    tick=dict(records()[5]['tick'])  # first candidate quote is after 09:01 boundary
    events=m.update(tick,tick['timestamp'])
    assert events[-1]['volume_status']=='current_sample_unavailable'
    assert events[-1]['volume_pass'] is None


def test_future_second_near_minute_boundary_does_not_enter_snapshot_cache():
    m=IntradayMonitor(plan())
    tick=dict(records()[-2]['tick'])  # exchange timestamp 09:02:00
    m.update(tick,'2026-11-09T09:01:59+09:00')
    assert not m.volume_samples


@pytest.mark.parametrize('change',['future','duplicate','mix','bad_age'])
def test_invalid_frozen_references_are_rejected(change):
    p=plan();ref=p['volume_reference']
    if change=='future':ref['as_of']='2026-11-09'
    if change=='duplicate':ref['symbols']['000001']['09:01:00']['sessions'][1]=ref['symbols']['000001']['09:01:00']['sessions'][0]
    if change=='mix':p.pop('synthetic')
    if change=='bad_age':ref['max_sample_age_seconds']=61
    with pytest.raises(ValueError):validate_plan(p)


def test_profile_uses_only_prior_dates_and_deduplicates_days_by_rejecting_duplicate_rows(tmp_path):
    history=pd.read_csv(EXAMPLES/'intraday-demo-volume-samples.csv',dtype={'symbol':str})
    future=history[history.session=='2026-11-06'].copy();future['session']='2026-11-09'
    future['sample_timestamp']=future.sample_timestamp.str.replace('2026-11-06','2026-11-09')
    future['cumulative_volume']=999999
    path=tmp_path/'h.csv';pd.concat([history,future]).to_csv(path,index=False)
    out=tmp_path/'ref.json';result=build_reference(path,'2026-11-06',out)
    ref=json.loads(out.read_text())
    assert result['ready_minutes']==2
    assert ref['symbols']['000001']['09:01:00']['mean_cumulative_volume']==1000
    assert ref['symbols']['000001']['09:01:00']['session_count']==5
    pd.concat([history,history.iloc[:1]]).to_csv(path,index=False)
    with pytest.raises(ValueError,match='중복'):build_reference(path,'2026-11-06',tmp_path/'bad.json')


def test_real_and_synthetic_history_cannot_be_mixed(tmp_path):
    history=pd.read_csv(EXAMPLES/'intraday-demo-volume-samples.csv',dtype={'symbol':str})
    history.loc[0,'synthetic']=False
    path=tmp_path/'h.csv';history.to_csv(path,index=False)
    with pytest.raises(ValueError,match='섞을'):read_samples(path)


def run_session(storage):
    m=IntradayMonitor(plan());journal,events=[],[]
    source=records()
    sampler=ObservationSession(m,source[0]['received_at'],journal.append,events.extend,seconds=1,storage=storage)
    sampler.reset_connection(source[0]['received_at'])
    for record in source:sampler.tick(record['tick'],record['received_at'])
    sampler.finish(source[-1]['received_at'])
    return journal,events


def test_summary_keeps_minute_anchor_and_replays_same_volume_gate_as_all_ticks(tmp_path):
    config=tmp_path/'p.json';config.write_text(json.dumps(plan()))
    outputs=[]
    for storage in ['all','summary']:
        journal,events=run_session(storage)
        path=tmp_path/(storage+'.jsonl');path.write_text(''.join(json.dumps(r)+'\n' for r in journal))
        out=tmp_path/storage;replay_ticks(config,path,out)
        replayed=[json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
        assert replayed==events
        outputs.append(events)
    assert outputs[0]==outputs[1] and any(e.get('entry_confirmed') for e in outputs[0])


def test_import_preserves_partial_windows_and_reimport_does_not_add_history_days(tmp_path):
    config=tmp_path/'p.json';config.write_text(json.dumps(plan()))
    source=tmp_path/'source.jsonl';source.write_text((EXAMPLES/'intraday-demo-volume-ticks.jsonl').read_text())
    run=tmp_path/'run';replay_ticks(config,source,run)
    path=tmp_path/'samples.csv'
    first=import_runs([run],path);second=import_runs([run,run],path)
    assert first['samples']==second['samples']==2 and first['sessions']==second['sessions']==1
    samples=read_samples(path).set_index('minute')
    assert set(samples.index)=={'09:01:00','09:02:00'}
    assert samples.loc['09:01:00','cumulative_volume']==1490
    assert samples.loc['09:02:00','cumulative_volume']==3100
    # No interpolation or full-day padding from a two-minute recording.
    assert not any(minute>'09:02:00' for minute in samples.index)


def test_import_full_and_summary_records_produce_same_minute_samples(tmp_path):
    outputs=[]
    for storage in ['all','summary']:
        root=tmp_path/storage;root.mkdir()
        journal,_=run_session(storage)
        (root/'plan.json').write_text(json.dumps(plan()))
        (root/'summary.json').write_text(json.dumps({'mode':'replay_observation_only'}))
        (root/'ticks.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in journal))
        history=tmp_path/(storage+'.csv');import_runs([root],history)
        outputs.append(read_samples(history))
    pd.testing.assert_frame_equal(outputs[0],outputs[1])


def test_probe_is_not_a_volume_history_source(tmp_path):
    root=tmp_path/'probe';root.mkdir()
    (root/'plan.json').write_text(json.dumps(plan()))
    (root/'summary.json').write_text(json.dumps({'mode':'connection_probe'}))
    with pytest.raises(ValueError,match='접속 확인'):import_runs([root],tmp_path/'h.csv')


@pytest.mark.parametrize('volume,passed',[(1490,False),(1500,True),(1510,True)])
def test_volume_multiple_includes_exact_threshold(volume,passed):
    m=IntradayMonitor(plan())
    for record in records()[1:5]:m.update(record['tick'],record['received_at'])
    tick=dict(records()[5]['tick'],timestamp='2026-11-09T09:01:00+09:00',cumulative_volume=volume)
    event=m.update(tick,tick['timestamp'])[-1]
    assert event['volume_pass'] is passed and event['entry_confirmed'] is passed
    assert event['volume_ratio_same_time']==volume/1000


def test_profile_lookback_counts_unique_collected_sessions(tmp_path):
    out=tmp_path/'reference.json'
    build_reference(EXAMPLES/'intraday-demo-volume-samples.csv','2026-11-06',out,lookback=4,min_sessions=3)
    row=json.loads(out.read_text())['symbols']['000001']['09:01:00']
    assert row['session_count']==4 and row['mean_cumulative_volume']==1050
    assert row['sessions']==['2026-11-03','2026-11-04','2026-11-05','2026-11-06']


def test_decreasing_cumulative_volume_is_rejected(tmp_path):
    f=pd.read_csv(EXAMPLES/'intraday-demo-volume-samples.csv',dtype={'symbol':str})
    f.loc[(f.session=='2026-11-02')&(f.minute=='09:02:00'),'cumulative_volume']=1
    path=tmp_path/'h.csv';f.to_csv(path,index=False)
    with pytest.raises(ValueError,match='감소'):read_samples(path)
