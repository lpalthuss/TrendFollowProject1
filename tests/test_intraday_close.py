from datetime import datetime
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest

from trendfollow.intraday import IntradayMonitor, replay_ticks
from trendfollow.intraday_close import close_comparison
from trendfollow.intraday_sampling import ObservationSession


@pytest.fixture
def files(tmp_path):
    examples=Path(__file__).resolve().parents[1]/'examples'
    paths={}
    for key,name in [('plan','intraday-demo-plan.json'),('ticks','intraday-demo-ticks.jsonl'),
                     ('prices','intraday-demo-close-prices.csv'),('peers','intraday-demo-close-peers.csv')]:
        target=tmp_path/name;shutil.copyfile(examples/name,target);paths[key]=target
    paths['root']=tmp_path
    paths['run']=tmp_path/'run'
    replay_ticks(paths['plan'],paths['ticks'],paths['run'])
    return paths


def compare(f,**kwargs):
    out=kwargs.pop('output',f['root']/'comparison')
    run=kwargs.pop('run_dir',f['run'])
    summary=close_comparison(f['plan'],f['prices'],f['peers'],out,run_dir=run,**kwargs)
    content=json.loads((out/'comparison.json').read_text(encoding='utf-8'))
    return summary,content['results'][0]


def test_price_breakout_failed_by_close_but_industry_held_and_stop_was_observed(files):
    hashes={key:files[key].read_bytes() for key in ['plan','ticks','prices','peers']}
    summary,row=compare(files)
    assert summary['mode']=='synthetic_comparison' and summary['breakouts_seen']==1
    assert row['intraday_breakout_seen'] is True and row['close_above_resistance'] is False
    assert row['breakout_outcome']=='not_held_at_close'
    assert row['industry_outcome']=='held_at_close'
    assert row['intraday_stop_contact_seen'] is True and row['close_stop_contact'] is True
    assert row['close_stop_contact_without_observation'] is False
    assert row['partial_session'] is True and row['close_setup_healthy'] is True
    assert all(files[key].read_bytes()==value for key,value in hashes.items())


def test_retained_price_breakout_is_not_claimed_as_final_entry_confirmation(files):
    prices=pd.read_csv(files['prices'],dtype={'symbol':str})
    prices.loc[(prices.symbol=='000001')&(prices.date=='2026-11-09'),'close']=106
    prices.to_csv(files['prices'],index=False)
    summary,row=compare(files)
    assert row['breakout_outcome']=='held_at_close' and summary['breakouts_held_at_close']==1
    assert 'entry_confirmed' not in row


def test_off_is_unknown_not_zero_even_with_existing_observation(files):
    summary,row=compare(files,observation='off')
    assert row['observation_status']=='disabled'
    assert row['intraday_breakout_seen'] is None and row['intraday_stop_contact_seen'] is None
    assert row['breakout_outcome']=='skipped_disabled' and row['partial_session'] is None
    assert summary['breakouts_seen'] is None and summary['breakouts_held_at_close'] is None
    assert row['close_above_resistance'] is False
    assert not any(str(files['run']) in p for p in summary['source_hashes'])


def test_missing_records_are_not_a_day_without_signals(files):
    summary,row=compare(files,run_dir=files['root']/'does-not-exist')
    assert row['observation_status']=='missing_recordings'
    assert summary['breakouts_seen'] is None and row['intraday_entry_watch_seen'] is None


def test_connection_probe_is_not_an_observation_run(files):
    probe=files['root']/'probe';probe.mkdir()
    (probe/'summary.json').write_text(json.dumps({'mode':'connection_probe'}))
    summary,row=compare(files,run_dir=probe)
    assert row['observation_status']=='probe_only' and summary['breakouts_seen'] is None


def test_subscribed_peers_without_target_quotes_are_unknown(files):
    records=[json.loads(line) for line in files['ticks'].read_text().splitlines()]
    records=[r for r in records if r.get('tick',{}).get('symbol')!='000001']
    files['ticks'].write_text(''.join(json.dumps(r)+'\n' for r in records))
    run=files['root']/'peer-only';replay_ticks(files['plan'],files['ticks'],run)
    summary,row=compare(files,run_dir=run)
    assert row['observation_status']=='no_target_quotes' and summary['breakouts_seen'] is None


@pytest.mark.parametrize('invalid',[False,True])
def test_missing_or_quarantined_daily_price_is_unknown_not_a_failed_breakout(files,invalid):
    prices=pd.read_csv(files['prices'],dtype={'symbol':str})
    today=(prices.symbol=='000001')&(prices.date=='2026-11-09')
    if invalid:prices.loc[today,'close']=0
    else:prices=prices[~today]
    prices.to_csv(files['prices'],index=False)
    summary,row=compare(files)
    assert row['close_above_resistance'] is None and row['breakout_outcome']=='unknown_close'
    assert summary['rejected_price_bars']==int(invalid)


def test_price_basis_discrepancy_blocks_threshold_comparison(files):
    prices=pd.read_csv(files['prices'],dtype={'symbol':str})
    prior=(prices.symbol=='000001')&(prices.date=='2026-11-06')
    for column in ['open','high','low','close']:
        prices[column]=prices[column].astype(float)
        prices.loc[prior,column]*=.5
    prices.to_csv(files['prices'],index=False)
    _,row=compare(files)
    assert row['close_price_basis_valid'] is False and row['close_above_resistance'] is None
    assert row['close_stop_contact'] is None


