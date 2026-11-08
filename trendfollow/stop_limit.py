"""Optional entry-distance cap, separate from stop placement and risk sizing."""
from dataclasses import asdict,replace
import json
from pathlib import Path

import pandas as pd


def apply_stop_limit(frame,rules):
    """Apply once to uncapped signals; never move a stop to fit the limit."""
    f=frame.copy()
    price=f.get('reference_price',pd.Series(float('nan'),index=f.index))
    stop=f.get('stop_price',pd.Series(float('nan'),index=f.index))
    f['stop_fraction']=(price-stop)/price
    f['stop_limit_enabled']=rules.stop_limit_enabled
    exceeded=f.stop_fraction>rules.max_stop_fraction
    f['stop_limit_exceeded']=exceeded
    if rules.stop_limit_enabled:
        for field in ['signal','signal_rs','signal_mansfield']:
            f.loc[exceeded,field]=False
        if 'reasons' in f:
            f.loc[exceeded,'reasons']=f.loc[exceeded,'reasons'].apply(
                lambda value:','.join(filter(None,[value,'stop_limit_exceeded'])))
    return f


def compare_stop_limit(prices_path,universe_path,account_path,as_of,config_path,output):
    from .strategy import Rules,screen
    from .risk import Account,Limits,allocate
    if Path(output).exists():raise FileExistsError('비교 출력 폴더가 이미 있습니다.')
    prices=pd.read_csv(prices_path,dtype={'symbol':str})
    universe=pd.read_csv(universe_path,dtype={'symbol':str,'benchmark':str},keep_default_na=False)
    account=Account(**json.loads(Path(account_path).read_text(encoding='utf-8-sig')))
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig')) if config_path else {}
    rules=Rules(**config.get('rules',{}));limits=Limits(**config.get('limits',{}))
    rules.validate();limits.validate()
    off=replace(rules,stop_limit_enabled=False);on=replace(rules,stop_limit_enabled=True)
    uncapped=screen(prices,universe,as_of,off)
    results={'on':allocate(apply_stop_limit(uncapped,on),account,limits),
             'off':allocate(uncapped,account,limits)}
    root=Path(output);root.mkdir(parents=True,exist_ok=False)
    columns=['symbol','signal','signal_rs','signal_mansfield','quantity','planned_risk','cash_required','allocation_reason','reasons']
    merged=results['off'].drop(columns=columns[1:]+['stop_limit_enabled']).copy()
    for label,result in results.items():
        result.to_csv(root/f'candidates-{label}.csv',index=False,encoding='utf-8-sig')
        renamed=result[columns].rename(columns={k:f'{k}_{label}' for k in columns if k!='symbol'})
        merged=merged.merge(renamed,on='symbol',validate='one_to_one')
    merged['newly_eligible_without_limit']=merged.signal_off & ~merged.signal_on
    merged.to_csv(root/'comparison.csv',index=False,encoding='utf-8-sig')
    summary={'as_of':as_of,'universe_size':len(universe),'max_stop_fraction':rules.max_stop_fraction,
        'candidates_on':int(merged.signal_on.sum()),'candidates_off':int(merged.signal_off.sum()),
        'newly_eligible_without_limit':int(merged.newly_eligible_without_limit.sum()),
        'allocated_on':int((merged.quantity_on>0).sum()),'allocated_off':int((merged.quantity_off>0).sum()),
        'rules_on':asdict(on),'rules_off':asdict(off),'limits':asdict(limits),
        'note':'같은 계좌를 독립 적용한 비교. 수량을 합산하지 않으며 스캔 차이는 수익성 검증이 아니다.'}
    (root/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return summary
