from dataclasses import replace
import json

import pandas as pd
import pytest

from trendfollow.industries import IndustrySource, apply_overrides, decision_universe_records, load_peer_history
from trendfollow.setup import sector_features
from trendfollow.strategy import Rules
from trendfollow.workflow import paper_input_digest


class Response:
    def __init__(self,body):self.body=body
    def raise_for_status(self):pass
    def json(self):return self.body


class Session:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return Response(next(self.responses))


def member(symbol,market):
    return {'itemCode':symbol,'itemName':symbol,'marketType':market,'stockEndType':'stock',
            'krx':{'changeRate':'1.0','tradingVolume':'100','tradableStatus':'tradable'}}


def test_source_pagination_collects_kospi_and_kosdaq_together(tmp_path):
    session=Session([
        {'items':[{'code':'1','name':'반도체'}],'hasNext':False},
        {'items':[member('005930','KOSPI')],'hasNext':True,'cursor':'next'},
        {'items':[member('035900','KOSDAQ'),dict(member('069500','KOSPI'),stockEndType='etf')],
         'hasNext':False}])
    source=IndustrySource(tmp_path,session=session,interval=0)
    rows,conflicts,groups=source.collect()
    assert set(rows.market)=={'KOSPI','KOSDAQ'}
    assert set(rows.industry_code)=={'1'} and len(rows)==2
    assert conflicts.empty and groups==1
    assert session.calls[-1][1]['params']['cursor']=='next'
    assert set(rows.source)=={'NAVER_INDUSTRY'}


def test_repeated_cursor_is_rejected(tmp_path):
    source=IndustrySource(tmp_path,session=Session([
        {'items':[],'hasNext':True,'cursor':'repeat'},
        {'items':[],'hasNext':True,'cursor':'repeat'}]),interval=0)
    with pytest.raises(ValueError,match='진행되지'):
        source.pages('https://example.com',{},'test')


def test_conflicting_memberships_are_quarantined(tmp_path):
    source=IndustrySource(tmp_path,session=Session([
        {'items':[{'code':'1','name':'A'},{'code':'2','name':'B'}],'hasNext':False},
        {'items':[member('005930','KOSPI')],'hasNext':False},
        {'items':[member('005930','KOSPI')],'hasNext':False}]),interval=0)
    clean,conflicts,_=source.collect()
    assert clean.empty and len(conflicts)==2


def classification():
    return pd.DataFrame([dict(symbol=s,industry_code='1',industry_name='Standard',source='NAVER_INDUSTRY',
        source_url='https://example.com/standard') for s in ['005930','035900']])


def test_business_overrides_preserve_standard_classification_and_reason():
    edits=pd.DataFrame([dict(symbol=s,industry_code='BATTERY',industry_name='배터리 소재',
        valid_from='2026-11-01',reason='사업보고서 주력 제품 확인',source_url='https://example.com/report')
        for s in ['005930','035900']])
    result=apply_overrides(classification(),edits,'2026-11-08')
    assert set(result.sector)=={'CUSTOM:BATTERY'}
    assert set(result.standard_industry_code)=={'1'}
    assert set(result.source)=={'MANUAL_OVERRIDE'}
    assert result.override_reason.str.len().min()>0
    future=edits.copy();future['valid_from']='2027-01-01'
    assert set(apply_overrides(classification(),future,'2026-11-08').sector)=={'NAVER:1'}
    edits.loc[0,'reason']=''
    with pytest.raises(ValueError,match='근거'):
        apply_overrides(classification(),edits,'2026-11-08')


def peer_inputs(tmp_path,peer_return=.01):
    # Candidate universe contains only A. Two valid peers are not buy candidates.
    peer=pd.DataFrame([dict(date='2026-11-06',symbol=s,sector='NAVER:1',day_return=r,
        benchmark_day_return=.001,quote_eligible=1,snapshot_observed_at='2026-11-08T13:00:00+09:00')
        for s,r in [('A',.5),('B',peer_return),('C',peer_return)]])
    path=tmp_path/'peers.csv';peer.to_csv(path,index=False)
    u=pd.DataFrame([dict(symbol='A',sector='NAVER:1',market='KOSPI',benchmark='INDEX',
                         industry_peer_history=str(path))])
    p=pd.DataFrame([dict(symbol='A',date='2026-11-05',open=100,high=101,low=99,close=100,volume=100),
                    dict(symbol='A',date='2026-11-06',open=150,high=151,low=149,close=150,volume=100),
                    dict(symbol='INDEX',date='2026-11-06',open=100,high=101,low=99,close=100,volume=100)])
    return p,u,peer,path