def test_industry_uses_frozen_peers_despite_changed_labels_and_excludes_self(files):
    peers=pd.read_csv(files['peers'],dtype={'symbol':str})
    peers['sector']='NEW_CLASSIFICATION'
    extra=pd.DataFrame([dict(date='2026-11-09',symbol=s,sector='SYNTHETIC',day_return=-.99,quote_eligible=1)
                        for s in ['000001','999999']])
    pd.concat([peers,extra]).to_csv(files['peers'],index=False)
    _,row=compare(files)
    assert row['close_peer_count']==3 and row['peer_classification_changes']==3
    assert row['close_peer_coverage']==1 and row['close_industry_action'] is True


@pytest.mark.parametrize('missing',['peer','eligible','benchmark','all'])
def test_missing_peer_coverage_is_unknown_not_weak_industry(files,missing):
    peers=pd.read_csv(files['peers'],dtype={'symbol':str})
    if missing=='peer':peers=peers[peers.symbol!='000002']
    elif missing=='eligible':peers.loc[peers.symbol=='000002','quote_eligible']=0
    elif missing=='all':peers=peers.iloc[:0]
    else:
        prices=pd.read_csv(files['prices'],dtype={'symbol':str})
        prices[prices.symbol!='069500'].to_csv(files['prices'],index=False)
    peers.to_csv(files['peers'],index=False)
    _,row=compare(files)
    assert row['close_industry_action'] is None and row['industry_outcome']=='unknown_close'


def test_different_plan_is_rejected_before_output_is_written(files):
    p=json.loads(files['plan'].read_text());p['targets']['000001']['resistance']=106
    files['plan'].write_text(json.dumps(p))
    with pytest.raises(ValueError,match='감시 계획이 다릅니다'):compare(files)
    assert not (files['root']/'comparison').exists()


@pytest.mark.parametrize('now',['2026-11-08T18:00:00+09:00','2026-11-09T16:09:00+09:00'])
def test_real_comparison_requires_completed_close(files,now):
    p=json.loads(files['plan'].read_text());p.pop('synthetic')
    files['plan'].write_text(json.dumps(p))
    with pytest.raises(ValueError,match='16:10'):
        compare(files,now=datetime.fromisoformat(now))


def test_current_real_day_is_allowed_after_close_with_no_observation(files):
    p=json.loads(files['plan'].read_text());p.pop('synthetic')
    files['plan'].write_text(json.dumps(p))
    summary,row=compare(files,observation='off',now=datetime.fromisoformat('2026-11-09T16:10:00+09:00'))
    assert summary['mode']=='observation_comparison' and row['observation_status']=='disabled'


def test_no_stale_baseline_across_intervening_benchmark_session(files):
    prices=pd.read_csv(files['prices'],dtype={'symbol':str})
    extra=prices[(prices.symbol=='069500')&(prices.date=='2026-11-06')].copy()
    extra['date']='2026-11-07'
    pd.concat([prices,extra]).to_csv(files['prices'],index=False)
    with pytest.raises(ValueError,match='미반영 거래일'):compare(files)


def test_batched_all_and_summary_journals_produce_same_close_outcomes(files):
    p=json.loads(files['plan'].read_text())
    records=[json.loads(line) for line in files['ticks'].read_text().splitlines()]
    results=[]
    for storage in ['all','summary']:
        journal,events=[],[]
        monitor=IntradayMonitor(p)
        sampler=ObservationSession(monitor,records[0]['received_at'],journal.append,events.extend,seconds=1,storage=storage)
        sampler.reset_connection(records[0]['received_at'])
        for record in records:
            if 'tick' in record:sampler.tick(record['tick'],record['received_at'])
            else:sampler.clock(record['received_at'])
        sampler.finish(records[-1]['received_at'])
        root=files['root']/storage;root.mkdir()
        (root/'plan.json').write_text(json.dumps(p))
        (root/'summary.json').write_text(json.dumps({'mode':'live_observation_only','status':'completed'}))
        (root/'ticks.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in journal))
        (root/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        _,row=compare(files,run_dir=root,output=files['root']/f'compare-{storage}')
        results.append(row)
    for field in ['intraday_breakout_seen','intraday_stop_contact_seen','breakout_outcome','industry_outcome','partial_session']:
        assert results[0][field]==results[1][field]


@pytest.mark.parametrize('existing_recording',[False,True])
def test_cli_uses_saved_off_setting_but_preserves_existing_partial_records(files,capsys,existing_recording):
    from trendfollow.cli import main
    from trendfollow.observation_control import set_observation
    control=files['root']/'c.json';set_observation(False,control)
    args=['intraday-close','--plan',str(files['plan']),'--prices',str(files['prices']),
          '--peer-returns',str(files['peers']),'--out',str(files['root']/'automatic'),
          '--control-file',str(control)]
    if existing_recording:args.extend(['--run-dir',str(files['run'])])
    main(args)
    summary=json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary['observation_status']==('recorded' if existing_recording else 'disabled')
    assert summary['breakouts_seen']==(1 if existing_recording else None)
