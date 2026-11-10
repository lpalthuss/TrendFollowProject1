from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from trendfollow.demo import generate
from trendfollow.strategy import Rules
from trendfollow.workflow import paper_checkpoint,read_inputs,paper_input_digest
from trendfollow.revisions import (DataRevisionError,report_revision,approve_revision,set_policy,
    snapshot_path,load_snapshot,previous_inputs,read_json)

def integer_demo(root):
    generate(root)
    p=pd.read_csv(root/'prices.csv',dtype={'symbol':str})
    p[['open','high','low','close']]=p[['open','high','low','close']].round(0)
    p.to_csv(root/'prices.csv',index=False)

@pytest.fixture
def book(tmp_path):
    integer_demo(tmp_path)
    config=tmp_path/'config.json';config.write_text(json.dumps({'rules':{'min_turnover':1e20},'limits':{}}))
    state=tmp_path/'book/state.json'
    paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-05',state,config_path=config)
    return tmp_path,config,state

def revise(root,field='volume',value=None):
    p=pd.read_csv(root/'prices.csv',dtype={'symbol':str})
    index=p[p.date.eq('2026-11-05')&p.symbol.eq('DEMO01')].index[0]
    p.loc[index,field]=p.loc[index,field]+1 if value is None else value
    p.to_csv(root/'prices.csv',index=False)

def report(book):
    root,config,state=book
    return report_revision(root/'prices.csv',root/'universe.csv',config,state,'2026-11-06')

def approve(book,r,**extra):
    root,config,state=book
    return approve_revision(state,r['id'],root/'prices.csv',root/'universe.csv',config,'2026-11-06',**extra)

def resume(book,r):
    root,config,state=book
    return paper_checkpoint(root/'prices.csv',root/'universe.csv','2026-11-06',state,config_path=config,revision_id=r['id'])

def test_report_is_complete_and_does_not_mutate_account_or_old_decisions(book):
    root,config,state=book;before=state.read_bytes();old_signal=(state.parent/'2026-11-05/signals.csv').read_bytes()
    revise(root);r=report(book)
    assert r['price_changes']==1 and r['peer_changes']==0 and r['can_continue']
    assert r['changed_dates']==['2026-11-05'] and r['policy']=='manual'
    assert state.read_bytes()==before
    frame=pd.read_csv(state.parent/'revisions'/r['id']/'changes.csv')
    assert list(frame.field)==['volume'] and frame.new_value.iloc[0]-frame.old_value.iloc[0]==1
    approve(book,r);assert state.read_bytes()==before
    result=resume(book,r)
    assert result['as_of']=='2026-11-06'
    assert (state.parent/'2026-11-05/signals.csv').read_bytes()==old_signal
    new=read_json(state);assert new['revision_lineage'][0]['report_id']==r['id']
    assert read_json(state.parent/'revisions'/r['id']/'before-state.json')['last_date']=='2026-11-05'
    assert (state.parent/'revisions'/r['id']/'applied.json').exists()

def test_no_fingerprint_bypass_without_review_receipt(book):
    root,config,state=book;revise(root);r=report(book);before=state.read_bytes()
    with pytest.raises(FileNotFoundError):resume(book,r)
    assert state.read_bytes()==before

def test_unreviewed_revisions_fail_with_report_and_account_preserved(book):
    root,config,state=book;before=state.read_bytes();revise(root)
    with pytest.raises(DataRevisionError) as error:
        paper_checkpoint(root/'prices.csv',root/'universe.csv','2026-11-06',state,config_path=config)
    assert error.value.report['price_changes']==1 and state.read_bytes()==before

def test_source_change_after_review_invalidates_approval(book):
    root,config,state=book;revise(root);r=report(book);approve(book,r);revise(root)
    with pytest.raises(ValueError,match='입력이 바뀌'):resume(book,r)

def test_price_revision_requires_explicit_price_review_and_never_volume_auto(book):
    root,config,state=book;revise(root,'close');set_policy(state,'volume_only');r=report(book)
    assert r['price_fields_changed'] and not r['automatic_eligible']
    with pytest.raises(ValueError,match='OHLC'):approve(book,r)
    approve(book,r,price_reviewed=True)
    assert resume(book,r)['status']=='processed'

def test_existing_position_price_basis_changes_are_blocked_even_after_review(book):
    root,config,state=book
    data=read_json(state);data['positions']={'DEMO01':{'quantity':1}};state.write_text(json.dumps(data))
    revise(root,'close');r=report(book)
    assert not r['can_continue'] and r['active_price_changes']==['DEMO01']
    with pytest.raises(ValueError,match='기업행사'):approve(book,r,price_reviewed=True)

def test_volume_only_auto_policy_is_opt_in_and_audited(book):
    root,config,state=book;set_policy(state,'volume_only');revise(root)
    paper_checkpoint(root/'prices.csv',root/'universe.csv','2026-11-06',state,config_path=config)
    data=read_json(state);key=data['revision_lineage'][0]['report_id']
    assert read_json(state.parent/'revisions'/key/'approval.json')['automatic']
    assert (state.parent/'revisions'/key/'changes.csv').exists()

def test_missing_old_complete_snapshot_refuses_partial_recovery(book):
    root,config,state=book;old=read_json(state)
    (snapshot_path(state,old['input_fingerprint'])/'manifest.json').unlink()
    revise(root)
    with pytest.raises(ValueError,match='부분 비교'):report(book)

def test_added_or_removed_old_rows_require_review_outside_continue(book):
    root,config,state=book
    p=pd.read_csv(root/'prices.csv',dtype={'symbol':str});p=p.drop(p[p.date.eq('2026-11-05')&p.symbol.eq('DEMO01')].index)
    p.to_csv(root/'prices.csv',index=False);r=report(book)
    assert r['missing_rows'] and not r['can_continue']

def test_original_pending_plan_is_used_even_if_revised_old_signal_would_disappear(tmp_path):
    integer_demo(tmp_path);prices=tmp_path/'prices.csv';universe=tmp_path/'universe.csv';config=tmp_path/'config.json'
    config.write_text('{}');state=tmp_path/'book/state.json'
    paper_checkpoint(prices,universe,'2026-11-06',state,config_path=config)
    original=read_json(state);assert original['pending']
    old_bytes=(state.parent/'2026-11-06/signals.csv').read_bytes()
    p=pd.read_csv(prices,dtype={'symbol':str});new=p.groupby('symbol').tail(1).copy();new['date']='2026-11-09'
    new['open']=new.close;new['high']=new.close*1.01;new['low']=new.close*.99
    p.loc[p.date.eq('2026-11-06'),'volume']=0
    pd.concat([p,new],ignore_index=True).to_csv(prices,index=False)
    r=report_revision(prices,universe,config,state,'2026-11-09')
    approve_revision(state,r['id'],prices,universe,config,'2026-11-09')
    paper_checkpoint(prices,universe,'2026-11-09',state,config_path=config,revision_id=r['id'])
    orders=pd.read_csv(state.parent/'2026-11-09/orders.csv')
    assert set(orders.symbol)=={x['symbol'] for x in original['pending']}
    assert orders.status.eq('filled').all()
    assert (state.parent/'2026-11-06/signals.csv').read_bytes()==old_bytes

def test_peer_changes_are_reported_separately_and_not_volume_auto(tmp_path):
    generate(tmp_path);config=tmp_path/'config.json';config.write_text(json.dumps({'rules':{'entry_method':'setup','min_turnover':1e20}}))
    u=pd.read_csv(tmp_path/'universe.csv',dtype={'symbol':str});peer=tmp_path/'peers.csv'
    rows=[]
    for day in ['2026-11-05','2026-11-06']:
        for r in u.to_dict('records'):rows.append(dict(date=day,symbol=r['symbol'],sector=r['sector'],day_return=.02,benchmark_day_return=.01,quote_eligible=1))
    pd.DataFrame(rows).to_csv(peer,index=False);u['industry_peer_history']=str(peer);u.to_csv(tmp_path/'universe.csv',index=False)
    state=tmp_path/'book/state.json';paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-05',state,config_path=config)
    set_policy(state,'volume_only');p=pd.read_csv(peer);p.loc[0,'day_return']=.03;p.to_csv(peer,index=False)
    r=report_revision(tmp_path/'prices.csv',tmp_path/'universe.csv',config,state,'2026-11-06')
    assert r['peer_changes']==1 and r['price_changes']==0 and not r['automatic_eligible']
    approve_revision(state,r['id'],tmp_path/'prices.csv',tmp_path/'universe.csv',config,'2026-11-06')
    p.loc[1,'day_return']=.04;p.to_csv(peer,index=False)
    with pytest.raises(ValueError,match='산업 동료'):
        paper_checkpoint(tmp_path/'prices.csv',tmp_path/'universe.csv','2026-11-06',state,config_path=config,revision_id=r['id'])
