"""Public master snapshots and reproducible, cached research data acquisition."""
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import threading
from tempfile import TemporaryDirectory
import time
import zipfile
import uuid
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import requests


def download_master(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for market in ['kospi', 'kosdaq']:
        url = f'https://raw.githubusercontent.com/koreainvestment/open-trading-api/main/stocks_info/kis_{market}_code_mst.py'
        reference = requests.get(url, timeout=30)
        reference.raise_for_status()
        # Parse constant field definitions; never execute downloaded Python code.
        definitions = {}
        for node in ast.walk(ast.parse(reference.text)):
            if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in {'field_specs', 'part2_columns'}):
                definitions[node.targets[0].id] = ast.literal_eval(node.value)
        widths, names = definitions['field_specs'], definitions['part2_columns']
        if len(widths) != len(names):
            raise ValueError('공식 마스터 필드 수가 일치하지 않습니다.')
        response = requests.get(f'https://new.real.download.dws.co.kr/common/master/{market}_code.mst.zip', timeout=30)
        response.raise_for_status()
        (directory/f'{market}.zip').write_bytes(response.content)
        (directory/f'{market}_parser_reference.py.txt').write_text(reference.text, encoding='utf-8')
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        text = archive.read(f'{market}_code.mst').decode('cp949')
        rows = []
        for line in text.splitlines():
            header, tail = line[:-sum(widths)], line[-sum(widths):]
            row = {'symbol': header[:9].strip(), 'isin': header[9:21].strip(),
                   'name': header[21:].strip(), 'market': market.upper()}
            offset = 0
            for width, name in zip(widths, names):
                row[name] = tail[offset:offset+width].strip()
                offset += width
            rows.append(row)
        frame = pd.DataFrame(rows)
        frame.to_csv(directory/f'{market}.csv', index=False, encoding='utf-8-sig')
        frames.append(frame)
    return frames


def build_universe(frames, min_recent_turnover=1e9):
    """Current snapshot only. Pre-volume liquidity proxy is an explicit research selection bias."""
    records, excluded = [], []
    for market, frame in zip(['KOSPI', 'KOSDAQ'], frames):
        pi = market == 'KOSPI'
        fields = (['그룹코드','거래정지','정리매매','관리종목','저유동성','우선주','SPAC',
                   '지수업종대분류','기준가','전일거래량'] if pi else
                  ['증권그룹구분코드','거래정지 여부','정리매매 여부','관리 종목 여부','저유동성종목 여부',
                   '우선주 구분 코드','기업인수목적회사여부','지수업종 대분류 코드','주식 기준가','전일 거래량'])
        for row in frame.fillna('').to_dict('records'):
            vals = [str(row.get(f, '')).strip() for f in fields]
            group, halted, liquidation, admin, low_liq, preferred, spac, industry, price, volume = vals
            # Prefer the finest nonzero published index-industry code, otherwise use broad group.
            industry_fields = (['지수업종소분류','지수업종중분류'] if pi else
                               ['지수업종 소분류 코드','지수 업종 중분류 코드'])
            for field in industry_fields:
                value = str(row.get(field,'')).strip()
                if value and value != '0000':
                    industry = value
                    break
            reasons = []
            if group not in {'ST', 'FS'} or not re.fullmatch('[0-9A-Z]{6}', str(row['symbol'])):
                reasons.append('not_ordinary_stock')
            for code, value in [('halted', halted), ('liquidation', liquidation), ('administrative', admin),
                                ('low_liquidity_flag', low_liq), ('spac', spac)]:
                if value != 'N':
                    reasons.append(code)
            if preferred != '0':
                reasons.append('preferred_or_other_class')
            if not industry or industry == '0000':
                reasons.append('missing_industry')
            try:
                recent_turnover = float(price)*float(volume)
            except ValueError:
                recent_turnover = 0
            if recent_turnover < min_recent_turnover:
                reasons.append('recent_turnover_proxy_below_threshold')
            base = {'symbol': row['symbol'], 'name': row['name'], 'market': market,
                    'sector': f'{market}:{industry}', 'benchmark': '069500' if pi else '229200',
                    'recent_turnover_proxy': recent_turnover}
            if reasons:
                excluded.append(dict(base, reasons=','.join(reasons)))
            else:
                records.append(base)
    return pd.DataFrame(records), pd.DataFrame(excluded)


