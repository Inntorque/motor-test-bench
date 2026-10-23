"""Production browser entry: built assets and a same-origin local API proxy.

No serial/CAN access here. Device ownership stays in the existing bridge.
"""
import argparse
import http.client
import ipaddress
import json
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.lan_access import Access, credentials, LOGIN


class Handler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    bridge_port = 8766
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'dist'), **kwargs)

    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(35)

    def _valid_host(self):
        port = self.server.server_port
        if self.headers.get('Host', '').lower() in (f'127.0.0.1:{port}', f'localhost:{port}'):
            return True
        if getattr(self.server, 'lan_access', None):
            try:
                host = urlsplit('//' + self.headers.get('Host', ''))
                private = host.hostname in {socket.gethostname().lower(), socket.getfqdn().lower()}
                if not private:
                    private = ipaddress.ip_address(host.hostname).is_private
                if private and host.port == port:
                    return True
            except (ValueError, TypeError):
                pass
        self.close_connection = True
        self.send_error(403, 'Local access only')
        return False

    def _local(self):
        return ipaddress.ip_address(self.client_address[0]).is_loopback

    def _respond(self, code, body, kind='text/html; charset=utf-8', headers=None):
        self.dynamic_response = True
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _authorized(self):
        access = getattr(self.server, 'lan_access', None)
        if self._local() or (access and access.valid(self.headers.get('Cookie'))):
            return True
        if self.path.startswith('/api/'):
            self._respond(401, json.dumps({'error': '局域网登录已失效，请刷新网页重新登录'}, ensure_ascii=False),
                          'application/json; charset=utf-8', {'Cache-Control': 'no-store'})
        else:
            self._respond(200, LOGIN)
        self.close_connection = True
        return False

    def _same_origin(self):
        origin = self.headers.get('Origin')
        if origin is None or origin == 'http://' + self.headers.get('Host', ''):
            return True
        self._respond(403, 'Cross-origin control rejected')
        self.close_connection = True
        return False

    def _login(self):
        access = getattr(self.server, 'lan_access', None)
        if not access:
            return self.send_error(404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 or self.headers.get('Transfer-Encoding'):
                raise ValueError()
            values = parse_qs(self.rfile.read(length).decode('utf-8'))
            session = access.login(values.get('password', [''])[0], self.client_address[0])
        except (ValueError, UnicodeError):
            session = None
        if not session:
            self.close_connection = True
            return self._respond(401, LOGIN.replace('<h2>', '<p>口令不正确或尝试过于频繁，请稍后重试。</p><h2>'))
        self._respond(303, '', headers={'Location': '/',
            'Set-Cookie': f'workbench_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'})

    def end_headers(self):
        if getattr(self, 'dynamic_response', False):
            self.send_header('Cache-Control', 'no-store')
            self.dynamic_response = False
        elif not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable'
                             if urlsplit(self.path).path.startswith('/assets/') and self.command in ('GET', 'HEAD')
                             else 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def _proxy(self):
        upstream = http.client.HTTPConnection('127.0.0.1', self.bridge_port, timeout=30)
        started = False
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 1024 * 1024 or self.headers.get('Transfer-Encoding'):
                self.send_error(400, 'Invalid request body')
                self.close_connection = True
                return
            body = self.rfile.read(length) if length else None
            headers = {key: self.headers[key] for key in ('Content-Type', 'Origin', 'X-MotorBench-Token',
                       'X-Workbench-Client','X-Control-Epoch')
                       if key in self.headers}
            # Only authenticated, same-origin LAN requests reach this trusted proxy.
            # The bridge stays on loopback and still validates its control token.
            if not self._local():
                headers['Origin'] = 'http://127.0.0.1:5173'
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ('connection', 'transfer-encoding', 'server', 'date'):
                    self.send_header(key, value)
            if response.getheader('Content-Length') is None:
                self.send_header('Connection', 'close')
                self.close_connection = True
            self.end_headers()
            started = True
            if self.command != 'HEAD':
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (OSError, ValueError, http.client.HTTPException):
            if not started:
                data = json.dumps({'error': '本机通信服务不可用，请检查启动日志'}, ensure_ascii=False).encode()
                self.send_response(502)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            self.close_connection = True
        finally:
            upstream.close()

    def do_GET(self):
        if not self._valid_host():
            return
        if not self._authorized():
            return
        if self.path == '/__workbench/status' and self._local():
            return self._respond(200, json.dumps({'app': 'motor-bench-web', 'directory': str(ROOT),
                'lan': bool(getattr(self.server, 'lan_access', None))}), 'application/json')
        if self.path.startswith('/api/'):
            return self._proxy()
        return super().do_GET()

    def do_HEAD(self):
        if not self._valid_host():
            return
        if not self._authorized():
            return
        if self.path.startswith('/api/'):
            return self._proxy()
        return super().do_HEAD()

    def do_POST(self):
        if not self._valid_host() or not self._same_origin():
            return
        if self.path == '/login':
            return self._login()
        if not self._authorized():
            return
        if self.path.startswith('/api/'):
            return self._proxy()
        self.send_error(405)

    def list_directory(self, path):
        self.send_error(404)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5173)
    parser.add_argument('--lan', action='store_true')
    args = parser.parse_args()
    if not (ROOT / 'dist' / 'index.html').is_file():
        raise SystemExit('缺少 dist/index.html，请先执行 npm run build')
    with ThreadingHTTPServer(('0.0.0.0' if args.lan else '127.0.0.1', args.port), Handler) as server:
        server.lan_access = Access(credentials(ROOT)) if args.lan else None
        server.serve_forever()


if __name__ == '__main__':
    main()
