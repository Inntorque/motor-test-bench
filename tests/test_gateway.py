"""Gateway packaging and real local HTTP integration; all device access is disabled."""
from http.client import HTTPConnection
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(os.environ.get('INNTORQUE_TEST_ROOT', Path(__file__).resolve().parents[1]))
SITE = Path(os.environ.get('INNTORQUE_TEST_SITE', ROOT / 'site'))
sys.path.insert(0, str(ROOT))
from gateway import local_services as services
from gateway.catalog import config, digest, engine_version


class StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def server(handler):
    http = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    return http


def stop(http):
    http.shutdown()
    http.server_close()


class GatewaySecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.http = server(services.Handler)
        self.http.token = 'local-test-token'
        self.http.origins = {'https://inntorque.github.io'}
        self.http.manager = Mock()
        self.http.manager.open.return_value = 'http://127.0.0.1:12345/'

    def tearDown(self):
        stop(self.http)
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        connection.request(method, path, body=urlencode(data) if data else None, headers=headers or {})
        response = connection.getresponse()
        value = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return value

    def test_status_has_exact_cors_and_never_starts_devices(self):
        code, headers, body = self.request('GET', '/status', headers={'Origin': 'https://inntorque.github.io'})
        self.assertEqual(code, 200)
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'https://inntorque.github.io')
        self.assertEqual(json.loads(body)['hostApi'], 1)
        self.http.manager.open.assert_not_called()

    def test_unknown_origin_is_rejected(self):
        code, headers, _ = self.request('GET', '/status', headers={'Origin': 'https://other.invalid'})
        self.assertEqual(code, 403)
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_foreign_host_is_rejected(self):
        self.assertEqual(self.request('GET', '/status', headers={'Host': 'other.invalid'})[0], 403)

    def test_second_companion_cannot_share_the_port(self):
        with services.LocalServer(('127.0.0.1', 0), services.Handler) as first:
            with self.assertRaises(OSError):
                services.LocalServer(first.server_address, services.Handler)

    def test_get_cannot_start_app(self):
        self.assertEqual(self.request('GET', '/open/dynamometer')[0], 404)
        self.http.manager.open.assert_not_called()

    def test_open_requires_token_and_preserves_target(self):
        self.assertEqual(self.request('POST', '/open/dynamometer', {'token': 'wrong'})[0], 403)
        self.http.manager.open.assert_not_called()
        code, headers, body = self.request('POST', '/open/dynamometer', {'token': self.http.token, 'version': 'abc'}, {'Origin': 'https://inntorque.github.io'})
        self.assertEqual(code, 200)
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'https://inntorque.github.io')
        self.assertEqual(json.loads(body)['url'], 'http://127.0.0.1:12345/')
        self.http.manager.open.assert_called_once_with('dynamometer', 'abc')

    def test_preflight_is_origin_limited(self):
        code, headers, _ = self.request('OPTIONS', '/status', headers={'Origin': 'https://inntorque.github.io'})
        self.assertEqual(code, 204)
        self.assertEqual(headers['Access-Control-Allow-Private-Network'], 'true')
        self.assertEqual(self.request('OPTIONS', '/status', headers={'Origin': 'https://other.invalid'})[0], 403)


class GatewayArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.site = self.root / 'site'
        self.site.mkdir()
        self.http = server(lambda *args, **kwargs: StaticHandler(*args, directory=str(self.site), **kwargs))
        self.url = f'http://127.0.0.1:{self.http.server_port}/'
        self.patches = [patch.object(services, 'ROOT', self.root), patch.object(services, 'engine_version', return_value='test-engine')]
        for item in self.patches:
            item.start()
        self.manager = services.Manager(self.url, offline=True)
        self.app = {'id': 'sg-6010hb', 'hostApi': 1, 'version': '1' * 16, 'engine': 'test-engine'}
        self.publish()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        stop(self.http)
        self.temp.cleanup()

    def publish(self, extra=None):
        files = {'index.html': b'<title>offline test</title>'}
        metadata = {**self.app, 'files': {key: digest(value) for key, value in files.items()}}
        files['ui.json'] = json.dumps(metadata).encode()
        files.update(extra or {})
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        payload = output.getvalue()
        self.app.update(bundle=f'releases/{self.app["id"]}/{self.app["version"]}.zip', bytes=len(payload), sha256=digest(payload))
        path = self.site / self.app['bundle']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        (self.site / 'catalog.json').write_text(json.dumps({'schema': 1, 'apps': [self.app]}))

    def test_checked_cache_and_corruption_detection(self):
        folder = self.manager.ui(self.app['id'], self.app['version'])
        self.assertEqual((folder / 'index.html').read_bytes(), b'<title>offline test</title>')
        (folder / 'index.html').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'files changed'):
            self.manager.ui(self.app['id'], self.app['version'])

    def test_checksum_mismatch_never_installs(self):
        (self.site / self.app['bundle']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.manager.ui(self.app['id'], self.app['version'])
        self.assertFalse((self.root / 'runtime/gateway/ui' / self.app['id'] / self.app['version']).exists())

    def test_traversal_archive_is_rejected(self):
        self.publish({'../../outside.txt': b'unsafe'})
        with self.assertRaisesRegex(ValueError, 'Unsafe archive'):
            self.manager.ui(self.app['id'], self.app['version'])
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_engine_mismatch_and_stale_version_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, '版本不匹配'):
            self.manager.ui(self.app['id'], '2' * 16)
        with patch.object(services, 'engine_version', return_value='new-engine'):
            with self.assertRaisesRegex(RuntimeError, '版本不匹配'):
                self.manager.ui(self.app['id'], self.app['version'])

    def test_other_app_is_not_interrupted(self):
        active = ({'id': 'dynamometer', 'name': '测功机'}, {'version': 'old'})
        with patch.object(self.manager, 'active', return_value=[active]), patch.object(services.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, '仍在运行'):
                self.manager.open(self.app['id'], self.app['version'])
            spawn.assert_not_called()

    def test_foreign_process_is_not_reused(self):
        with patch.object(services, 'listening', return_value=True), patch.object(services, 'read_json', return_value={'app': 'other'}):
            with self.assertRaisesRegex(RuntimeError, '端口被其他服务占用'):
                self.manager.identity(self.manager.catalog['apps'][0])

    def test_unconfirmed_stop_keeps_the_process_and_explains_why(self):
        app = self.manager.catalog['apps'][0]
        identity = {'closeToken': 'test-close'}
        process = Mock()
        self.manager.processes[app['id']] = process
        error = HTTPError('http://127.0.0.1/', 409, 'Conflict', {}, io.BytesIO(json.dumps({'error': '停机未确认，服务保持运行'}).encode()))
        with patch.object(self.manager, 'active', return_value=[(app, identity)]), patch.object(services, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, '停机未确认'):
                self.manager.close_apps()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()