def ensure_market_inputs(directory, now, progress):
    """Prepare the shared research universe and the two industry master inputs."""
    universe = directory / 'universe.csv'
    master = directory / 'master'
    markets = ('kospi.csv', 'kosdaq.csv')
    have_master = all((master / name).is_file() for name in markets)
    if universe.is_file() and have_master:
        return
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory, prefix='.bootstrap-') as tmp:
        stage = Path(tmp)
        if have_master:
            frames = [pd.read_csv(master / name, dtype=str, keep_default_na=False) for name in markets]
        else:
            progress('최초 운용 준비 · KIS 코스피·코스닥 종목 마스터 수집 중')
            try:
                frames = download_master(stage / 'master')
            except requests.RequestException as exc:
                raise ValueError('KIS 종목 마스터 수집에 실패했습니다. 잠시 후 같은 버튼으로 다시 시도하세요.') from exc
        if not universe.is_file():
            progress('최초 운용 준비 · 시장 종목 목록 생성 중')
            metadata = directory / 'universe-metadata.json'
            settings = json.loads(metadata.read_text(encoding='utf-8-sig')) if metadata.exists() else {}
            threshold = float(settings.get('min_recent_turnover_proxy', 1e9))
            members, excluded = build_universe(frames, threshold)
            if members.empty:
                raise ValueError('KIS 마스터에서 선정된 종목이 없습니다. 시장 종목 목록을 저장하지 않았습니다.')
            members.to_csv(stage / universe.name, index=False, encoding='utf-8-sig')
            excluded.to_csv(stage / 'excluded.csv', index=False, encoding='utf-8-sig')
            settings.update(snapshot_at=now.isoformat(), min_recent_turnover_proxy=threshold,
                            eligible=len(members), excluded=len(excluded), historical_point_in_time=False)
            (stage / metadata.name).write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
        if not have_master:
            master.mkdir(exist_ok=True)
            for path in (stage / 'master').iterdir():
                path.replace(master / path.name)
        if (stage / universe.name).is_file():
            for name in ('excluded.csv', 'universe-metadata.json', universe.name):
                (stage / name).replace(directory / name)


class NaverResearchSource:
    """Public chart used by FinanceDataReader; global request pacing even across worker threads."""
    def __init__(self, cache_dir: Path, interval=.55):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.interval, self.last = interval, 0.
        self.lock = threading.Lock()

    def daily(self, symbol, start, end, refresh=False):
        if symbol not in {'KOSPI','KOSDAQ'} and not re.fullmatch('[0-9A-Z]{6}', str(symbol)):
            raise ValueError('잘못된 종목코드입니다.')
        cache = self.cache_dir/f'{symbol}-{end}.xml'
        if cache.exists() and not refresh:
            content = cache.read_bytes()
        else:
            for attempt in range(3):
                try:
                    with self.lock:
                        time.sleep(max(0, self.interval-(time.monotonic()-self.last)))
                        self.last = time.monotonic()
                    response = requests.get('https://fchart.stock.naver.com/sise.nhn',
                        params={'timeframe':'day', 'count':6000, 'requestType':0, 'symbol':symbol}, timeout=30)
                    response.raise_for_status()
                    content = response.content
                    if b'<item ' not in content:
                        raise ValueError('네이버 일봉 레코드가 없습니다.')
                    cache.write_bytes(content)
                    break
                except (requests.RequestException, ValueError):
                    if attempt == 2:
                        raise ValueError(f'{symbol}: 공개 일봉 수집 실패') from None
                    time.sleep(2*(attempt+1))
        rows = []
        for item in re.findall(r'<item data="(.*?)"', content.decode('utf-8', errors='replace')):
            vals = item.split('|')
            if len(vals) != 6:
                raise ValueError('공개 차트 필드 개수가 변경되었습니다.')
            raw_date = vals[0]
            day = f'{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}'
            if start <= day <= end:
                rows.append([day, symbol, *map(float, vals[1:])])
        return pd.DataFrame(rows, columns=['date','symbol','open','high','low','close','volume'])


