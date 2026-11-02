"""Serve a verified UI with its original local control engine."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateway.catalog import config, engine_version


def run(app_id, ui, version, offline=False):
    if offline:
        import serial
        import can
        def denied(*args, **kwargs):
            raise RuntimeError('Offline validation: device access is disabled')
        serial.Serial = denied
        can.Bus = can.interface.Bus = denied
    app = next(row for row in config()['apps'] if row['id'] == app_id)
    identity = {'app': app_id, 'hostApi': 1, 'pid': os.getpid(), 'directory': str(ROOT),
                'version': version, 'engine': engine_version(app_id), 'closeToken': secrets.token_urlsafe(32)}

    class Identity:
        def identity_reply(self, value, code=200):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            host = self.headers.get('Host')
            return host in (f'127.0.0.1:{app["port"]}', f'localhost:{app["port"]}') and self.headers.get('Origin') in (None, 'http://' + host)

        def do_GET(self):
            if self.path == '/__inntorque_app':
                if not self.local_request():
                    return self.identity_reply({'error': 'Host/Origin rejected'}, 403)
                return self.identity_reply(identity)
            return super().do_GET()

        def do_POST(self):
            if self.path == '/__inntorque_close':
                if not self.local_request() or not secrets.compare_digest(self.headers.get('X-Inntorque-Close', ''), identity['closeToken']):
                    return self.identity_reply({'error': 'Invalid local close request'}, 403)
                if app_id != 'dynamometer':
                    if offline:
                        self.server.app.arm(False)
                    return self._route('/api/shutdown', {})
                import hardware_bridge as devices
                devices.SAFETY.stop('操作者关闭上位机', devices.CHANNELS.keys())
                deadline = time.monotonic() + 25
                while devices.SAFETY.status()['stopping'] and time.monotonic() < deadline:
                    time.sleep(.05)
                state = devices.SAFETY.status()
                if state['stopping'] or state['blocked']:
                    return self.identity_reply({'error': '输出关闭未确认，保留服务', 'safety': state}, 409)
                recording = devices.WORKBENCH_RECORDINGS.status() or {}
                if recording.get('running'):
                    devices.WORKBENCH_RECORDINGS.stop(recording['id'])
                self.identity_reply({'ok': True, 'stop_confirmed': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return super().do_POST()

    if app_id == 'dynamometer':
        import workbench.service as service

        class Handler(Identity, service.Handler):
            def __init__(self, *args, **kwargs):
                SimpleHTTPRequestHandler.__init__(self, *args, directory=str(ui), **kwargs)

        service.Handler = Handler
        service.serve(app['port'], connect=not offline)
        return
    engine = ROOT / 'runtime/gateway/apps' / app_id
    source = ROOT / 'gateway/hosts' / app_id
    for path in source.rglob('*.py'):
        destination = engine / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    (engine / '工程说明.txt').write_bytes((ROOT / 'docs/gateway-deployment.md').read_bytes())
    sys.path.insert(0, str(engine / 'scripts'))
    spec = importlib.util.spec_from_file_location('foc_web', engine / 'scripts/foc_web.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['foc_web'] = module
    spec.loader.exec_module(module)
    module.INDEX_HTML = (ui / 'index.html').read_text(encoding='utf-8')
    module.INDEX_BYTES = module.INDEX_HTML.encode('utf-8')

    class Handler(Identity, module.DashboardHandler):
        pass

    module.DashboardHandler = Handler
    if offline:
        module.App.open_bus = lambda self: (None, 'Offline validation: no device access')
    settings = {}
    path = ROOT / 'gateway.local.json'
    if path.is_file():
        settings = json.loads(path.read_text(encoding='utf-8')).get(app_id, {})
    argv = ['--port', str(app['port'])]
    for key in ['channel', 'interface', 'baud', 'node']:
        if key in settings:
            argv.extend(['--' + key, str(settings[key])])
    result = module.main(argv)
    if result:
        raise SystemExit(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('app')
    parser.add_argument('--ui', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    run(args.app, args.ui, args.version, args.offline)
