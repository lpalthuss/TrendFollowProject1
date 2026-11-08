"""Industry memberships and independent peer scope for industry-action validation.

NAVER public classification is labeled as NAVER, not certified as WICS. Current
snapshots must never be presented as historical point-in-time membership.
"""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from .market import NaverResearchSource, build_universe


class IndustrySource:
    LIST_URL='https://stock.naver.com/api/stockSecurity/rankings/v2/domestic/industries'
    MEMBER_URL='https://stock.naver.com/api/domestic/market/upjong/{code}/stocklist'

    def __init__(self, cache_dir, refresh=False, session=None, interval=.55):
        self.cache=Path(cache_dir);self.cache.mkdir(parents=True,exist_ok=True)
        self.refresh=refresh;self.session=session or requests.Session();self.interval=interval;self.last=0.

    def pages(self,url,params,prefix):
        cursor=None;seen=set();items=[]
        for page in range(100):
            path=self.cache/f'{prefix}-{page}.json'
            query=dict(params)
            if cursor:query['cursor']=cursor
            if path.exists() and not self.refresh:
                body=json.loads(path.read_text(encoding='utf-8'))
            else:
                time.sleep(max(0,self.interval-(time.monotonic()-self.last)))
                self.last=time.monotonic()
                response=self.session.get(url,params=query,timeout=30)
                response.raise_for_status();body=response.json()
                if not isinstance(body,dict) or not isinstance(body.get('items'),list):
                    raise ValueError('산업 목록 응답 형식이 변경되었습니다.')
                path.write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
            if not isinstance(body,dict) or not isinstance(body.get('items'),list):
                raise ValueError('산업 캐시 형식이 올바르지 않습니다.')
            items.extend(body['items'])
            if not body.get('hasNext',False):return items
            cursor=body.get('cursor')
            if not cursor or cursor in seen:raise ValueError('산업 목록 연속조회가 진행되지 않습니다.')
            seen.add(cursor)
        raise ValueError('산업 목록 페이지 한도에 도달했습니다.')

    def collect(self):
        groups=self.pages(self.LIST_URL,{'sortType':'changeRate','size':100,'period':'daily'},'industries')
        if not groups:raise ValueError('수집된 산업 분류가 없습니다.')
        rows=[]
        for i,group in enumerate(groups,1):
            code=str(group['code'])
            if not code.isdigit():raise ValueError('산업코드 형식이 올바르지 않습니다.')
            members=self.pages(self.MEMBER_URL.format(code=code),
                {'sortType':'marketCap','sortDirection':'desc','size':100},f'members-{code}')
            for member in members:
                if member.get('marketType') not in {'KOSPI','KOSDAQ'} or member.get('stockEndType')!='stock':continue
                symbol=str(member['itemCode'])
                if re.fullmatch('A[0-9A-Z]{6}',symbol):symbol=symbol[1:]
                if not re.fullmatch('[0-9A-Z]{6}',symbol):continue
                quote=member.get('krx') or {}
                try:ret=float(quote['changeRate'])/100;volume=float(quote['tradingVolume'])
                except (KeyError,ValueError,TypeError):ret=np.nan;volume=0
                rows.append({'symbol':symbol,'name':member['itemName'],'market':member['marketType'],
                    'industry_code':code,'industry_name':group['name'],'source':'NAVER_INDUSTRY',
                    'source_url':f'https://stock.naver.com/market/stock/kr/industry/1?no={code}',
                    'day_return':ret,'quote_eligible':int(np.isfinite(ret) and volume>0 and quote.get('tradableStatus')=='tradable'),
                    'quote_volume':volume})
            if i%10==0 or i==len(groups):print(f'산업 구성종목 {i}/{len(groups)}',flush=True)
        result=pd.DataFrame(rows)
        if result.empty:raise ValueError('산업별 주식 구성종목을 수집하지 못했습니다.')
        result=result.drop_duplicates(['symbol','industry_code'])
        conflicts=result[result.symbol.duplicated(keep=False)].copy()
        result=result[~result.symbol.isin(conflicts.symbol)]
        return result,conflicts,len(groups)


