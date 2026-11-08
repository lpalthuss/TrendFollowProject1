"""Initial stop selection. Repricing is allowed only before opening a position."""
from dataclasses import asdict,replace
import json
import math
from pathlib import Path

import pandas as pd

SETTINGS=('initial_stop_method','initial_stop_reference','fixed_stop_fraction')


def initial_stop(price,atr,multiplier=2,fixed_fraction=.08,method='atr'):
    if method not in {'fixed','atr','hybrid'}:raise ValueError('초기 손절 방식은 fixed/atr/hybrid여야 합니다.')
    if not math.isfinite(multiplier) or multiplier<=0:raise ValueError('ATR 배수는 양수여야 합니다.')
    if not math.isfinite(fixed_fraction) or not 0<fixed_fraction<1:raise ValueError('고정 손절 폭은 0 초과 1 미만이어야 합니다.')
    if not math.isfinite(price) or price<=0:raise ValueError('초기 손절 기준 가격이 유효하지 않습니다.')
    fixed=price*fixed_fraction
    if method=='fixed':distance,branch=fixed,'fixed'
    else:
        if not math.isfinite(atr) or atr<0:raise ValueError('초기 손절 ATR이 유효하지 않습니다.')
        distance,branch=atr*multiplier,'atr'
        if method=='hybrid' and fixed>=distance:distance,branch=fixed,'fixed'
    return {'stop_price':price-distance,'initial_stop_branch':branch,'initial_stop_distance':distance,
            'stop_fraction':distance/price}


def planning_stops(frame,rules):
    """Signal-close estimates; entry-fill policies are finalized by allocate()."""
    f=frame.copy()
    if 'reference_price' not in f:f['reference_price']=float('nan')
    if 'atr' not in f:f['atr']=float('nan')
    f['initial_stop_method']=rules.initial_stop_method
    f['initial_stop_reference']=rules.initial_stop_reference
    f['fixed_stop_fraction']=rules.fixed_stop_fraction
    f['stop_atr_multiple']=rules.stop_atr
    f['max_stop_fraction']=rules.max_stop_fraction
    fixed=f.reference_price*rules.fixed_stop_fraction
    if rules.initial_stop_method=='fixed':
        distance=fixed;branch=pd.Series('fixed',index=f.index)
    else:
        distance=f.atr*rules.stop_atr;branch=pd.Series('atr',index=f.index)
        if rules.initial_stop_method=='hybrid':
            branch.loc[fixed>=distance]='fixed'
            distance=pd.concat([fixed,distance],axis=1).max(axis=1)
            distance=distance.where(f.atr.notna())
    distance=distance.astype(float)
    f['stop_price']=(f.reference_price-distance).astype(float)
    f['initial_stop_branch']=branch
    f['initial_stop_distance']=distance
    return f


def compare_initial_stops(prices_path,universe_path,account_path,as_of,config_path,output):
    from .research_store import require_published_research
    require_published_research(prices_path)
    from .strategy import Rules,screen
    from .risk import Account,Limits,allocate
    root=Path(output)
    if root.exists():raise FileExistsError('비교 출력 폴더가 이미 있습니다.')
    p=pd.read_csv(prices_path,dtype={'symbol':str})
    u=pd.read_csv(universe_path,dtype={'symbol':str,'benchmark':str},keep_default_na=False)
    account=Account(**json.loads(Path(account_path).read_text(encoding='utf-8-sig')))
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig')) if config_path else {}
    if set(config)-{'rules','limits'}:raise ValueError('config에는 rules,limits만 지정할 수 있습니다.')
    base=Rules(**config.get('rules',{}));limits=Limits(**config.get('limits',{}))
    base.validate();limits.validate()
    settings={};results={}
    for method in ['fixed','atr','hybrid']:
        rules=replace(base,initial_stop_method=method,initial_stop_reference='entry_fill')
        settings[method]=asdict(rules)
        results[method]=allocate(screen(p,u,as_of,rules),account,limits)
    root.mkdir(parents=True)
    merged=u[['symbol','name']].copy()
    cols=['signal','quantity','stop_price','stop_fraction','initial_stop_branch',
          'estimated_entry_price','planned_risk','cash_required','allocation_reason','reasons']
    for method,result in results.items():
        result.to_csv(root/f'candidates-{method}.csv',index=False,encoding='utf-8-sig')
        renamed=result[['symbol',*cols]].rename(columns={c:f'{c}_{method}' for c in cols})
        merged=merged.merge(renamed,on='symbol',validate='one_to_one')
    merged.to_csv(root/'comparison.csv',index=False,encoding='utf-8-sig')
    summary={'as_of':as_of,'mode':'planning_only','universe_size':len(u),'rules':settings,'limits':asdict(limits),
        'candidates':{m:int(f.signal.sum()) for m,f in results.items()},
        'allocated':{m:int((f.quantity>0).sum()) for m,f in results.items()},
        'note':'동일 계좌의 독립 비교이며 수량 합산 금지. 실제 다음 시가에서 다시 확정한다.'}
    (root/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary
