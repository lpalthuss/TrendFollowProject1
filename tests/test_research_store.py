import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from trendfollow.market import acquire_research,NaverResearchSource
from trendfollow.research_store import publish_research,recover_research,require_published_research,JOURNAL


def bars(symbol):
    return pd.DataFrame([dict(date='2026-11-06',symbol=symbol,open=100.,high=101.,low=99.,close=100.,volume=1000.)])


@pytest.fixture
def files(tmp_path,monkeypatch):
    universe=tmp_path/'universe.csv'
    pd.DataFrame([dict(symbol='000001',name='A',sector='S',benchmark='069500')]).to_csv(universe,index=False)
    root=tmp_path/'history';root.mkdir()
    old_prices=bars('000001').to_csv(index=False).encode('utf-8')
    old_manifest=b'{"prior_valid_snapshot":true}'
    (root/'prices.csv').write_bytes(old_prices);(root/'manifest.json').write_bytes(old_manifest)
    monkeypatch.setattr(NaverResearchSource,'daily',lambda self,symbol,*a,**k:bars(symbol))
    return universe,root,old_prices,old_manifest


def unchanged(files):
    _,root,prices,manifest=files
    assert (root/'prices.csv').read_bytes()==prices
    assert (root/'manifest.json').read_bytes()==manifest


def acquire(files,**kwargs):
    u,root,*_=files
    return acquire_research(u,root,'2026-11-01','2026-11-06',**kwargs)


def test_partial_failure_preserves_both_previous_files_and_records_attempt(files,monkeypatch):
    def source(self,symbol,*args,**kwargs):
        if symbol=='069500':raise ValueError('controlled missing source')
        return bars(symbol)
    monkeypatch.setattr(NaverResearchSource,'daily',source)
    result=acquire(files)
    unchanged(files)
    assert not result['published'] and result['status']=='failed' and len(result['errors'])==1
    assert json.loads(Path(result['attempt_report']).read_text(encoding='utf-8'))==result


def test_all_failed_preserves_previous_files(files,monkeypatch):
    def source(*args,**kwargs):raise ValueError('no source')
    monkeypatch.setattr(NaverResearchSource,'daily',source)
    with pytest.raises(ValueError,match='수집된 데이터가 없습니다'):acquire(files)
    unchanged(files)
    assert len(list((files[1]/'acquisition-attempts').glob('*.json')))==1


def test_first_failed_acquisition_does_not_create_active_files(files,monkeypatch,tmp_path):
    root=tmp_path/'new'
    def source(self,symbol,*args,**kwargs):
        if symbol=='069500':raise ValueError('missing')
        return bars(symbol)
    monkeypatch.setattr(NaverResearchSource,'daily',source)
    result=acquire_research(files[0],root,'2026-11-01','2026-11-06')
    assert not result['published']
    assert not (root/'prices.csv').exists() and not (root/'manifest.json').exists()


@pytest.mark.parametrize('kind',['empty','wrong_symbol','duplicate','missing_field','nonfinite','outside_range'])
def test_invalid_response_is_not_published(files,monkeypatch,kind):
    def source(self,symbol,*args,**kwargs):
        frame=bars(symbol)
        if symbol=='000001':
            if kind=='empty':frame=frame.iloc[:0]
            elif kind=='wrong_symbol':frame['symbol']='000002'
            elif kind=='duplicate':frame=pd.concat([frame,frame])
            elif kind=='missing_field':frame=frame.drop(columns='volume')
            elif kind=='nonfinite':frame['close']=float('nan')
            else:frame['date']='2026-10-30'
        return frame
    monkeypatch.setattr(NaverResearchSource,'daily',source)
    result=acquire(files)
    unchanged(files)
    assert result['errors'] and not result['published']


def test_daily_requires_current_benchmark_but_research_allows_holiday_end(files,monkeypatch):
    def old(self,symbol,*args,**kwargs):
        f=bars(symbol);f['date']='2026-11-05';return f
    monkeypatch.setattr(NaverResearchSource,'daily',old)
    failed=acquire(files,required_session='2026-11-06')
    unchanged(files)
    assert not failed['published'] and failed['errors'][0]['symbol']=='benchmarks'
    # No hard-coded weekday assumption: optional research end may be a holiday.
    published=acquire_research(files[0],files[1]/'holiday','2026-11-01','2026-11-06')
    assert published['published']


def test_success_replaces_both_files_and_links_prices_checksum(files):
    result=acquire(files,required_session='2026-11-06')
    root=files[1]
    assert result['published'] and not result['errors']
    assert set(pd.read_csv(root/'prices.csv',dtype={'symbol':str}).symbol)=={'000001','069500'}
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    assert manifest==result and result['prices_sha256']==hashlib.sha256((root/'prices.csv').read_bytes()).hexdigest()
    assert not (root/JOURNAL).exists() and not list(root.glob('.research-stage-*'))


