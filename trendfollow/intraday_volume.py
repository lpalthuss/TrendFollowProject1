"""Completed-minute cumulative volume versus frozen prior-session samples."""
from datetime import datetime, time
import hashlib
import json
import math
from pathlib import Path

import pandas as pd


def validate_reference(profile, as_of, synthetic):
    if profile.get('version')!=1 or profile.get('exchange')!='KRX' or profile.get('provider')!='kis':
        raise ValueError('지원하지 않는 장중 거래량 기준입니다.')
    if bool(profile.get('synthetic'))!=bool(synthetic):
        raise ValueError('합성 거래량 기준과 실제 감시 계획을 섞을 수 없습니다.')
    if profile['as_of']>as_of:
        raise ValueError('거래량 기준에 감시 계획 기준일 이후 자료가 있습니다.')
    for key in ['lookback','min_sessions','max_sample_age_seconds']:
        value=profile[key]
        if isinstance(value,bool) or not isinstance(value,int) or value<1:
            raise ValueError('거래량 비교 기간·신선도는 양의 정수여야 합니다.')
    if profile['min_sessions']>profile['lookback'] or not math.isfinite(profile['multiple']) or profile['multiple']<=0:
        raise ValueError('거래량 비교 배수·최소 이력이 유효하지 않습니다.')
    if profile['max_sample_age_seconds']>60:raise ValueError('거래량 표본 신선도는 최대 60초입니다.')
    for bins in profile['symbols'].values():
        for minute,row in bins.items():
            minute_time=datetime.strptime(minute,'%H:%M:%S').time()
            if minute_time.second or not time(9,1)<=minute_time<=time(15,30):raise ValueError('거래량 기준은 정규장의 완료된 분 시각이어야 합니다.')
            sessions=row['sessions']
            if len(set(sessions))!=len(sessions) or len(sessions)!=row['session_count'] or len(sessions)>profile['lookback']:
                raise ValueError('거래량 비교 거래일이 중복되거나 개수가 다릅니다.')
            if any(s>profile['as_of'] for s in sessions) or not math.isfinite(row['mean_cumulative_volume']) or row['mean_cumulative_volume']<0:
                raise ValueError('거래량 기준에 미래 자료 또는 잘못된 거래량이 있습니다.')


def update_sample(monitor, symbol, cutoff, quote):
    from .intraday import stamp
    at=stamp(quote['timestamp'])
    lag=(cutoff-at).total_seconds()
    limit=monitor.plan.get('volume_reference',{}).get('max_sample_age_seconds',60)
    if not 0<=lag<=limit:return
    sample={'minute':cutoff.strftime('%H:%M:%S'),'sample_timestamp':at.isoformat(),
            'cumulative_volume':quote['cumulative_volume']}
    old=monitor.volume_samples.get(symbol)
    if (old is None or old['minute']<sample['minute'] or old['minute']==sample['minute'] and
        (old['sample_timestamp'],old['cumulative_volume'])<=(sample['sample_timestamp'],sample['cumulative_volume'])):
        monitor.volume_samples[symbol]=sample


def capture_tick_boundary(monitor, symbol, quote, old, now):
    from .intraday import stamp
    if symbol not in monitor.plan['targets']:return
    cutoff=min(stamp(quote['timestamp']),now).replace(second=0,microsecond=0)
    if old is not None:update_sample(monitor,symbol,cutoff,old)
    update_sample(monitor,symbol,cutoff,quote)


def current_sample(monitor, symbol, now):
    cutoff=now.replace(second=0,microsecond=0)
    if not time(9,1)<=cutoff.time().replace(tzinfo=None)<=time(15,30):return None
    quote=monitor.quotes.get(symbol)
    if quote is not None:update_sample(monitor,symbol,cutoff,quote)
    sample=monitor.volume_samples.get(symbol)
    return sample if sample and sample['minute']==cutoff.strftime('%H:%M:%S') else None