def test_industry_action_uses_broader_peers_without_self_bias(tmp_path):
    p,u,peer,path=peer_inputs(tmp_path)
    result=sector_features(p,u,Rules(sector_min_peers=2)).iloc[-1]
    assert result.sector_peer_count==2
    assert result.sector_up_fraction==1
    assert result.sector_excess_return==pytest.approx(.009)
    assert result.sector_action and result.sector_action_scope=='industry_snapshot'
    peer.loc[peer.symbol!='A','day_return']=-.01;peer.to_csv(path,index=False)
    result=sector_features(p,u,Rules(sector_min_peers=2)).iloc[-1]
    assert result.sector_up_fraction==0 and not result.sector_action


def test_missing_snapshot_never_falls_back_to_candidate_universe(tmp_path):
    p,u,_,_=peer_inputs(tmp_path)
    result=sector_features(p,u,Rules(sector_min_peers=1)).iloc[0]
    assert result.sector_action_scope=='missing_industry_snapshot'
    assert result.sector_peer_count==0 and not result.sector_action


def test_unclassified_stocks_are_not_combined_in_one_artificial_industry(tmp_path):
    p,u,_,_=peer_inputs(tmp_path)
    u['sector']='UNCLASSIFIED:A'
    result=sector_features(p,u,Rules(sector_min_peers=1)).iloc[-1]
    assert result.sector_peer_count==0 and not result.sector_action
    assert result.sector_action_scope=='unclassified_industry'


def test_invalid_or_untradable_peer_quotes_do_not_count(tmp_path):
    p,u,peer,path=peer_inputs(tmp_path)
    peer.loc[peer.symbol=='B','quote_eligible']=0
    peer.loc[peer.symbol=='C','day_return']=float('nan')
    peer.to_csv(path,index=False)
    result=sector_features(p,u,Rules(sector_min_peers=1)).iloc[-1]
    assert result.sector_peer_count==0 and not result.sector_action


def test_snapshot_corrections_affect_setup_digest_but_observation_times_do_not(tmp_path):
    p,u,peer,path=peer_inputs(tmp_path)
    rules=Rules(entry_method='setup')
    before=paper_input_digest(p,u,rules)
    peer['snapshot_observed_at']='2026-11-08T14:00:00+09:00';peer.to_csv(path,index=False)
    assert paper_input_digest(p,u,rules)==before
    peer.loc[peer.symbol=='B','day_return']=.04;peer.to_csv(path,index=False)
    assert paper_input_digest(p,u,rules)!=before


def test_classification_audit_timestamps_do_not_reset_account_but_sector_does():
    u=pd.DataFrame([dict(symbol='A',sector='NAVER:1',benchmark='INDEX',classification_observed_at='old')])
    old=decision_universe_records(u)
    u['classification_observed_at']='new'
    assert decision_universe_records(u)==old
    u['sector']='NAVER:2'
    assert decision_universe_records(u)!=old


def test_duplicate_peer_quotes_are_not_silently_accepted(tmp_path):
    _,u,peer,path=peer_inputs(tmp_path)
    pd.concat([peer,peer.iloc[:1]]).to_csv(path,index=False)
    with pytest.raises(ValueError,match='중복'):
        load_peer_history(u)


def test_current_industry_snapshot_is_not_a_historical_backtest_input(tmp_path):
    from trendfollow.industries import require_peer_sessions
    p,u,_,_=peer_inputs(tmp_path)
    benchmark=p[p.symbol=='INDEX'].iloc[:1].copy()
    benchmark['date']='2026-11-05'
    p=pd.concat([p,benchmark],ignore_index=True)
    with pytest.raises(ValueError,match='1거래일 부족'):
        require_peer_sessions(p,u,'2026-11-05','2026-11-06')
    require_peer_sessions(p,u,'2026-11-06','2026-11-06')
