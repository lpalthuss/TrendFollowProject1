import json

import pandas as pd
import pytest

from trendfollow.krx_sessions import previous_close_date, require_previous_close


def test_watch_basis_uses_krx_sessions_and_rejects_stale_candidates():
    assert previous_close_date('2026-11-13') == '2026-11-12'
    assert previous_close_date('2026-10-12') == '2026-10-08'
    with pytest.raises(ValueError, match='개장'):
        previous_close_date('2026-10-09')
    with pytest.raises(ValueError, match='2026-11-12'):
        require_previous_close('2026-11-13', '2026-11-10')

from trendfollow.intraday import IntradayMonitor, prepare_plan, replay_ticks, validate_plan
from trendfollow.kis_stream import parse_trades, subscription, watch


def plan():
    peers = ['000002','000003','000004']
    return {'version':1,'as_of':'2026-11-06','session':'2026-11-09','exchange':'KRX',
        'mode':'observation_only','sector_min_peers':3,'sector_min_breadth':.6,
        'sector_require_action':True,'min_peer_coverage':1.,'max_quote_age_seconds':60,
        'volume_confirmation':'unavailable_same_time_history','rs_timing':'prior_completed_close_frozen',
        'subscriptions':['000001',*peers,'069500'],
        'members':{s:{'sector':'S','benchmark':'069500'} for s in peers},
        'targets':{'000001':{'previous_close':100,'resistance':105,'healthy_floor':90,
            'holding_stop':95,'sector':'S','benchmark':'069500',
            'prior_filters_pass':True,'peer_symbols':peers}}}


def tick(symbol='000001',price=106,volume=100,at='09:01:00',low=100):
    return {'symbol':symbol,'timestamp':'2026-11-09T'+at+'+09:00','price':price,
            'previous_close':100,'day_low':min(low,price),'cumulative_volume':volume,
            'volume_vs_previous_same_time':2.0}


def populate(monitor):
    for symbol in ['069500','000002','000003','000004']:
        t=tick(symbol,100 if symbol=='069500' else 102)
        monitor.update(t,t['timestamp'])


def test_industry_requires_full_fresh_coverage_and_excludes_candidate():
    m=IntradayMonitor(plan())
    t=tick();events=m.update(t,t['timestamp'])
    assert events[-1]['industry_action'] is None
    populate(m)
    state=m.last['000001']
    assert state['industry_action'] is True and state['entry_watch'] is True
    # Repeated identical ticks produce no duplicate state alerts.
    assert m.update(t,t['timestamp'])==[]
    events=m.evaluate('2026-11-09T09:02:01+09:00')
    assert events[-1]['peer_count']==0 and events[-1]['entry_watch'] is None
    assert events[-1]['entry_confirmed'] is False
    assert events[-1]['expected_peer_count']==3


def test_price_crossing_cancel_and_invalidated_setup_do_not_recover():
    m=IntradayMonitor(plan());populate(m)
    t=tick();assert m.update(t,t['timestamp'])[-1]['entry_watch'] is True
    below=tick(price=104,volume=101,at='09:01:01')
    assert m.update(below,below['timestamp'])[-1]['entry_watch'] is False
    broken=tick(price=106,volume=102,at='09:01:02',low=89)
    assert m.update(broken,broken['timestamp'])[-1]['setup_invalidated'] is True
    rebound=tick(price=110,volume=103,at='09:01:03')
    m.update(rebound,rebound['timestamp'])
    assert m.last['000001']['entry_watch'] is None


def test_stop_detects_day_low_touch_even_after_rebound():
    m=IntradayMonitor(plan())
    t=tick(price=100,low=94)
    event=m.update(t,t['timestamp'])[-1]
    assert event['holding_stop_breached'] is True and event['entry_confirmed'] is False
    recovered=tick(price=110,volume=101,at='09:01:01',low=100)
    m.update(recovered,recovered['timestamp'])
    assert m.last['000001']['holding_stop_breached'] is True


def test_out_of_order_volume_reset_and_stale_ticks_never_replace_quote():
    m=IntradayMonitor(plan())
    t=tick();m.update(t,t['timestamp'])
    for old in [tick(price=90,volume=99),tick(price=90,volume=101,at='09:00:59')]:
        m.update(old,t['timestamp'])
    assert m.quotes['000001']['price']==106
    m.update(t,'2026-11-09T09:03:00+09:00')
    assert m.last['000001']['breakout_present'] is None
    future=tick(at='09:04:00')
    m.update(future,'2026-11-09T09:03:00+09:00')
    assert m.quotes['000001']['timestamp']==t['timestamp']
    with pytest.raises(ValueError,match='감시일'):
        m.update(t,'2026-11-10T09:01:00+09:00')


def test_corporate_action_price_basis_mismatch_blocks_signals():
    m=IntradayMonitor(plan());populate(m)
    t=tick();t['previous_close']=50
    event=m.update(t,t['timestamp'])[-1]
    assert event['price_basis_mismatch'] is True
    assert event['breakout_present'] is None and event['holding_stop_breached'] is None
    assert m.latest_target_quotes[t['symbol']]['price'] == t['price']
    assert m.latest_target_quotes[t['symbol']]['cumulative_volume'] == t['cumulative_volume']
    m.reset_connection()
    assert t['symbol'] not in m.quotes and t['symbol'] in m.latest_target_quotes


def test_connection_reset_requires_new_peer_quotes():
    m=IntradayMonitor(plan());populate(m)
    t=tick();m.update(t,t['timestamp'])
    m.reset_connection()
    event=m.evaluate(t['timestamp'])[-1]
    assert event['industry_action'] is None
    m.update(t,t['timestamp'])
    assert m.last['000001']['entry_watch'] is None


def packet(sign='2',price='106',change='6',symbol='000001'):
    fields=['0']*47
    for key,value in {0:symbol,1:'090100',2:price,3:sign,4:change,9:'100',13:'200',33:'20261109',41:'100'}.items():
        fields[key]=value
    return '^'.join(fields)


def test_kis_batched_fields_dates_signed_changes_and_volume():
    parsed=parse_trades('0|H0STCNT0|002|'+packet()+'^'+packet(sign='5',price='94',symbol='000002'))
    assert len(parsed)==2
    assert parsed[0]['previous_close']==parsed[1]['previous_close']==100
    assert parsed[0]['volume_vs_previous_same_time']==2
    assert parsed[0]['timestamp']=='2026-11-09T09:01:00+09:00'
    with pytest.raises(ValueError):parse_trades('0|H0STCNT0|002|'+packet())
    with pytest.raises(ValueError):parse_trades('1|H0STCNI0|001|encrypted')
    assert subscription('APPROVAL','000001')['body']['input']['tr_id']=='H0STCNT0'


def test_after_market_uses_integrated_trades_and_rejects_other_market_codes():
    fields = packet(price='106', change='6').split('^')
    fields[1] = '160001'
    fields[46] = '3'
    parsed = parse_trades('0|H0UNCNT0|001|'+'^'.join(fields))
    assert parsed[0]['market'] == 'nxt_after'
    assert parsed[0]['timestamp'] == '2026-11-09T16:00:01+09:00'
    assert subscription('APPROVAL','000001','nxt_after')['body']['input']['tr_id'] == 'H0UNCNT0'
    fields[46] = '2'
    assert parse_trades('0|H0UNCNT0|001|'+'^'.join(fields)) == []
    monitor = IntradayMonitor(plan())
    trade = parsed[0]
    events = monitor.update(trade, trade['timestamp'])
    assert events and monitor.latest_target_quotes['000001']['market'] == 'nxt_after'
    assert monitor.update(dict(trade, market='krx'),trade['timestamp']) == []


def test_replay_records_staleness_and_disconnects_deterministically(tmp_path):
    config=tmp_path/'plan.json';config.write_text(json.dumps(plan()))
    t=tick()
    records=[{'tick':t,'received_at':t['timestamp']},
             {'kind':'clock','received_at':'2026-11-09T09:02:01+09:00'},
             {'kind':'connection_reset','received_at':'2026-11-09T09:02:02+09:00'}]
    source=tmp_path/'ticks.jsonl';source.write_text('\n'.join(json.dumps(r) for r in records))
    a=tmp_path/'a';b=tmp_path/'b'
    result=replay_ticks(config,source,a)
    replay_ticks(config,source,b)
    assert result['orders_sent']==0 and result['ticks']==3
    assert (a/'events.jsonl').read_bytes()==(b/'events.jsonl').read_bytes()
    with pytest.raises(FileExistsError):replay_ticks(config,source,a)


def test_plan_requires_peer_subscriptions_and_accepts_more_than_40():
    p=plan();p['subscriptions'].remove('000003')
    with pytest.raises(ValueError,match='누락'):validate_plan(p)
    p=plan();p['subscriptions'].extend(str(i).zfill(6) for i in range(100,141))
    validate_plan(p)


