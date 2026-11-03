"""Loopback companion: verified per-APP web updates and owned local processes."""
import argparse
import html
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, urljoin
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile
import io

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateway.catalog import config, digest, engine_version
from scripts.instance_lock import InstanceLock


def read_json(url, timeout=8):
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read(2_000_000))


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.2)
        return sock.connect_ex(('127.0.0.1', port)) == 0


class Manager:
    def __init__(self, site_url, offline=False):
        self.catalog = config()
        self.catalog['apps'] = [app for app in self.catalog['apps'] if not app.get('browserOnly')]
        parsed = urlsplit(site_url)
        if site_url != self.catalog['siteUrl'] and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost') and parsed.port):
            raise ValueError('Only the configured Pages site or a loopback test site is allowed')
        self.site_url = site_url.rstrip('/') + '/'
        self.offline = offline
        self.lock = threading.Lock()
        self.processes = {}
        self.folder = ROOT / '.runtime/gateway'
        self.folder.mkdir(parents=True, exist_ok=True)

    def identity(self, app):
        if not listening(app['port']):
            return None
        try:
            value = read_json(f'http://127.0.0.1:{app["port"]}/__inntorque_app', timeout=2)
        except (OSError, ValueError) as error:
            if not listening(app['port']):
                return None
            raise RuntimeError(f'{app["name"]}端口被其他服务占用；请通过原启动方式关闭它') from error
        if not isinstance(value, dict) or value.get('app') != app['id'] or value.get('directory') != str(ROOT) or value.get('hostApi') != 1:
            raise RuntimeError(f'{app["name"]}端口被其他服务占用；请通过原启动方式关闭它')
        return value

    def active(self):
        return [(app, value) for app in self.catalog['apps'] if (value := self.identity(app))]

    def ui(self, app_id, version):
        remote = read_json(urljoin(self.site_url, 'catalog.json'))
        if remote.get('schema') != 1:
            raise ValueError('Unsupported online catalog')
        app = next(row for row in remote['apps'] if row['id'] == app_id)
        if app.get('version') != version or app.get('hostApi') != 1 or app.get('engine') != engine_version(app_id):
            raise RuntimeError('网页与本机控制服务版本不匹配，请更新本机运行包后重新打开')
        bundle = f'releases/{app_id}/{version}.zip'
        if app['bundle'] != bundle or len(version) != 16 or any(c not in '0123456789abcdef' for c in version):
            raise ValueError('Invalid application bundle')
        folder = ROOT / 'runtime/gateway/ui' / app_id / version
        if not folder.is_dir():
            with urlopen(urljoin(self.site_url, bundle), timeout=30) as response:
                payload = response.read(50_000_001)
            if len(payload) > 50_000_000 or len(payload) != app['bytes'] or digest(payload) != app['sha256']:
                raise ValueError('Application archive checksum mismatch')
            folder.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=folder.parent) as temporary:
                destination = Path(temporary) / 'ui'
                destination.mkdir()
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    if sum(item.file_size for item in archive.infolist()) > 50_000_000:
                        raise ValueError('Application archive is too large')
                    names = set()
                    for item in archive.infolist():
                        path = PurePosixPath(item.filename)
                        if (item.filename in names or path.is_absolute() or '..' in path.parts or '\\' in item.filename
                                or ':' in item.filename or item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000):
                            raise ValueError('Unsafe archive member')
                        names.add(item.filename)
                        target = destination.joinpath(*path.parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(item))
                self.verify_ui(destination, app)
                destination.rename(folder)
        self.verify_ui(folder, app)
        return folder

    @staticmethod
    def verify_ui(folder, app):
        metadata = json.loads((folder / 'ui.json').read_text(encoding='utf-8'))
        if any(metadata.get(key) != app[key] for key in ['id', 'version', 'engine', 'hostApi']):
            raise ValueError('Application metadata mismatch')
        actual = {path.relative_to(folder).as_posix(): digest(path.read_bytes()) for path in folder.rglob('*') if path.is_file() and path.name != 'ui.json'}
        if actual != metadata['files'] or 'index.html' not in actual:
            raise ValueError('Application files changed or incomplete')

    def open(self, app_id, version):
        with self.lock:
            app = next(row for row in self.catalog['apps'] if row['id'] == app_id)
            active = self.active()
            if active:
                current, identity = active[0]
                if current['id'] == app_id and identity['version'] == version:
                    return f'http://127.0.0.1:{app["port"]}/'
                raise RuntimeError(f'{current["name"]}仍在运行。请先正常关闭，再切换或更新 APP；正在进行的测试不会被自动中断')
            ui = self.ui(app_id, version)
            command = [sys.executable, '-X', 'utf8', '-u', str(ROOT / 'gateway/app_host.py'), app_id, '--ui', str(ui), '--version', version]
            if self.offline:
                command.append('--offline')
            options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
            with open(self.folder / f'{app_id}.log', 'ab') as log:
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
            self.processes[app_id] = process
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'{app["name"]}启动失败，请查看本机服务日志')
                if listening(app['port']):
                    identity = self.identity(app)
                    if identity['pid'] != process.pid or identity['version'] != version:
                        raise RuntimeError('Application process identity mismatch')
                    return f'http://127.0.0.1:{app["port"]}/'
                time.sleep(.1)
            raise RuntimeError('启动尚未完成，服务日志已保留；未强制结束进程')

    def close_apps(self):
        with self.lock:
            for app, identity in self.active():
                request = Request(f'http://127.0.0.1:{app["port"]}/__inntorque_close', method='POST', data=b'',
                                  headers={'X-Inntorque-Close': identity['closeToken']})
                try:
                    with urlopen(request, timeout=35) as response:
                        result = json.load(response)
                except HTTPError as error:
                    detail = json.loads(error.read())
                    raise RuntimeError(detail.get('error', '上位机拒绝关闭，保留服务')) from error
                if not result.get('ok') or (app['id'] == 'dynamometer' and not result.get('stop_confirmed')):
                    raise RuntimeError('关闭未确认，保留应用服务')
                deadline = time.monotonic() + 10
                while listening(app['port']) and time.monotonic() < deadline:
                    time.sleep(.1)
                if listening(app['port']):
                    raise RuntimeError('Application has not exited')
                if app['id'] in self.processes:
                    self.processes[app['id']].wait(timeout=10)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def valid(self):
        host = self.headers.get('Host', '')
        return host in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}') and self.headers.get('Origin') in (None, *self.server.origins)

    def reply(self, status, payload, kind='application/json; charset=utf-8', extra=None):
        body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        origin = self.headers.get('Origin')
        if origin in self.server.origins:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self.valid():
            return self.reply(403, {'error': 'Host/Origin rejected'})
        self.reply(204, '', extra={'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type'})

    def do_GET(self):
        if not self.valid():
            return self.reply(403, {'error': 'Host/Origin rejected'})
        if self.path == '/status':
            return self.reply(200, {'app': 'inntorque-local-services', 'hostApi': 1, 'pid': os.getpid(), 'directory': str(ROOT), 'token': self.server.token})
        if self.path != '/':
            return self.reply(404, {'error': 'Not found'})
        body = f'<meta charset="utf-8"><title>Inntorque 本机服务</title><h2>本机连接服务已启动</h2><p>请从测试总网关选择 APP。</p><form method="post" action="/close"><input type="hidden" name="token" value="{self.server.token}"><button>确认关闭当前 APP 与输出</button></form>'
        self.reply(200, body, 'text/html; charset=utf-8')

    def do_POST(self):
        if not self.valid():
            return self.reply(403, {'error': 'Host/Origin rejected'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid request body')
            values = parse_qs(self.rfile.read(length).decode())
            if not secrets.compare_digest(values.get('token', [''])[0], self.server.token):
                return self.reply(403, {'error': 'Invalid service token'})
            if self.path in ('/close', '/shutdown'):
                self.server.manager.close_apps()
                if self.path == '/shutdown':
                    self.reply(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                return self.reply(200, '<meta charset="utf-8"><p>当前 APP 已正常关闭。</p><a href="/">返回本机服务</a>', 'text/html; charset=utf-8')
            if not self.path.startswith('/open/'):
                return self.reply(404, {'error': 'Not found'})
            destination = self.server.manager.open(self.path[len('/open/'):], values.get('version', [''])[0])
            self.reply(200, {'url': destination})
        except (OSError, ValueError, RuntimeError, StopIteration, KeyError, zipfile.BadZipFile) as error:
            if self.path.startswith('/open/'):
                return self.reply(409, {'error': str(error)})
            self.reply(409, '<meta charset="utf-8"><title>APP 尚未打开</title><p>' + html.escape(str(error)) + '</p><a href="/">查看本机服务 / 正常关闭当前 APP</a>', 'text/html; charset=utf-8')


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        exclusive = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


def serve(port=5181, site_url=None, offline=False):
    catalog = config()
    manager = Manager(site_url or catalog['siteUrl'], offline)
    with InstanceLock(ROOT / '.runtime/gateway-service.lock'):
        with LocalServer(('127.0.0.1', port), Handler) as server:
            server.daemon_threads = True
            server.manager = manager
            server.token = secrets.token_urlsafe(32)
            site = urlsplit(manager.site_url)
            server.origins = {f'{site.scheme}://{site.netloc}', f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
            print(f'Inntorque local services http://127.0.0.1:{port}', flush=True)
            server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5181)
    parser.add_argument('--site-url')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    serve(args.port, args.site_url, args.offline)
