"""Loopback-only HTTP UI with same-origin checks and per-process session protection."""
from http import cookies
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs,urlsplit

from .ui_service import Operations


def make_server(root,port=8765):
    operations=Operations(root);token=secrets.token_hex(32)
    assets=Path(__file__).parent/'web'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def allowed(self):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:return False
            origin=self.headers.get('Origin')
            return origin is None or origin in {'http://'+h for h in hosts}

        def authenticated(self):
            jar=cookies.SimpleCookie()
            try:jar.load(self.headers.get('Cookie',''))
            except cookies.CookieError:return False
            return 'trendfollow_ui' in jar and secrets.compare_digest(jar['trendfollow_ui'].value,token)

        def send(self,status,value,kind='application/json; charset=utf-8',session=False):
            data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8') if kind.startswith('application/json') else value
            self.send_response(status)
            self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            if session:self.send_header('Set-Cookie',f'trendfollow_ui={token}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers();self.wfile.write(data)

        def do_GET(self):
            if not self.allowed():return self.send(403,{'error':'로컬 UI에서 접속하세요.'})
            url=urlsplit(self.path);query=parse_qs(url.query)
            if url.path in {'/','/app.js','/style.css'}:
                name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[url.path]
                kind={'/':'text/html','/app.js':'text/javascript','/style.css':'text/css'}[url.path]+'; charset=utf-8'
                return self.send(200,(assets/name).read_bytes(),kind,session=url.path=='/')
            if not self.authenticated():return self.send(403,{'error':'UI 페이지를 먼저 여세요.'})
            try:
                key=query.get('workspace',[''])[0]
                if url.path=='/api/bootstrap':result=dict(workspaces=operations.workspaces(),presets=operations.presets(),csrf=token)
                elif url.path=='/api/snapshot':result=operations.snapshot(key)
                elif url.path=='/api/pulse':result=operations.pulse(key,query.get('as_of',[None])[0])
                elif url.path=='/api/intraday':result=operations.intraday(key or None)
                elif url.path=='/api/prices':result=operations.prices(key,query.get('symbol',[''])[0])
                else:return self.send(404,{'error':'요청한 화면을 찾을 수 없습니다.'})
                self.send(200,result)
            except (ValueError,FileNotFoundError) as error:self.send(400,{'error':str(error)})
            except Exception:self.send(500,{'error':'자료를 읽지 못했습니다. 경로와 파일 형식을 확인하세요.'})

        def do_POST(self):
            if (not self.allowed() or not self.authenticated() or
                    not secrets.compare_digest(self.headers.get('X-UI-Token',''),token)):
                return self.send(403,{'error':'허용되지 않은 UI 요청입니다.'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=65536:raise ValueError('요청 크기를 확인하세요.')
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict):raise ValueError('JSON 객체가 필요합니다.')
                path=urlsplit(self.path).path
                if path=='/api/workspaces':result=operations.create_workspace(payload)
                elif path=='/api/jobs':result=operations.submit(payload)
                elif path=='/api/watch/start':result=operations.start_watch(payload)
                elif path=='/api/watch/stop':result=operations.stop_watch()
                else:return self.send(404,{'error':'지원하지 않는 작업입니다.'})
                self.send(200,result)
            except (ValueError,TypeError,KeyError,FileNotFoundError) as error:self.send(400,{'error':str(error)})
            except Exception:self.send(500,{'error':'작업 요청을 처리하지 못했습니다. 입력과 파일 경로를 확인하세요.'})

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.operations=operations
    return server


def serve(root,port=8765):
    if not 0<=port<=65535:raise ValueError('포트를 확인하세요.')
    server=make_server(root,port)
    print(f'로컬 운용 UI: http://127.0.0.1:{server.server_port}',flush=True)
    print('종료: Ctrl+C · 증권사 주문 기능 없음',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();server.operations.close()