def test_prepare_recomputes_base_including_last_completed_day(tmp_path):
    # Minimal six-session setup whose supplied prior-row ratios intentionally
    # disagree. Preparation must calculate the full completed base itself.
    dates=pd.bdate_range('2026-10-30',periods=6)
    prices=pd.DataFrame([dict(date=str(d.date()),symbol='000001',open=100,high=101,
        low=99,close=100,volume=v) for d,v in zip(dates,[100,100,100,100,10,10])])
    prices.to_csv(tmp_path/'prices.csv',index=False)
    signal=dict(date='2026-11-06',symbol='000001',name='Alpha',benchmark='069500',sector='S',
        rs_method='percentile',entry_method='setup',weekly_trend=True,eligible_history=True,
        avg_turnover=1e10,stop_price=95,reference_price=100,rs_percentile=99,excess_return=.1,
        setup_phase='base',setup_peak_date='2026-10-30',setup_resistance=110,setup_wave_low=80)
    pd.DataFrame([signal]).to_csv(tmp_path/'signals.csv',index=False)
    peerfile=tmp_path/'peers.csv'
    pd.DataFrame([dict(date='2026-11-06',symbol=s,sector='S',day_return=.01,
        benchmark_day_return=0,quote_eligible=1,market='KOSPI',benchmark='069500')
        for s in ['000001','000002','000003','000004']]).to_csv(peerfile,index=False)
    pd.DataFrame([dict(symbol='000001',benchmark='069500',sector='S',industry_peer_history=str(peerfile))]).to_csv(tmp_path/'universe.csv',index=False)
    config={'rules':{'entry_method':'setup','setup_range_contraction':1.0}}
    (tmp_path/'config.json').write_text(json.dumps(config))
    p=prepare_plan(tmp_path/'signals.csv',tmp_path/'prices.csv',tmp_path/'universe.csv',
        '2026-11-09',['000001'],tmp_path/'config.json',tmp_path/'plan.json')
    # Full base volumes [100,100,100,10,10] contract; all raw prices are healthy.
    assert p['targets']['000001']['prior_filters_pass'] is True
    assert len(p['subscriptions'])==5
    assert p['members']['000002']['benchmark']=='069500'
    peerfile.unlink()
    p=prepare_plan(tmp_path/'signals.csv',tmp_path/'prices.csv',tmp_path/'universe.csv',
        '2026-11-09',['000001'],tmp_path/'config.json',tmp_path/'single.json',watch_mode='symbols')
    assert p['subscriptions']==['000001'] and not p['members']
    p['sector_require_action']=False
    monitor=IntradayMonitor(p)
    at='2026-11-09T09:00:01+09:00'
    events=monitor.update(dict(symbol='000001', timestamp=at, price=112, previous_close=100,
                               day_low=99, cumulative_volume=100),at)
    assert events[0]['breakout_present'] is True
    assert all(events[0][key] is None for key in ('industry_action','entry_watch','entry_confirmed'))


def test_probe_only_logs_sanitized_acks_and_never_quotes_or_orders(tmp_path,monkeypatch):
    import websocket
    import trendfollow.kis_stream as stream
    config=tmp_path/'plan.json';config.write_text(json.dumps(plan()))
    monkeypatch.setattr(stream.Credentials,'load',lambda *a: stream.Credentials('kis','paper','SECRET_KEY','SECRET_VALUE'))
    monkeypatch.setattr(stream.QuoteClient,'_send',lambda *a,**kw: ({'approval_key':'PRIVATE_APPROVAL'},{}))
    sent=[]
    class Socket:
        def __init__(self):self.symbols=iter(plan()['subscriptions'])
        def send(self,value):sent.append(json.loads(value))
        def recv(self):return json.dumps({'header':{'tr_id':'H0STCNT0','tr_key':next(self.symbols)},'body':{'rt_cd':'0','output':{'key':'PRIVATE_KEY'}}})
        def close(self):pass
    monkeypatch.setattr(websocket,'create_connection',lambda *a,**kw:Socket())
    monkeypatch.setattr(stream.time,'sleep',lambda *a:None)
    root=tmp_path/'out'
    result=watch(config,root,seconds=10,probe=True,control_file=tmp_path/'control.json')
    assert result['subscription_acks']==5 and result['orders_sent']==0
    assert (root/'events.jsonl').read_text()==''
    assert all(s['body']['input']['tr_id']=='H0STCNT0' for s in sent)
    for path in root.iterdir():
        content=path.read_text(encoding='utf-8')
        assert 'PRIVATE' not in content and 'SECRET' not in content


def test_synthetic_plan_cannot_be_used_for_live_subscription(tmp_path):
    p=plan();p['synthetic']=True
    config=tmp_path/'plan.json';config.write_text(json.dumps(p))
    with pytest.raises(ValueError,match='재생 전용'):
        watch(config,tmp_path/'live',probe=True,control_file=tmp_path/'control.json')
