"""Local operations UI: explicit jobs over the existing engines, never broker orders."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
import json
import hashlib
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid
from zoneinfo import ZoneInfo

import pandas as pd

from .risk import Account,Limits,allocate
from .strategy import Rules
from .workflow import read_inputs,paper_checkpoint,daily_run,save_json

KST=ZoneInfo('Asia/Seoul')
BUILTINS={
    'baseline':('돌파 기준선','examples/config.json','outputs/paper/state.json','data/market/universe.csv','data/market/history'),
    'setup-rs':('셋업 · RS','examples/config-setup.json','outputs/paper-setup-rs/state.json','data/industries/universe.csv','data/market/history'),
    'setup-mansfield':('셋업 · Mansfield','examples/config-setup-mansfield-index.json','outputs/paper-setup-mansfield/state.json','data/industries/universe.csv','data/market/history-mansfield'),
}
SIGNAL_FIELDS='symbol name sector benchmark date reference_price rs_percentile excess_return mansfield_rs allocation_score weekly_trend eligible_history volume_ratio avg_turnover atr stop_price stop_fraction signal signal_rs signal_mansfield setup_phase setup_ready setup_trigger setup_entry setup_invalidated setup_resistance setup_retracement setup_base_bars setup_volume_ratio setup_range_ratio sector_action sector_peer_count sector_up_fraction sector_excess_return sector_action_scope quantity planned_risk cash_required allocation_reason market_unit_regime market_unit_cap rs_exit_weak rs_exit_value'.split()


def json_rows(frame):
    return json.loads(frame.to_json(orient='records',date_format='iso',force_ascii=False))


def read_csv(path):
    if not path.exists():return pd.DataFrame()
    try:return pd.read_csv(path,dtype={'symbol':str,'benchmark':str},keep_default_na=True)
    except pd.errors.EmptyDataError:return pd.DataFrame()


class Operations:
    def __init__(self,root):
        self.root=Path(root).resolve();self.home=self.root/'outputs/ui'
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='trendfollow-ui')
        self.lock=threading.RLock();self.jobs={};self.processes={}
        self.registry=self.home/'workspaces.json'
        for p in sorted((self.home/'jobs').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)[-100:]:
            try:
                job=json.loads(p.read_text(encoding='utf-8'))
                if job['status'] in {'queued','running'}:
                    job.update(status='interrupted',error='이전 UI 서버가 종료되었습니다. 계좌 처리일을 확인하고 수동 재실행하세요.')
                    save_json(p,job)
                self.jobs[job['id']]=job
            except (ValueError,KeyError):continue

    def close(self):
        # Explicitly started quote workers have their own bounded lifetime and OFF control.
        self.pool.shutdown(wait=False,cancel_futures=True)

    def path(self,value):
        p=(self.root/value).resolve()
        if not p.is_relative_to(self.root) or not p.relative_to(self.root).parts or p.relative_to(self.root).parts[0] not in {'data','outputs','examples'}:
            raise ValueError('허용된 프로젝트 데이터 경로가 아닙니다.')
        return p

    def registry_rows(self):
        if not self.registry.exists():return []
        return json.loads(self.registry.read_text(encoding='utf-8'))

    def workspaces(self):
        rows=[dict(id=k,name=v[0],config=v[1],state=v[2],universe=v[3],data_dir=v[4],capital=10000000,builtin=True)
            for k,v in BUILTINS.items()]
        return rows+self.registry_rows()

    def workspace(self,key):
        for row in self.workspaces():
            if row['id']==key:return row
        raise ValueError('계좌를 찾을 수 없습니다.')

    def configuration(self,w):
        cfg=json.loads(self.path(w['config']).read_text(encoding='utf-8-sig'))
        if set(cfg)-{'rules','limits'}:raise ValueError('설정에는 rules와 limits만 지정하세요.')
        rules=Rules(**cfg.get('rules',{}));limits=Limits(**cfg.get('limits',{}))
        rules.validate();limits.validate()
        return rules,limits

    def presets(self):
        result=[]
        for p in sorted((self.root/'examples').glob('config*.json')):
            cfg=json.loads(p.read_text(encoding='utf-8-sig'))
            if not set(cfg)<= {'rules','limits'}:continue
            rules=Rules(**cfg.get('rules',{}));limits=Limits(**cfg.get('limits',{}))
            rules.validate();limits.validate()
            result.append(dict(id=p.stem,name=p.stem.removeprefix('config-'),rules=asdict(rules),limits=asdict(limits)))
        return result

    def create_workspace(self,payload):
        name=str(payload.get('name','')).strip()
        if not 1<=len(name)<=60:raise ValueError('계좌 이름은 1~60자로 입력하세요.')
        rules=Rules(**payload.get('rules',{}));limits=Limits(**payload.get('limits',{}))
        rules.validate();limits.validate()
        capital=float(payload.get('capital',10000000))
        if not math.isfinite(capital) or capital<=0:raise ValueError('초기 모의자금을 확인하세요.')
        key=uuid.uuid4().hex[:16]
        indices=rules.mansfield_benchmark=='market_index' or (rules.market_units_enabled and rules.market_units_benchmark=='market_index')
        row=dict(id=key,name=name,config=f'outputs/ui/accounts/{key}/config.json',
            state=f'outputs/ui/accounts/{key}/state.json',capital=capital,builtin=False,
            universe='data/industries/universe.csv' if rules.entry_method=='setup' else 'data/market/universe.csv',
            data_dir='data/market/history-mansfield' if indices else 'data/market/history')
        self.path(row['universe'])  # validate path; actual availability is shown separately.
        with self.lock:
            save_json(self.path(row['config']),dict(rules=asdict(rules),limits=asdict(limits)))
            save_json(self.registry,[*self.registry_rows(),row])
        return row

    def state(self,w):
        p=self.path(w['state'])
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None

    def latest_signals(self,w):
        state=self.state(w)
        candidates=[]
        if state:candidates.append(self.path(w['state']).parent/state['last_date']/'signals.csv')
        directory=self.home/'analyses'/w['id']
        if directory.exists():candidates.extend(directory.glob('*/signals.csv'))
        available=[p for p in candidates if p.exists()]
        return max(available,key=lambda p:p.stat().st_mtime_ns) if available else None

    def pulse(self,key):
        w=self.workspace(key);paths=[self.path(w['state']),self.path(w['config'])]
        signal=self.latest_signals(w)
        if signal:paths.append(signal)
        revision='|'.join(f'{p}:{p.stat().st_mtime_ns}' for p in paths if p.exists())
        with self.lock:jobs=list(self.jobs.values())[-25:]
        return dict(revision=revision,jobs=jobs)

    def snapshot(self,key):
        w=self.workspace(key);rules,limits=self.configuration(w);state=self.state(w)
        signal=self.latest_signals(w);frame=read_csv(signal) if signal else pd.DataFrame()
        fields=[c for c in SIGNAL_FIELDS if c in frame]
        rows=json_rows(frame[fields])
        universe=read_csv(self.path(w['universe']))
        industry_names={str(r['symbol']):r['industry_name'] for r in json_rows(universe)
            if r.get('industry_name')}
        for row in rows:
            row['industry_name']=industry_names.get(row.get('symbol'),row.get('sector'))
            row['rs_pass']=((row.get('rs_percentile') is not None and row['rs_percentile']>=rules.min_rs
                and row.get('excess_return') is not None and row['excess_return']>0) if rules.rs_method=='percentile' else
                (row.get('mansfield_rs') is not None and row['mansfield_rs']>rules.min_mansfield))
            if (rules.rs_method=='percentile' and (row.get('rs_percentile') is None or row.get('excess_return') is None)
                    or rules.rs_method=='mansfield' and row.get('mansfield_rs') is None):row['rs_pass']=None
            if rules.entry_method!='setup':row['sector_action']=None
        logs={}
        if state:
            day=self.path(w['state']).parent/state['last_date']
            for name in ['orders','trades','events','campaign-risk','pyramiding-review']:
                logs[name]=json_rows(read_csv(day/f'{name}.csv').tail(150))
        positions=[]
        if state:
            for symbol,p in state['positions'].items():
                positions.append(dict(symbol=symbol,**{k:v for k,v in p.items() if k in {
                    'sector','quantity','entry_price','average_entry_price','last_price','stop_price','initial_risk',
                    'target_3r','addition_count','success','entry_date','rs_exit_pending','exit_pending'}}))
        analysis_meta={}
        if signal and (signal.parent/'analysis.json').exists():analysis_meta=json.loads((signal.parent/'analysis.json').read_text(encoding='utf-8'))
        status='not_started' if state is None else 'ready'
        if state:
            from .industries import decision_universe_records
            from .relative_strength import decision_rule_settings
            u=pd.read_csv(self.path(w['universe']),dtype={'symbol':str,'benchmark':str},keep_default_na=False)
            value=dict(universe=decision_universe_records(u),rules=decision_rule_settings(rules),limits=asdict(limits),capital=w['capital'])
            if hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()!=state.get('config_fingerprint'):
                status='configuration_mismatch'
        if analysis_meta.get('config_sha256') and analysis_meta['config_sha256']!=hashlib.sha256(self.path(w['config']).read_bytes()).hexdigest():
            status='configuration_mismatch'
        as_of=(str(frame.date.iloc[0])[:10] if not frame.empty and 'date' in frame else state['last_date'] if state else None)
        return dict(workspace=w,rules=asdict(rules),limits=asdict(limits),status=status,as_of=as_of,
            account_as_of=state['last_date'] if state else None,cash=state['cash'] if state else w['capital'],
            units=state['units'] if state else 1,positions=positions,pending=state.get('pending',[]) if state else [],
            rows=[] if status=='configuration_mismatch' else rows,logs=logs,analysis=analysis_meta,source='completed_close',
            files_ready=all(self.path(w[k]).is_file() for k in ['config','universe']) and (self.path(w['data_dir'])/'prices.csv').is_file())

    def completed_date(self,value):
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('기준일을 YYYY-MM-DD로 입력하세요.')
        day=pd.Timestamp(value);now=datetime.now(KST)
        if day.date()>now.date() or (day.date()==now.date() and now.hour*60+now.minute<970):
            raise ValueError('완료된 장마감 기준일만 처리하세요. 당일은 16:10 이후 허용합니다.')
        return value

    def submit(self,payload):
        w=self.workspace(payload['workspace']);action=payload.get('action')
        if action not in {'analyze','paper','daily','industry','prepare_intraday','intraday_close','volume_import','volume_profile'}:raise ValueError('지원하지 않는 작업입니다.')
        as_of=self.completed_date(payload.get('as_of'))
        if self.state(w) and action in {'analyze','paper','daily','prepare_intraday'} and self.snapshot(w['id'])['status']=='configuration_mismatch':
            raise ValueError('계좌 설정·유니버스가 변경되었습니다. 기존 계좌를 보존하고 새 비교 계좌를 사용하세요.')
        with self.lock:
            if any(j['status'] in {'queued','running'} for j in self.jobs.values()):raise ValueError('다른 작업이 진행 중입니다. 완료 후 실행하세요.')
            key=uuid.uuid4().hex[:16]
            job=dict(id=key,workspace=w['id'],action=action,as_of=as_of,status='queued',created_at=datetime.now(KST).isoformat())
            self.jobs[key]=job
            save_json(self.home/'jobs'/f'{key}.json',job)
            self.pool.submit(self.execute,key,w,dict(payload))
        return dict(job)

    def execute(self,key,w,payload):
        job=self.jobs[key];job['status']='running';save_json(self.home/'jobs'/f'{key}.json',job)
        try:
            action=payload['action'];as_of=job['as_of'];prices=self.path(w['data_dir'])/'prices.csv'
            universe=self.path(w['universe']);config=self.path(w['config']);state_path=self.path(w['state'])
            rules,limits=self.configuration(w)
            if action=='analyze':
                from .backtest import features
                from .research_store import require_published_research
                require_published_research(prices)
                p,u,r,l=read_inputs(prices,universe,as_of,config)
                f,bad=features(p,u,r)
                today=f[f.date==pd.Timestamp(as_of)].copy()
                if today.empty:raise ValueError('기준일 가격이 없습니다. 데이터 갱신 상태를 확인하세요.')
                state=self.state(w)
                if state and state['last_date']!=as_of:raise ValueError('후보 수량 계산은 계좌 기준일과 같은 날짜로 실행하세요. 먼저 일별 모의 처리를 진행하세요.')
                holdings=[dict(symbol=s,sector=x['sector'],quantity=x['quantity'],current_price=x['last_price'],stop_price=x['stop_price'])
                    for s,x in state['positions'].items()] if state else []
                cash=state['cash'] if state else w['capital'];equity=cash+sum(x['quantity']*x['current_price'] for x in holdings)
                from dataclasses import replace
                result=allocate(today,Account(equity,cash,holdings),replace(l,units=state['units'] if state else 1))
                out=self.home/'analyses'/w['id']/key;out.mkdir(parents=True)
                result.to_csv(out/'signals.csv',index=False,encoding='utf-8-sig')
                info=dict(as_of=as_of,mode='planning_only',quarantined_rows=len(bad),generated_at=datetime.now(KST).isoformat(),
                    signals=int(result.signal.sum()),allocated=int((result.quantity>0).sum()),
                    config_sha256=hashlib.sha256(config.read_bytes()).hexdigest())
                save_json(out/'analysis.json',info);result=info
            elif action=='paper':result=paper_checkpoint(prices,universe,as_of,state_path,w['capital'],config)
            elif action=='daily':result=daily_run(universe,self.path(w['data_dir']),state_path,as_of,w['capital'],config,self.root/'.env','kiwoom')
            elif action=='industry':
                from .industries import industry_sync
                result=industry_sync(self.root/'data/market/universe.csv',self.root/'data/market/master',self.root/'data/industries',refresh=True)
            elif action=='prepare_intraday':
                from .intraday import prepare_plan
                signal=self.latest_signals(w)
                if signal is None:raise ValueError('먼저 기준일 후보를 분석하세요.')
                f=read_csv(signal)
                if f.empty or str(f.date.iloc[0])[:10]!=as_of:raise ValueError('분석 기준일과 감시 준비 기준일이 다릅니다.')
                symbols=payload.get('symbols',[])
                if not isinstance(symbols,list) or not symbols or not all(isinstance(s,str) and re.fullmatch('[0-9A-Z]{6}',s) for s in symbols):
                    raise ValueError('감시할 종목을 선택하세요.')
                out=self.home/'intraday'/key;out.mkdir(parents=True)
                plan=out/'plan.json'
                references=[]
                for ref in (self.home/'volume').glob('reference-*.json'):
                    data=json.loads(ref.read_text(encoding='utf-8'))
                    if data['as_of']<=as_of and not data.get('synthetic'):references.append((data['as_of'],ref.stat().st_mtime_ns,ref))
                reference=max(references)[2] if references else None
                result=prepare_plan(signal,prices,universe,payload['session'],symbols,config,plan,
                    paper_state=state_path if state_path.exists() else None,volume_profile=reference)
                result['ui_workspace_id']=w['id'];save_json(plan,result)
                result=dict(plan_id=key,subscriptions=len(result['subscriptions']),session=result['session'],volume_reference='attached' if reference else 'unavailable')
            elif action=='volume_import':
                from .intraday_volume import import_runs
                result=import_runs([self.run_path(payload['run_id'])],self.home/'volume/samples.csv')
            elif action=='volume_profile':
                from .intraday_volume import build_reference
                result=build_reference(self.home/'volume/samples.csv',as_of,self.home/f'volume/reference-{key}.json')
            else:
                from .intraday_close import close_comparison
                run=self.run_path(payload['run_id']);out=self.home/'close-comparisons'/key
                plan=json.loads((run/'plan.json').read_text(encoding='utf-8'))
                if plan['session']!=as_of:raise ValueError('대조 기준일을 관측 거래일과 맞추세요.')
                result=close_comparison(run/'plan.json',prices,self.root/'data/industries/peer-returns.csv',out,run_dir=run)
            job.update(status='completed',result=result)
        except (ValueError,FileNotFoundError) as error:job.update(status='failed',error=str(error))
        except Exception:job.update(status='failed',error='작업 처리에 실패했습니다. 데이터·설정·출력 경로를 확인하세요.')
        finally:
            job['finished_at']=datetime.now(KST).isoformat()
            save_json(self.home/'jobs'/f'{key}.json',job)

    def plans(self):
        result=[]
        root=self.home/'intraday'
        for p in root.glob('*/plan.json') if root.exists() else []:
            plan=json.loads(p.read_text(encoding='utf-8'))
            result.append(dict(id=p.parent.name,workspace=plan.get('ui_workspace_id'),session=plan['session'],targets=list(plan['targets']),subscriptions=len(plan['subscriptions'])))
        return result

    def run_path(self,key):
        if not re.fullmatch('[a-f0-9]{16}',key):raise ValueError('관측 기록을 찾을 수 없습니다.')
        return self.home/'live'/key

    def start_watch(self,payload):
        key=payload.get('plan_id');plans={p['id']:p for p in self.plans()}
        if key not in plans:raise ValueError('생성한 감시 계획을 선택하세요.')
        if payload.get('workspace') and plans[key]['workspace']!=payload['workspace']:raise ValueError('감시 계획과 선택 계좌가 다릅니다.')
        now=datetime.now(KST)
        if plans[key]['session']!=str(now.date()) or now.weekday()>=5 or not '09:00'<=now.strftime('%H:%M')<='15:30':
            raise ValueError('감시 계획의 실제 정규장 시간에만 관측을 시작할 수 있습니다.')
        seconds=int(payload.get('seconds',300))
        if not 1<=seconds<=23400:raise ValueError('관측 시간은 1~23,400초로 지정하세요.')
        from .observation_control import control_status
        if control_status(self.root/'outputs/intraday/control.json')['active_or_unconfirmed']:raise ValueError('이미 관측이 실행 중입니다.')
        run=uuid.uuid4().hex[:16];out=self.run_path(run)
        log=self.home/'live-logs'/f'{run}.log';log.parent.mkdir(parents=True,exist_ok=True)
        command=[sys.executable,'-m','trendfollow','intraday-live','--observation','on',
            '--plan',str(self.home/'intraday'/key/'plan.json'),'--out',str(out),'--seconds',str(seconds)]
        with log.open('wb') as handle:
            process=subprocess.Popen(command,cwd=self.root,stdout=handle,stderr=handle,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        self.processes[run]=process
        return dict(run_id=run,status='starting',orders_sent=0)

    def intraday(self,workspace=None):
        from .observation_control import control_status
        control=control_status(self.root/'outputs/intraday/control.json')
        runs=[]
        root=self.home/'live'
        for p in sorted(root.glob('*/summary.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)[:10] if root.exists() else []:
            try:
                summary=json.loads(p.read_text(encoding='utf-8'));events=[]
                plan=json.loads((p.parent/'plan.json').read_text(encoding='utf-8'))
                if workspace and plan.get('ui_workspace_id')!=workspace:continue
                file=p.parent/'events.jsonl'
                if file.exists():
                    with file.open('rb') as handle:
                        size=handle.seek(0,2);handle.seek(max(0,size-65536));lines=handle.read().decode('utf-8',errors='replace').splitlines()
                    for line in lines[-50:]:
                        try:events.append(json.loads(line))
                        except ValueError:continue
                for event in events:
                    try:age=(datetime.now(KST)-datetime.fromisoformat(event['timestamp'])).total_seconds()
                    except (ValueError,KeyError,TypeError):age=None
                    event['ui_current']=summary.get('connection_state')=='active' and age is not None and -2<=age<=60
                runs.append(dict(id=p.parent.name,summary=summary,events=events))
            except (OSError,ValueError):continue
        return dict(control=control,plans=[p for p in self.plans() if workspace is None or p['workspace']==workspace],runs=runs)

    def stop_watch(self):
        from .observation_control import set_observation
        return set_observation(False,self.root/'outputs/intraday/control.json',wait_seconds=1)

    def prices(self,key,symbol):
        w=self.workspace(key)
        if not re.fullmatch('[0-9A-Z]{6}',symbol):raise ValueError('종목코드를 확인하세요.')
        result=[]
        for chunk in pd.read_csv(self.path(w['data_dir'])/'prices.csv',dtype={'symbol':str},chunksize=100000):
            rows=chunk[chunk.symbol.eq(symbol)]
            if not rows.empty:result.extend(json_rows(rows))
        state=self.state(w);date=state['last_date'] if state else '9999-12-31'
        signal=self.latest_signals(w)
        if signal:
            header=pd.read_csv(signal,usecols=['date'],nrows=1)
            if not header.empty:date=str(header.date.iloc[0])[:10]
        return [r for r in result if r['date']<=date and all(r.get(k,0)>0 for k in ['open','high','low','close'])][-180:]
