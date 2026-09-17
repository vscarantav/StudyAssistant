"""Serve local course plans with durable shared progress. No external service needed."""
import argparse
import json
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from course_plans.sources import ROOT
from course_plans.progress import ProgressStore

class Handler(SimpleHTTPRequestHandler):
    def send_json(self,value,status=200):
        raw=json.dumps(value).encode(); self.send_response(status)
        self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def translate_path(self,path):
        root=Path(self.directory).resolve()
        resolved=Path(super().translate_path(path)).resolve()
        return str(resolved if resolved.is_relative_to(root) else root/'__forbidden__')
    def do_GET(self):
        if self.headers.get('Host') not in self.server.allowed_hosts:
            return self.send_json({'error':'Invalid host'},403)
        if urlsplit(self.path).path=='/api/progress':
            try: return self.send_json(self.server.progress.read())
            except (ValueError,OSError) as e: return self.send_json({'error':str(e)},500)
        if self.path=='/': self.path='/course_plans/index.html'
        if urlsplit(self.path).path.startswith('/api/'): return self.send_json({'error':'Not found'},404)
        return super().do_GET()
    def do_POST(self):
        if self.headers.get('Host') not in self.server.allowed_hosts or self.headers.get('Origin') not in self.server.allowed_origins:
            return self.send_json({'error':'Only same-origin local requests are allowed.'},403)
        if urlsplit(self.path).path not in ('/api/progress','/api/progress/import'): return self.send_json({'error':'Not found'},404)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=2_000_000: raise ValueError('Invalid request size')
            body=json.loads(self.rfile.read(length))
            manifest=json.loads((Path(self.directory)/'assets/learning-manifest.json').read_text())
            allowed={k for c in manifest['courses'].values() for k in c['items']}
            state=self.server.progress.update(body['items'],body.get('revision'), None if self.path.endswith('/import') else allowed)
            self.send_json(state)
        except RuntimeError as e: self.send_json({'error':str(e)},409)
        except (ValueError,KeyError,TypeError) as e: self.send_json({'error':str(e)},400)
        except OSError as e: self.send_json({'error':f'Progress was not saved: {e}'},500)
    def list_directory(self,path): return self.send_error(404,'No directory index')
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-cache')
        super().end_headers()

def create_server(port=8765,output=None,progress=None):
    server=ThreadingHTTPServer(('127.0.0.1',port),partial(Handler,directory=str(output or ROOT/'output')))
    port=server.server_address[1]
    server.allowed_hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    server.allowed_origins={f'http://{h}' for h in server.allowed_hosts}
    server.progress=ProgressStore(progress or ROOT/'data/learning_progress.json')
    return server

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--port',type=int,default=8765); p.add_argument('--open',action='store_true')
    args=p.parse_args(); server=create_server(args.port)
    url=f'http://127.0.0.1:{args.port}/course_plans/index.html'
    print(f'Learning dashboard: {url}',flush=True)
    print('Progress is stored in data/learning_progress.json. Stop with Ctrl+C.',flush=True)
    if args.open: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__=='__main__': main()