def apply_overrides(members,overrides,classification_date):
    result=members.copy()
    result['standard_industry_code']=result.industry_code
    result['standard_industry_name']=result.industry_name
    result['sector']='NAVER:'+result.industry_code.astype(str)
    result['override_reason']=''
    if overrides is None or overrides.empty:return result
    required={'symbol','industry_code','industry_name','valid_from','reason','source_url'}
    if not required.issubset(overrides):raise ValueError('산업 수정 CSV 필드가 부족합니다.')
    if overrides.symbol.duplicated().any():raise ValueError('종목별 산업 수정이 중복되었습니다.')
    if not set(overrides.symbol).issubset(set(result.symbol)):raise ValueError('산업 목록에 없는 종목의 수정입니다.')
    for item in overrides.fillna('').to_dict('records'):
        if not re.fullmatch('[A-Za-z0-9_-]+',str(item['industry_code'])):
            raise ValueError('직접 지정하는 산업코드는 영문·숫자·밑줄·하이픈만 사용하세요.')
        if not item['industry_name'].strip() or not item['reason'].strip() or not item['source_url'].strip():
            raise ValueError('산업 수정의 이름·근거·출처가 필요합니다.')
        effective=pd.Timestamp(item['valid_from'])
        if pd.isna(effective):raise ValueError('산업 수정 적용일이 필요합니다.')
        if effective.date()>pd.Timestamp(classification_date).date():continue
        mask=result.symbol==item['symbol']
        for col in ['industry_code','industry_name','source_url']:
            result.loc[mask,col]=item[col]
        result.loc[mask,'sector']='CUSTOM:'+str(item['industry_code'])
        result.loc[mask,'source']='MANUAL_OVERRIDE'
        result.loc[mask,'override_reason']=item['reason']
    return result


def industry_sync(universe_path,master_dir,out_dir,overrides_path=None,refresh=False):
    root=Path(out_dir);root.mkdir(parents=True,exist_ok=True)
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    if now.weekday()<5 and now.hour*60+now.minute<970:
        raise ValueError('장마감 인더스트리 액션 스냅샷은 한국시간 16:10 이후 수집하세요.')
    observed_date=str(now.date());snapshot=root/'snapshots'/observed_date
    source=IndustrySource(snapshot/'raw',refresh=refresh)
    members,conflicts,group_count=source.collect()
    snapshot.mkdir(parents=True,exist_ok=True)
    conflicts.to_csv(snapshot/'conflicts.csv',index=False,encoding='utf-8-sig')
    overrides=pd.read_csv(overrides_path,dtype=str,keep_default_na=False) if overrides_path else None
    members=apply_overrides(members,overrides,observed_date)
    members['classification_observed_at']=now.isoformat()
    members.to_csv(snapshot/'memberships.csv',index=False,encoding='utf-8-sig')
    frames=[pd.read_csv(Path(master_dir)/f'{m}.csv',dtype=str,keep_default_na=False) for m in ['kospi','kosdaq']]
    broad,_=build_universe(frames,min_recent_turnover=0)
    # The peer universe excludes unsuitable share classes/statuses, but has no trading-value floor.
    peers=members.merge(broad[['symbol','market','benchmark']],on=['symbol','market'],how='inner',validate='one_to_one')
    charts=NaverResearchSource(snapshot/'benchmark-raw')
    start=str(now.date()-timedelta(days=20))
    index=charts.daily('KOSPI',start,observed_date)
    if index.empty:raise ValueError('산업 수익률의 최근 거래일을 확인하지 못했습니다.')
    returns_date=str(index.date.max())
    benchmark_returns={}
    for code in sorted(set(peers.benchmark)):
        history=charts.daily(code,start,observed_date)
        if len(history)<2 or history.date.iloc[-1]!=returns_date:
            raise ValueError('산업 동료 벤치마크의 거래일이 일치하지 않습니다.')
        benchmark_returns[code]=float(history.close.iloc[-1]/history.close.iloc[-2]-1)
    peers['date']=returns_date
    peers['benchmark_day_return']=peers.benchmark.map(benchmark_returns)
    peers['snapshot_observed_at']=now.isoformat()
    peers.to_csv(snapshot/'peer-returns.csv',index=False,encoding='utf-8-sig')
    history_path=root/'peer-returns.csv'
    if history_path.exists():
        past=pd.read_csv(history_path,dtype={'symbol':str,'industry_code':str})
        past=past[past.date!=returns_date]
        history=pd.concat([past,peers],ignore_index=True)
    else:history=peers
    history.sort_values(['date','symbol']).to_csv(history_path,index=False,encoding='utf-8-sig')
    universe=pd.read_csv(universe_path,dtype={'symbol':str,'benchmark':str},keep_default_na=False)
    universe=universe.rename(columns={'sector':'sector_original'})
    labels=members[['symbol','sector','industry_code','industry_name','source','source_url',
                    'classification_observed_at','standard_industry_code','standard_industry_name','override_reason']]
    enriched=universe.merge(labels,on='symbol',how='left',validate='one_to_one')
    missing=enriched.sector.isna()
    enriched.loc[missing,'sector']='UNCLASSIFIED:'+enriched.loc[missing,'symbol']
    enriched['industry_classified']=~missing
    enriched['industry_peer_history']=str(history_path.resolve())
    enriched.fillna('').to_csv(root/'universe.csv',index=False,encoding='utf-8-sig')
    enriched[missing].to_csv(root/'unclassified.csv',index=False,encoding='utf-8-sig')
    summary={'classification_source':'NAVER public industry classification; not certified WICS',
        'classification_observed_at':now.isoformat(),'returns_date':returns_date,
        'point_in_time_membership':False,'industry_count':group_count,'memberships':len(members),
        'peer_universe_size':len(peers),'peer_eligible_count':int(peers.quote_eligible.sum()),
        'candidate_universe_size':len(enriched),'classified_candidates':int((~missing).sum()),
        'unclassified_candidates':int(missing.sum()),'conflicting_memberships':len(conflicts),
        'cross_check':'listing market and share/status filters from KIS public master',
        'not_completed':['WiseIndex/WICS direct retrieval unavailable','KRX sector constituents not retrieved',
                         'business reports not individually reviewed','historical membership not reconstructed'],
        'universe_sha256':hashlib.sha256((root/'universe.csv').read_bytes()).hexdigest()}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def load_peer_history(universe):
    paths=set(universe.industry_peer_history.dropna().astype(str))-{''}
    if len(paths)!=1:raise ValueError('산업 동료 수익률 파일 경로가 하나여야 합니다.')
    history=pd.read_csv(next(iter(paths)),dtype={'symbol':str,'sector':str},keep_default_na=False)
    required={'date','symbol','sector','day_return','benchmark_day_return','quote_eligible'}
    if not required.issubset(history):raise ValueError('산업 동료 수익률 파일 필드가 부족합니다.')
    history['date']=pd.to_datetime(history.date,format='%Y-%m-%d',errors='raise')
    if history.duplicated(['date','symbol']).any():raise ValueError('산업 동료 수익률이 중복되었습니다.')
    for col in ['day_return','benchmark_day_return','quote_eligible']:
        history[col]=pd.to_numeric(history[col],errors='coerce')
    return history