class GatewaySourceTests(unittest.TestCase):
    def test_icons_are_standalone_svg_images(self):
        for app in config()['apps']:
            svg = ET.fromstring((ROOT / 'gateway/icons' / (app['id'] + '.svg')).read_bytes())
            self.assertEqual(svg.tag, '{http://www.w3.org/2000/svg}svg')
            self.assertEqual(svg.attrib['viewBox'], '0 0 240 240')

    def test_source_snapshots_match_recorded_hashes(self):
        for app in ['sg-6010hb', 'rm-7010']:
            folder = ROOT / 'gateway/hosts' / app
            manifest = json.loads((folder / 'source-manifest.json').read_text())
            for name, checksum in manifest['importedFiles'].items():
                self.assertEqual(digest((folder / 'scripts' / name).read_bytes()), checksum)
            self.assertEqual(digest((ROOT / 'gateway/ui' / app / 'index.html').read_bytes()), manifest['uiSha256'])

    def test_build_catalog_and_bundle_fingerprints(self):
        catalog = json.loads((SITE / 'catalog.json').read_text())
        self.assertEqual([app['id'] for app in catalog['apps']], [app['id'] for app in config()['apps']])
        for app in catalog['apps']:
            self.assertEqual(app['engine'], engine_version(app['id']))
            self.assertEqual(digest((SITE / app['bundle']).read_bytes()), app['sha256'])
            self.assertTrue((SITE / 'apps' / app['id'] / 'index.html').is_file())


class GatewayIntegrationTests(unittest.TestCase):
    def test_three_original_http_apps_without_device_io(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for path in ROOT.glob('*.py'):
                shutil.copy2(path, root / path.name)
            for name in ['gateway', 'workbench', 'protocol', 'profiles', 'dist']:
                shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (root / 'docs').mkdir()
            shutil.copy2(ROOT / 'docs/gateway-deployment.md', root / 'docs/gateway-deployment.md')
            (root / 'scripts').mkdir()
            for name in ['web_server.py', 'lan_access.py', 'instance_lock.py']:
                shutil.copy2(ROOT / 'scripts' / name, root / 'scripts' / name)
            catalog = config()
            catalog['apps'] = [app for app in catalog['apps'] if not app.get('browserOnly')]
            reserved = []
            for app in catalog['apps']:
                sock = socket.socket(); sock.bind(('127.0.0.1', 0)); reserved.append(sock)
                app['port'] = sock.getsockname()[1]
            (root / 'gateway/apps.json').write_text(json.dumps(catalog))
            http = server(lambda *args, **kwargs: StaticHandler(*args, directory=str(SITE), **kwargs))
            for sock in reserved:
                sock.close()
            try:
                with patch.object(services, 'ROOT', root):
                    manager = services.Manager(f'http://127.0.0.1:{http.server_port}/', offline=True)
                    manager.catalog = catalog
                    online = json.loads((SITE / 'catalog.json').read_text())
                    try:
                        for app in (app for app in online['apps'] if not app.get('browserOnly')):
                            with self.subTest(app=app['id']):
                                url = manager.open(app['id'], app['version'])
                                with urlopen(url, timeout=5) as response:
                                    page = response.read().decode()
                                self.assertIn('<html', page.lower())
                                self.assertNotIn('__CONTROL_TOKEN__', page)
                                self.assertNotIn('__MUTATING_ROUTES__', page)
                                identity = manager.identity(next(row for row in catalog['apps'] if row['id'] == app['id']))
                                self.assertEqual(identity['version'], app['version'])
                                self.assertEqual(manager.open(app['id'], app['version']), url)
                                path = 'api/v1/health' if app['id'] == 'dynamometer' else 'api/status'
                                with urlopen(url + path, timeout=5) as response:
                                    state = json.load(response)
                                if app['id'] != 'dynamometer':
                                    self.assertFalse(state['connected'])
                                    self.assertFalse(state.get('adapter', {}).get('open'))
                                if app['id'] == 'rm-7010':
                                    with urlopen(url + 'api/session', timeout=3) as response:
                                        session = json.load(response)
                                    from urllib.request import Request
                                    request = Request(url + 'api/motor?enable=0', data=b'', headers={
                                        'X-Control-Token': session['token'], 'X-Command-Epoch': str(session['command_epoch'])})
                                    with urlopen(request, timeout=5) as response:
                                        response.read()
                                manager.close_apps()
                    finally:
                        manager.close_apps()
            finally:
                stop(http)


if __name__ == '__main__':
    unittest.main()
