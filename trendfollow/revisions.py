"""Audited input revisions. Preserve prior decisions; never replace a fingerprint in place."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

import pandas as pd

PRICE_KEYS=['date','symbol']
PRICE_FIELDS=['open','high','low','close','volume']

class DataRevisionError(ValueError):
    def __init__(self,report):
        self.report=report
        super().__init__('이미 처리한 과거 일봉이 수정되었거나 산업 입력이 정정되어 처리를 중단했습니다. 계좌는 보존되었습니다. 변경 내역을 확인한 뒤 정정 반영 또는 별도 재계산 계좌를 선택하세요.')

def sha(path):
    if path is None:return None
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()

def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def write_json(path,value):
    from .observation_control import atomic_json
    atomic_json(Path(path),value)

def selected_prices(prices,universe,rules):
    from .relative_strength import mansfield_benchmark_symbol
    symbols=set(universe.symbol.astype(str))|set(universe.benchmark.astype(str))
    if rules.rs_method=='mansfield':symbols|={mansfield_benchmark_symbol(r,rules) for r in universe.to_dict('records')}
    if rules.market_units_enabled:
        from .market_units import market_symbol
        symbols|={market_symbol(r,rules) for r in universe.to_dict('records')}
    return prices[prices.symbol.isin(symbols)].copy()

def peer_frame(universe,rules,end):
    from .industries import load_peer_history,PEER_DECISION_COLUMNS
    if rules.entry_method!='setup' or 'industry_peer_history' not in universe:return None
    p=load_peer_history(universe)
    return p.loc[p.date<=pd.Timestamp(end),PEER_DECISION_COLUMNS].sort_values(['date','symbol']).copy()

def digest(prices,universe,rules,peers):
    from .workflow import paper_input_digest
    return paper_input_digest(prices,universe,rules,peer_history=peers)

def snapshot_path(state_path,value):
    if not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('입력 스냅샷 ID를 확인하세요.')
    return Path(state_path).parent/'inputs'/value[:20]

def snapshot_inputs(state_path,prices,universe,rules,peers=None,origin='checkpoint'):
    prices=selected_prices(prices,universe,rules)
    if peers is None:peers=peer_frame(universe,rules,prices.date.max())
    value=digest(prices,universe,rules,peers)
    home=snapshot_path(state_path,value)
    if (home/'manifest.json').exists():
        _,_,meta=load_snapshot(home)
        if meta['digest']!=value:raise ValueError('입력 스냅샷 ID 충돌을 확인하세요.')
        return value
    home.mkdir(parents=True,exist_ok=True)
    prices.to_csv(home/'prices.csv.gz',index=False,compression='gzip')
    files={'prices.csv.gz':sha(home/'prices.csv.gz')}
    if peers is not None:
        peers.to_csv(home/'peers.csv.gz',index=False,compression='gzip')
        files['peers.csv.gz']=sha(home/'peers.csv.gz')
    write_json(home/'manifest.json',dict(version=1,digest=value,end=str(prices.date.max())[:10],origin=origin,files=files,
        created_at=datetime.now(timezone.utc).isoformat()))
    return value

def load_snapshot(home):
    home=Path(home);meta=read_json(home/'manifest.json')
    for name,value in meta['files'].items():
        if name not in {'prices.csv.gz','peers.csv.gz'} or sha(home/name)!=value:raise ValueError('보존 입력 스냅샷의 무결성 확인에 실패했습니다.')
    p=pd.read_csv(home/'prices.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    peers=None
    if 'peers.csv.gz' in meta['files']:
        peers=pd.read_csv(home/'peers.csv.gz',dtype={'symbol':str,'sector':str},keep_default_na=False,float_precision='round_trip')
        peers['date']=pd.to_datetime(peers.date)
    return p,peers,meta

def previous_inputs(state_path,universe,rules):
    state=read_json(state_path);home=snapshot_path(state_path,state['input_fingerprint'])
    if (home/'manifest.json').exists():
        p,peers,meta=load_snapshot(home)
        if digest(p,universe,rules,peers)!=state['input_fingerprint']:raise ValueError('보존 입력과 계좌의 입력 지문이 다릅니다.')
        return p,peers,meta
    # Legacy accounts: only existing, dated raw caches; never query the network here.
    from .market import NaverResearchSource
    source=Path(state['source']);cache=source.parent/'raw';end=state['last_date']
    current=pd.read_csv(source,dtype={'symbol':str})
    relevant=selected_prices(current,universe,rules)
    symbols=sorted(relevant.symbol.unique())
    missing=[s for s in symbols if not (cache/f'{s}-{end}.xml').is_file()]
    if missing:raise ValueError(f'변경 전 전체 입력 스냅샷·원본 캐시가 부족합니다 ({len(missing)}종목). 부분 비교만으로 복구를 허용하지 않습니다.')
    client=NaverResearchSource(cache)
    candidates=pd.concat([client.daily(s,'1900-01-01',end) for s in symbols],ignore_index=True).sort_values(['symbol','date'])
    attempts=list((source.parent/'acquisition-attempts').glob('*.json'))
    starts={str(relevant.date.min())}
    for file in attempts:
        meta=read_json(file)
        if meta.get('end')==end and meta.get('published'):starts.add(meta['start'])
    peers=peer_frame(universe,rules,end)
    peer_candidates=[peers]
    if peers is not None:
        paths=set(universe.industry_peer_history.dropna().astype(str))-{''}
        if len(paths)==1:
            root=Path(next(iter(paths))).parent/'snapshots'
            frames=[]
            for file in sorted(root.glob('*/peer-returns.csv')):
                if file.parent.name<=end:
                    f=pd.read_csv(file,dtype={'symbol':str,'sector':str},keep_default_na=False)
                    f['date']=pd.to_datetime(f.date);frames.append(f)
            if frames:
                from .industries import PEER_DECISION_COLUMNS
                peer_candidates.append(pd.concat(frames)[PEER_DECISION_COLUMNS].sort_values(['date','symbol']))
    for start in sorted(starts):
        p=candidates[candidates.date>=start]
        # Match old CSV parsing conventions, including floating point values.
        from io import StringIO
        p=pd.read_csv(StringIO(p.to_csv(index=False)),dtype={'symbol':str})
        for old_peers in peer_candidates:
            if digest(p,universe,rules,old_peers)==state['input_fingerprint']:
                key=snapshot_inputs(state_path,p,universe,rules,old_peers,origin='verified_legacy_raw_cache')
                return load_snapshot(snapshot_path(state_path,key))
    raise ValueError('과거 원본 캐시를 복원했지만 계좌 입력 지문과 일치하지 않습니다. 정정 범위를 확정할 수 없어 복구를 보류합니다.')

def changes(old,new,source,fields):
    old=old.copy();new=new.copy()
    for f in (old,new):f['date']=f.date.astype(str).str[:10]
    if old.duplicated(PRICE_KEYS).any() or new.duplicated(PRICE_KEYS).any():raise ValueError('대조 입력에 중복 날짜·종목이 있습니다.')
    joined=old.set_index(PRICE_KEYS)[fields].join(new.set_index(PRICE_KEYS)[fields],how='outer',lsuffix='_old',rsuffix='_new')
    result=[]
    for field in fields:
        a,b=joined[field+'_old'],joined[field+'_new']
        mask=~(a.eq(b)|(a.isna()&b.isna()))
        for (date,symbol),row in joined[mask].iterrows():
            ov,nv=row[field+'_old'],row[field+'_new']
            result.append(dict(source=source,date=date,symbol=symbol,field=field,
                old_value=None if pd.isna(ov) else ov,new_value=None if pd.isna(nv) else nv))
    return result

def report_revision(prices_path,universe_path,config_path,state_path,as_of):
    from .workflow import read_inputs
    from dataclasses import asdict
    from .industries import decision_universe_records
    from .relative_strength import decision_rule_settings
    state_path=Path(state_path);state=read_json(state_path)
    p,u,r,limits=read_inputs(prices_path,universe_path,as_of,config_path)
    settings=dict(universe=decision_universe_records(u),rules=decision_rule_settings(r),limits=asdict(limits),capital=state['initial_cash'])
    if hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()!=state['config_fingerprint']:
        raise ValueError('계좌 설정·유니버스가 변경되었습니다. 데이터 정정 복구로 전략 변경을 반영할 수 없습니다.')
    prior=selected_prices(p[p.date<=state['last_date']],u,r)
    peers=peer_frame(u,r,state['last_date'])
    new_digest=digest(prior,u,r,peers)
    if new_digest==state['input_fingerprint']:return dict(status='unchanged',account_as_of=state['last_date'])
    previous,old_peers,origin=previous_inputs(state_path,u,r)
    diff=changes(previous,prior,'prices',PRICE_FIELDS)
    if old_peers is not None:
        from .industries import PEER_DECISION_COLUMNS
        diff+=changes(old_peers,peers,'peers',[c for c in PEER_DECISION_COLUMNS if c not in PRICE_KEYS])
    if not diff:raise ValueError('수치 변경 없이 입력 지문이 달라졌습니다. 정렬·스키마 변경은 별도 확인이 필요합니다.')
    key=uuid.uuid4().hex;home=state_path.parent/'revisions'/key;home.mkdir(parents=True)
    new_snapshot=snapshot_inputs(state_path,p,u,r,origin='revision_candidate')
    frame=pd.DataFrame(diff)
    frame.to_csv(home/'changes.csv',index=False,encoding='utf-8-sig')
    write_json(home/'before-state.json',state)
    day=state_path.parent/state['last_date'];saved={}
    for file in day.glob('*'):
        if file.is_file():
            destination=home/'before-decision'/file.name;destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(file,destination);saved[file.name]=sha(destination)
    price_changes=[x for x in diff if x['source']=='prices' and x['field'] in PRICE_FIELDS[:-1]]
    active=set(state['positions'])|{str(row['symbol']) for row in state['pending']}
    affected=sorted(active&{x['symbol'] for x in price_changes})
    missing_rows=any(x['old_value'] is None or x['new_value'] is None for x in diff)
    policy=read_policy(state_path)
    report=dict(id=key,status='review_required',as_of=as_of,account_as_of=state['last_date'],
        old_digest=state['input_fingerprint'],new_prior_digest=new_digest,new_snapshot=new_snapshot,
        state_sha256=sha(state_path),prices_sha256=sha(prices_path),universe_sha256=sha(universe_path),config_sha256=sha(config_path),
        price_changes=len([x for x in diff if x['source']=='prices']),peer_changes=len([x for x in diff if x['source']=='peers']),
        changed_symbols=len(set(x['symbol'] for x in diff)),changed_dates=sorted(set(x['date'] for x in diff)),
        price_fields_changed=bool(price_changes),missing_rows=missing_rows,active_price_changes=affected,
        can_continue=not affected and not missing_rows and as_of>state['last_date'],
        block_reason='이미 처리한 날짜는 원계좌에서 다시 계산하지 않습니다. 별도 재계산 계좌를 만드세요.' if as_of<=state['last_date'] else
            '보유·진입대기 종목의 가격 기준이 바뀌었습니다. 기업행사·수량·손절 기준을 확인해야 합니다.' if affected else
            '과거 행의 추가·삭제가 있습니다. 자동 복구를 보류하고 데이터 누락·기업행사를 확인하세요.' if missing_rows else None,
        policy=policy,automatic_eligible=policy=='volume_only' and all(x['source']=='prices' and x['field']=='volume' for x in diff) and not missing_rows,
        baseline_origin=origin['origin'],before_decision_files=saved,changes_sha256=sha(home/'changes.csv'),
        created_at=datetime.now(timezone.utc).isoformat(),row_changes=len(diff),
        explanation='이전 결정·보유·진입 대기는 보존합니다. 정정 자료는 이후 신호 계산에만 사용합니다. 원천 정정의 원인은 아직 확정되지 않았습니다.')
    write_json(home/'report.json',report)
    return report

def report_path(state_path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('정정 보고서 ID를 확인하세요.')
    return Path(state_path).parent/'revisions'/key

def validate_report(state_path,key,prices_path,universe_path,config_path,as_of):
    home=report_path(state_path,key);report=read_json(home/'report.json')
    if report['as_of']!=as_of or report['state_sha256']!=sha(state_path):raise ValueError('계좌 또는 처리일이 달라졌습니다. 변경 대조를 다시 실행하세요.')
    for field,path in [('prices_sha256',prices_path),('universe_sha256',universe_path),('config_sha256',config_path),('changes_sha256',home/'changes.csv')]:
        if sha(path)!=report[field]:raise ValueError('대조 이후 입력이 바뀌었습니다. 변경 대조를 다시 실행하세요.')
    for name,value in report['before_decision_files'].items():
        if sha(home/'before-decision'/name)!=value or sha(Path(state_path).parent/report['account_as_of']/name)!=value:
            raise ValueError('보존한 과거 결정 기록이 바뀌었습니다. 복구를 중단합니다.')
    from .workflow import read_inputs
    p,u,r,_=read_inputs(prices_path,universe_path,as_of,config_path)
    prior=p[p.date<=report['account_as_of']]
    if digest(prior,u,r,peer_frame(u,r,report['account_as_of']))!=report['new_prior_digest']:raise ValueError('산업 동료 자료가 바뀌었습니다. 변경 대조를 다시 실행하세요.')
    candidate,peers,_=load_snapshot(snapshot_path(state_path,report['new_snapshot']))
    if digest(p,u,r,peer_frame(u,r,as_of))!=digest(candidate,u,r,peers):raise ValueError('후보 스냅샷과 현재 입력이 다릅니다. 변경 대조를 다시 실행하세요.')
    if as_of<=report['account_as_of']:raise ValueError('이미 기록한 날짜를 다시 처리할 수 없습니다. 별도 재계산 계좌를 만드세요.')
    if not report['can_continue']:raise ValueError(report['block_reason'])
    return report

def approve_revision(state_path,key,prices_path,universe_path,config_path,as_of,price_reviewed=False,automatic=False):
    report=validate_report(state_path,key,prices_path,universe_path,config_path,as_of)
    if report['price_fields_changed'] and price_reviewed is not True:raise ValueError('OHLC 가격 정정 내역과 가격·수량 기준을 확인했다는 표시가 필요합니다.')
    if automatic and not report['automatic_eligible']:raise ValueError('이 정정은 선택한 자동 반영 정책 대상이 아닙니다.')
    receipt=dict(report_id=key,report_sha256=sha(report_path(state_path,key)/'report.json'),
        automatic=automatic,price_reviewed=bool(price_reviewed),approved_at=datetime.now(timezone.utc).isoformat())
    write_json(report_path(state_path,key)/'approval.json',receipt)
    return report

def validated_approval(state_path,key,prices_path,universe_path,config_path,as_of):
    report=validate_report(state_path,key,prices_path,universe_path,config_path,as_of)
    receipt=read_json(report_path(state_path,key)/'approval.json')
    if receipt['report_sha256']!=sha(report_path(state_path,key)/'report.json'):raise ValueError('정정 승인 기록의 무결성을 확인하세요.')
    return report

def read_policy(state_path):
    file=Path(state_path).parent/'revision-policy.json'
    return read_json(file)['policy'] if file.exists() else 'manual'

def set_policy(state_path,policy):
    if policy not in {'manual','volume_only'}:raise ValueError('지원하지 않는 정정 정책입니다.')
    write_json(Path(state_path).parent/'revision-policy.json',dict(policy=policy,updated_at=datetime.now(timezone.utc).isoformat()))
    return dict(policy=policy)