def decision_universe_records(universe):
    """Classification IDs/peer file remain material; observation timestamps are audit metadata."""
    audit_columns=['sector_original','industry_code','industry_name','source','source_url',
        'classification_observed_at','standard_industry_code','standard_industry_name',
        'override_reason','industry_classified']
    return universe.drop(columns=audit_columns,errors='ignore').to_dict('records')


PEER_DECISION_COLUMNS=['date','symbol','sector','day_return','benchmark_day_return','quote_eligible']


def require_peer_sessions(prices,universe,start,end):
    history=load_peer_history(universe)
    dates=pd.to_datetime(prices.loc[prices.symbol.isin(set(universe.benchmark)),'date'])
    required=set(dates[(dates>=pd.Timestamp(start))&(dates<=pd.Timestamp(end))])
    missing=required-set(history.date)
    if missing:
        raise ValueError(f'인더스트리 액션용 동료 수익률이 {len(missing)}거래일 부족합니다. '
                         '현재 산업 스냅샷을 과거 자료로 대체하지 않습니다.')


def industry_action_features(prices,universe,rules):
    history=load_peer_history(universe)
    valid=(history.quote_eligible==1)&np.isfinite(history.day_return)&np.isfinite(history.benchmark_day_return)
    peers=history[valid].copy()
    peers['up']=(peers.day_return>0).astype(int)
    peers['excess']=peers.day_return-peers.benchmark_day_return
    totals=peers.groupby(['date','sector']).agg(total=('symbol','size'),up=('up','sum'),excess=('excess','sum')).reset_index()
    own=peers[['date','symbol','sector','up','excess']].rename(columns={'up':'own_up','excess':'own_excess'})
    own['own_count']=1
    target=prices[['date','symbol']].copy();target['date']=pd.to_datetime(target.date)
    target=target.merge(universe[['symbol','sector']],on='symbol',validate='many_to_one')
    target=target.merge(totals,on=['date','sector'],how='left',validate='many_to_one')
    target=target.merge(own,on=['date','symbol','sector'],how='left',validate='one_to_one')
    count=target.total.fillna(0)-target.own_count.fillna(0)
    target['sector_peer_count']=count
    target['sector_up_fraction']=(target.up-target.own_up.fillna(0))/count.replace(0,np.nan)
    target['sector_excess_return']=(target.excess-target.own_excess.fillna(0))/count.replace(0,np.nan)
    target['sector_action']=(count>=rules.sector_min_peers)&(target.sector_up_fraction>=rules.sector_min_breadth)&(target.sector_excess_return>0)
    target['sector_action_scope']=np.select([target.sector.str.startswith('UNCLASSIFIED:'),
        target.date.isin(history.date)],['unclassified_industry','industry_snapshot'],default='missing_industry_snapshot')
    return target[['date','symbol','sector_peer_count','sector_up_fraction','sector_excess_return','sector_action','sector_action_scope']]
