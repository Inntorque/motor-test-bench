"""Production browser entry: built assets and a same-origin local API proxy.

No serial/CAN access here. Device ownership stays in the existing bridge.
"""
import argparse
import http.client
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


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
        self.close_connection = True
        self.send_error(403, 'Local access only')
        return False

    def end_headers(self):
        if not self.path.startswith('/api/'):
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
            headers = {key: self.headers[key] for key in ('Content-Type', 'Origin', 'X-MotorBench-Token')
                       if key in self.headers}
            # http.client supplies the bridge Host; preserve Origin/token validation.
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
        if self.path.startswith('/api/'):
            return self._proxy()
        return super().do_GET()

    def do_HEAD(self):
        if not self._valid_host():
            return
        if self.path.startswith('/api/'):
            return self._proxy()
        return super().do_HEAD()

    def do_POST(self):
        if not self._valid_host():
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
    args = parser.parse_args()
    if not (ROOT / 'dist' / 'index.html').is_file():
        raise SystemExit('缺少 dist/index.html，请先执行 npm run build')
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