def volume_check(monitor, symbol, now, quote):
    profile=monitor.plan.get('volume_reference')
    result={'volume_status':'unavailable_same_time_history','volume_pass':None,
            'volume_ratio_same_time':None,'volume_reference_sessions':0,
            'volume_minute':None,'volume_current_cumulative':None,'volume_reference_mean':None,
            'volume_sample_at':None,'volume_multiple':profile['multiple'] if profile else None,
            'volume_min_sessions':profile['min_sessions'] if profile else None}
    if profile is None:return result
    if quote is None or symbol in monitor.basis_mismatch:
        result['volume_status']='stale_or_invalid_quote';return result
    sample=current_sample(monitor,symbol,now)
    if sample is None:
        result['volume_status']='current_sample_unavailable';return result
    result.update(volume_minute=sample['minute'],volume_current_cumulative=sample['cumulative_volume'],volume_sample_at=sample['sample_timestamp'])
    reference=profile['symbols'].get(symbol,{}).get(sample['minute'])
    if reference is None:
        result['volume_status']='insufficient_history';return result
    result.update(volume_reference_sessions=reference['session_count'],volume_reference_mean=reference['mean_cumulative_volume'])
    if reference['session_count']<profile['min_sessions']:
        result['volume_status']='insufficient_history';return result
    if reference['mean_cumulative_volume']<=0:
        result['volume_status']='zero_reference_volume';return result
    ratio=sample['cumulative_volume']/reference['mean_cumulative_volume']
    passed=ratio>=profile['multiple']
    result.update(volume_status='verified' if passed else 'weak',volume_pass=passed,volume_ratio_same_time=ratio)
    return result


def read_samples(path):
    f=pd.read_csv(path,dtype={'symbol':str,'session':str,'minute':str,'sample_timestamp':str})
    required={'symbol','session','minute','sample_timestamp','cumulative_volume','synthetic','provider','exchange'}
    if not required.issubset(f):raise ValueError('거래량 이력 필드가 부족합니다.')
    if f.duplicated(['symbol','session','minute']).any():raise ValueError('거래량 이력이 중복되었습니다.')
    for row in f.to_dict('records'):
        at=datetime.fromisoformat(row['sample_timestamp'])
        cutoff=datetime.fromisoformat(row['session']+'T'+row['minute']+'+09:00')
        if cutoff.second or not time(9,1)<=cutoff.time().replace(tzinfo=None)<=time(15,30) or row['provider']!='kis' or row['exchange']!='KRX':
            raise ValueError('거래량 이력의 시각·거래소·제공자가 유효하지 않습니다.')
        if at.tzinfo is None or not 0<=(cutoff-at).total_seconds()<=60:
            raise ValueError('거래량 이력 시각이 비교 분보다 늦거나 오래됐습니다.')
        if not math.isfinite(row['cumulative_volume']) or row['cumulative_volume']<0:
            raise ValueError('거래량 이력 수치가 유효하지 않습니다.')
        if not isinstance(row['synthetic'],bool):raise ValueError('합성 거래량 여부가 유효하지 않습니다.')
    if f.synthetic.nunique()>1:raise ValueError('실측·합성 거래량 이력을 섞을 수 없습니다.')
    for _,group in f.groupby(['symbol','session']):
        if (group.sort_values('minute').cumulative_volume.diff().dropna()<0).any():
            raise ValueError('거래일 내 누적 거래량이 감소합니다.')
    return f


def build_reference(history, as_of, output, lookback=20,min_sessions=5,multiple=1.5,max_sample_age_seconds=60):
    datetime.strptime(as_of,'%Y-%m-%d')
    f=read_samples(history)
    f=f[(f.session<=as_of)].copy()
    if f.empty:raise ValueError('기준일까지 수집된 거래량 이력이 없습니다.')
    synthetic=bool(f.synthetic.iloc[0])
    profile={'version':1,'exchange':'KRX','provider':'kis','synthetic':synthetic,'as_of':as_of,
             'lookback':lookback,'min_sessions':min_sessions,'multiple':multiple,
             'max_sample_age_seconds':max_sample_age_seconds,'symbols':{},
             'history_sha256':hashlib.sha256(Path(history).read_bytes()).hexdigest()}
    # Validate parameters before applying a lookback or constructing reference bins.
    validate_reference(profile,as_of,synthetic)
    for symbol,group in f.groupby('symbol'):
        dates=sorted(group.session.unique())[-lookback:]
        group=group[group.session.isin(dates)]
        bins={}
        for minute,rows in group.groupby('minute'):
            cutoff=pd.to_datetime(rows.session+'T'+rows.minute+'+09:00')
            at=pd.to_datetime(rows.sample_timestamp)
            rows=rows[((cutoff-at).dt.total_seconds()<=max_sample_age_seconds).to_numpy()]
            if rows.empty:continue
            bins[minute]={'session_count':len(rows),'sessions':sorted(rows.session.tolist()),
                          'mean_cumulative_volume':float(rows.cumulative_volume.mean())}
        profile['symbols'][str(symbol)]=bins
    validate_reference(profile,as_of,synthetic)
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as sink:json.dump(profile,sink,ensure_ascii=False,indent=2,allow_nan=False)
    return {'symbols':len(profile['symbols']),'as_of':as_of,'synthetic':synthetic,
            'ready_minutes':sum(r['session_count']>=min_sessions and r['mean_cumulative_volume']>0
                for bins in profile['symbols'].values() for r in bins.values())}