def test_nonempty_but_truncated_history_does_not_replace_existing_data(files):
    _,root,previous,old_manifest=files
    f=bars('000001');older=f.copy();older['date']='2026-11-05'
    expected=pd.concat([older,f]).to_csv(index=False).encode('utf-8')
    (root/'prices.csv').write_bytes(expected)
    result=acquire(files)
    assert not result['published'] and result['errors'][0]['symbol']=='history'
    assert (root/'prices.csv').read_bytes()==expected
    assert (root/'manifest.json').read_bytes()==old_manifest


def test_raw_zero_bars_are_preserved_for_quarantine(files,monkeypatch):
    def source(self,symbol,*args,**kwargs):
        f=bars(symbol)
        if symbol=='000001':f[['open','high','low','close','volume']]=0
        return f
    monkeypatch.setattr(NaverResearchSource,'daily',source)
    result=acquire(files)
    assert result['published']
    f=pd.read_csv(files[1]/'prices.csv',dtype={'symbol':str})
    assert f.loc[f.symbol=='000001','close'].iloc[0]==0


def test_csv_serialization_failure_never_touches_active_files(files,monkeypatch):
    def failed(*a,**k):raise OSError('disk full')
    monkeypatch.setattr(pd.DataFrame,'to_csv',failed)
    with pytest.raises(OSError):acquire(files)
    unchanged(files)
    assert not (files[1]/JOURNAL).exists()


def test_second_replace_failure_rolls_back_first_replace(files,monkeypatch):
    original=os.replace;root=files[1]
    def replace(source,target):
        if Path(source).name=='manifest.json' and Path(target)==root/'manifest.json':
            raise PermissionError('controlled publication failure')
        return original(source,target)
    monkeypatch.setattr(os,'replace',replace)
    with pytest.raises(PermissionError):acquire(files)
    unchanged(files)
    assert not (root/JOURNAL).exists() and not list(root.glob('.research-stage-*'))
    attempt=json.loads(next((root/'acquisition-attempts').glob('*.json')).read_text(encoding='utf-8'))
    assert not attempt['published'] and attempt['status']=='publication_failed'


def test_success_log_write_failure_also_rolls_back_active_files(files,monkeypatch):
    import trendfollow.research_store as store
    original=store.write_json
    def failed_log(path,value):
        if path.parent.name=='acquisition-attempts':raise OSError('audit disk full')
        return original(path,value)
    monkeypatch.setattr(store,'write_json',failed_log)
    with pytest.raises(OSError):acquire(files)
    unchanged(files)
    assert not (files[1]/JOURNAL).exists()


def test_backup_cleanup_failure_does_not_misreport_successful_publication(files,monkeypatch):
    import trendfollow.research_store as store
    def failed(*args):raise PermissionError('cleanup temporarily blocked')
    monkeypatch.setattr(store,'clean_stage',failed)
    result=acquire(files)
    assert result['published']
    assert json.loads((files[1]/'manifest.json').read_text(encoding='utf-8'))==result
    assert not (files[1]/JOURNAL).exists()
    require_published_research(files[1]/'prices.csv')


def test_interrupted_rollback_blocks_readers_and_next_run_recovers(files,monkeypatch):
    original=os.replace;root=files[1]
    def replace(source,target):
        if Path(source).name in {'manifest.json','restore-prices.csv'}:raise PermissionError('temporarily blocked')
        return original(source,target)
    monkeypatch.setattr(os,'replace',replace)
    with pytest.raises(PermissionError):acquire(files)
    assert (root/JOURNAL).exists()
    with pytest.raises(ValueError,match='미완료'):require_published_research(root/'prices.csv')
    from trendfollow.workflow import read_inputs
    with pytest.raises(ValueError,match='미완료'):read_inputs(root/'prices.csv',files[0],'2026-11-06')
    monkeypatch.setattr(os,'replace',original)
    recover_research(root)
    unchanged(files)
    assert not (root/JOURNAL).exists()
    assert acquire(files)['published']


def test_rollback_on_first_publication_restores_absent_files(files,monkeypatch,tmp_path):
    original=os.replace;root=tmp_path/'new';root.mkdir()
    def replace(source,target):
        if Path(source).name=='manifest.json':raise OSError('controlled failure')
        return original(source,target)
    monkeypatch.setattr(os,'replace',replace)
    with pytest.raises(OSError):publish_research(root,bars('000001'),{'rows':1})
    assert not (root/'prices.csv').exists() and not (root/'manifest.json').exists()
    assert not (root/JOURNAL).exists()


def test_research_cli_reports_failure_instead_of_completion(files,monkeypatch,capsys):
    from trendfollow.cli import main
    def source(self,symbol,*args,**kwargs):
        if symbol=='069500':raise ValueError('missing')
        return bars(symbol)
    monkeypatch.setattr(NaverResearchSource,'daily',source)
    with pytest.raises(SystemExit) as error:
        main(['research-data','--universe',str(files[0]),'--start','2026-11-01','--end','2026-11-06','--out',str(files[1])])
    assert error.value.code==1
    output=capsys.readouterr()
    assert '수집 완료:' not in output.out and '기존 파일은 보존' in output.err
    unchanged(files)