def acquire_research(universe_path, output_dir, start, end, refresh=False, include_market_indices=False,required_session=None,progress=None,append_sessions=None):
    from .research_store import recover_research,publish_research,write_json
    root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
    recover_research(root)
    first,last=pd.Timestamp(start),pd.Timestamp(end)
    if (pd.isna(first) or pd.isna(last) or first>last or first.tz is not None or last.tz is not None
            or first!=first.normalize() or last!=last.normalize()):
        raise ValueError('연구 데이터 수집 날짜 범위를 확인하세요.')
    universe = pd.read_csv(universe_path, dtype={'symbol':str,'benchmark':str})
    symbols = set(universe.symbol) | set(universe.benchmark)
    if include_market_indices:
        markets = set(universe.get('market',pd.Series(dtype=str)))
        if not markets or markets-{'KOSPI','KOSDAQ'}:
            raise ValueError('지수 수집에는 universe의 market(KOSPI/KOSDAQ)이 필요합니다.')
        symbols |= markets
    symbols = sorted(symbols)
    if progress:progress('가격 자료 수집 중',0,len(symbols))
    source = NaverResearchSource(root/'raw')
    frames, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(source.daily, symbol, start, end, refresh): symbol for symbol in symbols}
        for number, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                frame = future.result()
                if frame.empty:
                    raise ValueError('요청 기간에 일봉이 없습니다.')
                required={'date','symbol','open','high','low','close','volume'}
                if not required.issubset(frame):raise ValueError('일봉 필드가 누락되었습니다.')
                dates=pd.to_datetime(frame.date,format='%Y-%m-%d',errors='raise')
                if (dates.isna().any() or dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all()
                        or not dates.between(first,last).all()):raise ValueError('수집 일봉의 거래일 범위가 올바르지 않습니다.')
                if not frame.symbol.astype(str).eq(symbol).all():raise ValueError('요청 종목과 응답 종목이 다릅니다.')
                if dates.duplicated().any():raise ValueError('수집 일봉에 중복 거래일이 있습니다.')
                values=frame[['open','high','low','close','volume']].apply(pd.to_numeric,errors='raise')
                if not np.isfinite(values.to_numpy()).all():raise ValueError('수집 일봉에 유효하지 않은 수치가 있습니다.')
                # Preserve raw zero/malformed OHLC bars; features() quarantines these.
                frame=frame.copy();frame['date']=dates.dt.strftime('%Y-%m-%d')
                frame[values.columns]=values
                frames.append(frame)
            except (ValueError,TypeError,requests.RequestException) as exc:
                errors.append({'symbol':symbol,'reason':str(exc)})
            if number % 25 == 0 or number == len(symbols):
                print(f'일봉 수집 {number}/{len(symbols)} | 실패 {len(errors)}', flush=True)
                if progress:progress('가격 자료 수집 중',number,len(symbols))
    if progress:progress('수집한 가격 자료 검증·저장 중')
    prices=(pd.concat(frames,ignore_index=True).sort_values(['symbol','date']) if frames else
        pd.DataFrame(columns=['date','symbol','open','high','low','close','volume']))
    benchmarks=set(universe.benchmark)
    if include_market_indices:benchmarks|=markets
    latest=prices.groupby('symbol').date.max().to_dict()
    sessions={latest[s] for s in benchmarks if s in latest}
    if not errors and (len(sessions)!=1 or any(s not in latest for s in benchmarks)):
        errors.append({'symbol':'benchmarks','reason':'벤치마크 기준 거래일이 일치하지 않습니다.'})
    if not errors and any(day>next(iter(sessions)) for day in latest.values()):
        errors.append({'symbol':'benchmarks','reason':'벤치마크가 종목의 최신 거래일보다 오래되었습니다.'})
    if not errors and required_session is not None and sessions!={str(pd.Timestamp(required_session).date())}:
        errors.append({'symbol':'benchmarks','reason':'요청한 확정 거래일의 벤치마크가 없습니다.'})
    if not errors and (root/'prices.csv').exists() and append_sessions is None:
        # Detect a truncated response even when every symbol returned some rows.
        old=pd.read_csv(root/'prices.csv',usecols=['symbol','date'],dtype=str)
        old=old[old.symbol.isin(symbols)&old.date.between(str(first.date()),str(last.date()))]
        missing=pd.MultiIndex.from_frame(old[['symbol','date']]).difference(pd.MultiIndex.from_frame(prices[['symbol','date']]))
        if len(missing):errors.append({'symbol':'history','reason':f'기존 수집 구간의 {len(missing)}개 일봉이 누락되었습니다.'})
    source_comparison=None
    prior_manifest={}
    if not errors and append_sessions is not None and (root/'prices.csv').exists():
        old=pd.read_csv(root/'prices.csv',dtype={'symbol':str,'date':str})
        required={'date','symbol','open','high','low','close','volume'}
        if not required.issubset(old) or old.duplicated(['date','symbol']).any():
            raise ValueError('기존 일봉의 필드나 날짜·종목 중복을 확인하세요.')
        requested=set(append_sessions)
        if not requested or any(day<str(first.date()) or day>str(last.date()) for day in requested):
            raise ValueError('추가할 거래일이 수집 범위를 벗어났습니다.')
        boundary=[day for day in old.date.unique() if day<min(requested)]
        compared=requested | ({max(boundary)} if boundary else set())
        overlap=old.loc[old.date.isin(compared)].merge(
            prices.loc[prices.date.isin(compared)],on=['date','symbol'],suffixes=('_old','_new'))
        differences=[]
        for row in overlap.itertuples(index=False):
            close_delta=abs(float(row.close_old)-float(row.close_new))/max(float(row.close_new),1)
            volume_delta=abs(float(row.volume_old)-float(row.volume_new))/max(float(row.volume_new),1)
            if close_delta>.001 or volume_delta>.05:
                differences.append({'date':row.date,'symbol':row.symbol,
                                    'close_difference_ratio':close_delta,
                                    'volume_difference_ratio':volume_delta})
        source_comparison={'overlap_checked':len(overlap),'difference_count':len(differences),
                           'differences':differences[:100]}
        existing=pd.MultiIndex.from_frame(old[['date','symbol']])
        fresh=prices.loc[prices.date.isin(requested)]
        fresh=fresh.loc[~pd.MultiIndex.from_frame(fresh[['date','symbol']]).isin(existing)]
        prices=pd.concat([old,fresh],ignore_index=True).sort_values(['symbol','date'])
        for session in sorted(requested):
            available=set(prices.loc[prices.date.eq(session),'symbol'])
            if not benchmarks.issubset(available):
                errors.append({'symbol':'benchmarks',
                               'reason':f'{session} 기준 지수 일봉이 부족합니다.'})
        prior_path=root/'manifest.json'
        if prior_path.exists():prior_manifest=json.loads(prior_path.read_text(encoding='utf-8'))
    attempt=root/'acquisition-attempts';attempt.mkdir(exist_ok=True)
    stamp=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:12]
    attempt_path=attempt/(stamp+'.json')
    manifest = {'source':'NAVER public chart, research-only', 'start':start,'end':end,
        'universe_snapshot_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
        'requested_symbols':len(symbols),'collected_symbols':int(prices.symbol.nunique()),
        'rows':len(prices),'errors':errors,
        'status':'failed' if errors else 'published','published':not errors,
        'attempt_report':str(attempt_path),
        'included_market_indices':include_market_indices,
        'universe_sha256':hashlib.sha256(Path(universe_path).read_bytes()).hexdigest(),
        'limitations':['current survivor/liquidity-selected universe; historical selection bias',
                      'vendor-adjusted OHLC: not executable raw prices',
                      'source adjusted-volume conventions unverified']}
    if source_comparison is not None:
        manifest.update(source='mixed existing research and NAVER public chart',
                        price_sources=[*prior_manifest.get('price_sources',[]),
                                       {'source':'NAVER public chart','sessions':sorted(requested),
                                        'collected_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat()}],
                        source_comparison=source_comparison)
    if errors:
        write_json(attempt_path,manifest)
        if not frames:raise ValueError('수집된 데이터가 없습니다. 기존 가격·매니페스트는 보존됩니다.')
        return manifest
    try:publish_research(root,prices,manifest,on_publish=lambda:write_json(attempt_path,manifest))
    except (OSError,ValueError):
        manifest.update(status='publication_failed',published=False)
        manifest['errors'].append({'symbol':'publication','reason':'게시 실패. 기존 파일 복구 상태를 확인하세요.'})
        write_json(attempt_path,manifest)
        raise
    return manifest


def add_index_data(prices_path, output_path, cache_dir, start, end):
    from .backtest import clean_research_prices
    source=NaverResearchSource(Path(cache_dir))
    prices=pd.read_csv(prices_path,dtype={'symbol':str})
    prices=prices[~prices.symbol.isin(['KOSPI','KOSDAQ'])]
    indices=[source.daily(symbol,start,end) for symbol in ['KOSPI','KOSDAQ']]
    if any(f.empty or f.date.max()<end for f in indices):
        raise ValueError('요청 종료일의 지수 일봉을 확인할 수 없습니다. 완료된 거래일을 지정하세요.')
    combined=pd.concat([prices,*indices],ignore_index=True).sort_values(['symbol','date'])
    clean,bad=clean_research_prices(combined)
    # Never silently discard newly fetched malformed index bars.
    if not bad.empty and bad.symbol.isin(['KOSPI','KOSDAQ']).any():
        raise ValueError('지수 가격 검증에 실패했습니다.')
    out=Path(output_path);out.parent.mkdir(parents=True,exist_ok=True)
    combined.to_csv(out,index=False)
    metadata={'source':'NAVER public index chart','price_unit':'index points','indices':['KOSPI','KOSDAQ'],
              'index_rows':sum(len(f) for f in indices),'start':start,'end':end,
              'volume_note':'지수 거래량은 본 계산에 사용하지 않으며 증권사 주 단위와 비교하지 않습니다.',
              'stock_prices_source':str(prices_path)}
    out.with_suffix('.indices.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    return metadata
