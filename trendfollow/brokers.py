"""Quote-only REST adapters. Secrets and server response bodies are never logged."""
from dataclasses import dataclass, field
from datetime import date, timedelta
import os
from pathlib import Path
import re
import time

from dotenv import dotenv_values
import pandas as pd
import requests


class BrokerError(RuntimeError):
    pass


@dataclass(repr=False)
class Credentials:
    provider: str
    environment: str
    app_key: str = field(repr=False)
    secret: str = field(repr=False)

    @classmethod
    def load(cls, provider: str, path: Path):
        if provider not in {'kis', 'kiwoom'}:
            raise ValueError('provider must be kis or kiwoom')
        values = {**dotenv_values(path, interpolate=False), **os.environ}
        prefix = provider.upper()
        env = values.get(f'{prefix}_ENV', 'paper')
        if env not in {'paper', 'live'}:
            raise ValueError(f'{prefix}_ENV는 paper 또는 live여야 합니다.')
        secret_name = 'APP_SECRET' if provider == 'kis' else 'SECRET_KEY'
        key, secret = values.get(f'{prefix}_APP_KEY', ''), values.get(f'{prefix}_{secret_name}', '')
        if not key or not secret:
            raise ValueError(f'{prefix} 키와 시크릿을 .env에 입력하세요.')
        return cls(provider, env, key, secret)


class QuoteClient:
    def __init__(self, credentials: Credentials, session=None, delay: float = 1.1):
        self.credentials = credentials
        self.session = session or requests.Session()
        self.delay = delay
        self.token = None
        self.last_request = 0.
        self.base = ({'live': 'https://openapi.koreainvestment.com:9443',
                      'paper': 'https://openapivts.koreainvestment.com:29443'}
                     if credentials.provider == 'kis' else
                     {'live': 'https://api.kiwoom.com', 'paper': 'https://mockapi.kiwoom.com'})[credentials.environment]

    def _send(self, method, path, **kwargs):
        allowed = {'/oauth2/tokenP', '/oauth2/token', '/oauth2/Approval', '/api/dostk/chart',
                   '/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'}
        if path not in allowed:
            raise BrokerError('시세 조회·인증 외의 경로는 지원하지 않습니다.')
        time.sleep(max(0, self.delay-(time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        try:
            response = self.session.request(method, self.base+path, timeout=30,
                                            allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise BrokerError('API 네트워크 요청 실패. 연결 상태를 확인하세요.') from None
        if response.status_code != 200:
            raise BrokerError(f'API HTTP {response.status_code}. 환경·키·등록 IP를 확인하세요.')
        try:
            payload = response.json()
        except ValueError:
            raise BrokerError('API 응답이 JSON 형식이 아닙니다.') from None
        if not isinstance(payload, dict):
            raise BrokerError('API 응답 구조가 올바르지 않습니다.')
        if str(payload.get('rt_cd', '0')) != '0' or str(payload.get('return_code', '0')) != '0':
            # Never echo free-form upstream messages: they can contain sensitive values.
            raise BrokerError('API가 요청을 거절했습니다. 서버 환경·권한·호출 한도를 확인하세요.')
        return payload, response.headers

    def authenticate(self):
        c = self.credentials
        if c.provider == 'kis':
            body, _ = self._send('POST', '/oauth2/tokenP', json={
                'grant_type': 'client_credentials', 'appkey': c.app_key, 'appsecret': c.secret})
            self.token = body.get('access_token')
        else:
            body, _ = self._send('POST', '/oauth2/token', json={
                'grant_type': 'client_credentials', 'appkey': c.app_key, 'secretkey': c.secret},
                headers={'api-id': 'au10001', 'Content-Type': 'application/json;charset=UTF-8'})
            self.token = body.get('token')
        if not isinstance(self.token, str) or not self.token:
            raise BrokerError('토큰 발급 결과를 확인할 수 없습니다.')

    def daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        if not re.fullmatch(r'[0-9A-Z]{6}', symbol):
            raise ValueError('KRX 종목코드 6자리를 입력하세요.')
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
        if self.token is None:
            self.authenticate()
        rows = []
        c = self.credentials
        headers = {'authorization': f'Bearer {self.token}', 'Content-Type': 'application/json;charset=UTF-8'}
        cursor, continuation, next_key = last, 'N', ''
        previous_key = None
        # A cap is a hard error, not silently truncated history.
        for _ in range(200):
            if c.provider == 'kis':
                headers.update(appkey=c.app_key, appsecret=c.secret, tr_id='FHKST03010100', custtype='P')
                body, response_headers = self._send('GET', '/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
                    headers=headers, params={'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': symbol,
                    'FID_INPUT_DATE_1': first.strftime('%Y%m%d'), 'FID_INPUT_DATE_2': cursor.strftime('%Y%m%d'),
                    'FID_PERIOD_DIV_CODE': 'D', 'FID_ORG_ADJ_PRC': '0'})
                records = body.get('output2', [])
                fields = ['stck_bsop_date', 'stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_clpr', 'acml_vol']
            else:
                headers.update({'api-id': 'ka10081', 'cont-yn': continuation, 'next-key': next_key})
                body, response_headers = self._send('POST', '/api/dostk/chart', headers=headers,
                    json={'stk_cd': symbol, 'base_dt': last.strftime('%Y%m%d'), 'upd_stkpc_tp': '1'})
                records = body.get('stk_dt_pole_chart_qry', [])
                fields = ['dt', 'open_pric', 'high_pric', 'low_pric', 'cur_prc', 'trde_qty']
            if not isinstance(records, list):
                raise BrokerError('일봉 목록 형식이 올바르지 않습니다.')
            if not records:
                break
            page_dates = []
            for record in records:
                if not isinstance(record, dict):
                    raise BrokerError('일봉 레코드 형식이 올바르지 않습니다.')
                raw_date = record.get(fields[0])
                if raw_date in (None, ''):
                    continue
                try:
                    dt = date.fromisoformat(f'{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}')
                    numbers = [float(record[f]) for f in fields[1:]]
                except (ValueError, TypeError, KeyError):
                    raise BrokerError('일봉 필드가 누락되었거나 값이 올바르지 않습니다.') from None
                page_dates.append(dt)
                if first <= dt <= last:
                    rows.append([dt.isoformat(), symbol, *numbers])
            if not page_dates:
                raise BrokerError('유효한 일봉 거래일이 없습니다.')
            oldest = min(page_dates)
            if oldest <= first:
                break
            if c.provider == 'kis':
                new_cursor = oldest - timedelta(days=1)
                if new_cursor >= cursor:
                    raise BrokerError('KIS 과거 일봉 조회가 진행되지 않습니다.')
                cursor = new_cursor
            else:
                continuation = response_headers.get('cont-yn', 'N')
                next_key = response_headers.get('next-key', '')
                if continuation != 'Y':
                    break
                if not next_key or next_key == previous_key:
                    raise BrokerError('키움 연속조회 키가 누락되었거나 반복되었습니다.')
                previous_key = next_key
        else:
            raise BrokerError('조회 페이지 한도에 도달했습니다. 조회 기간을 줄이세요.')
        if not rows:
            raise BrokerError('조회 기간의 일봉 데이터가 없습니다.')
        return pd.DataFrame(rows, columns=['date', 'symbol', 'open', 'high', 'low', 'close', 'volume']).drop_duplicates(['date', 'symbol']).sort_values('date')