def import_runs(run_dirs, output):
    from .intraday import IntradayMonitor,KST,stamp
    from .intraday_sampling import restore_snapshot
    from .observation_control import control_lock
    samples={};sources={}
    for folder in run_dirs:
        root=Path(folder)
        plan=json.loads((root/'plan.json').read_text(encoding='utf-8'))
        metadata=json.loads((root/'summary.json').read_text(encoding='utf-8'))
        synthetic=bool(plan.get('synthetic'))
        if metadata.get('mode')=='connection_probe':raise ValueError('접속 확인 기록은 거래량 이력으로 사용할 수 없습니다.')
        if not synthetic:
            if metadata.get('mode')!='live_observation_only' or metadata.get('provider')!='kis' or metadata.get('connection_state') not in {'closed','failed'}:
                raise ValueError('거래량 이력은 종료된 KIS 실측 실행만 가져올 수 있습니다.')
            now=datetime.now(KST);day=datetime.strptime(plan['session'],'%Y-%m-%d').date()
            if day>now.date() or day==now.date() and now.hour*60+now.minute<970:
                raise ValueError('장중 거래량 이력 가져오기는 완료된 거래일만 허용합니다.')
        journal=root/'ticks.jsonl'
        if not journal.exists() and synthetic:journal=Path(metadata.get('ticks_source',''))
        monitor=IntradayMonitor(plan)
        previous=None
        with journal.open(encoding='utf-8') as source:
            for line in source:
                record=json.loads(line);at=stamp(record['received_at'])
                if str(at.date())!=plan['session'] or previous is not None and at<previous:
                    raise ValueError('거래량 원천의 수신 거래일·순서가 유효하지 않습니다.')
                previous=at
                if record.get('kind')=='connection_reset':monitor.reset_connection()
                elif record.get('kind')=='quote_snapshot':restore_snapshot(monitor,record)
                elif 'tick' in record:monitor.update(record['tick'],record['received_at'],evaluate=False)
                for symbol in plan['targets']:
                    if symbol in monitor.basis_mismatch:continue
                    value=current_sample(monitor,symbol,at)
                    if value:
                        candidate=dict(symbol=symbol,session=plan['session'],**value,synthetic=synthetic,provider='kis',exchange='KRX')
                        key=(symbol,plan['session'],value['minute']);old=samples.get(key)
                        if old and ((old['sample_timestamp']<candidate['sample_timestamp'] and old['cumulative_volume']>candidate['cumulative_volume']) or
                                    (old['sample_timestamp']>candidate['sample_timestamp'] and old['cumulative_volume']<candidate['cumulative_volume'])):
                            raise ValueError('같은 거래일의 누적 거래량 원천이 상충합니다.')
                        if old is None or (old['sample_timestamp'],old['cumulative_volume'])<(candidate['sample_timestamp'],candidate['cumulative_volume']):samples[key]=candidate
        sources[str(journal.resolve())]=hashlib.sha256(journal.read_bytes()).hexdigest()
    if not samples:raise ValueError('완료된 분의 유효 거래량 표본이 없습니다.')
    path=Path(output)
    with control_lock(path):
        current=read_samples(path) if path.exists() else pd.DataFrame()
        previous_meta=path.with_suffix('.metadata.json')
        old_sources=json.loads(previous_meta.read_text(encoding='utf-8')).get('sources',{}) if previous_meta.exists() else {}
        f=pd.concat([current,pd.DataFrame(samples.values())],ignore_index=True)
        if f.synthetic.nunique()>1:raise ValueError('실측·합성 거래량 이력을 섞을 수 없습니다.')
        f=f.sort_values(['symbol','session','minute','sample_timestamp','cumulative_volume'])
        for _,g in f.groupby(['symbol','session','minute']):
            if (g.cumulative_volume.diff().dropna()<0).any():raise ValueError('같은 거래일의 누적 거래량 원천이 상충합니다.')
        f=f.drop_duplicates(['symbol','session','minute'],keep='last')
        # Unique temporary file avoids partial reads; lock prevents lost appends.
        import uuid
        temp=path.with_name(uuid.uuid4().hex[:12]+'.tmp')
        try:
            f.to_csv(temp,index=False);read_samples(temp);temp.replace(path)
        finally:
            if temp.exists():temp.unlink()
        meta={'samples':len(f),'sessions':int(f.session.nunique()),'synthetic':bool(f.synthetic.iloc[0]),'sources':{**old_sources,**sources}}
        from .observation_control import atomic_json
        atomic_json(previous_meta,meta)
    return meta
