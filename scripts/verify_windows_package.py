"""Hardware-free Windows release verification, executed with the bundled Python."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    assert os.name == 'nt', 'Run this check on Windows'
    import serial, can, wrapt
    print('Bundled Python and Windows communication dependencies imported')
    assert Path(sys.executable).resolve() == (ROOT / 'runtime/python/python.exe').resolve()
    with tempfile.TemporaryDirectory(prefix='workbench-deployment-') as folder:
        target = Path(folder) / '工作台 install'
        installer = [sys.executable, '-X', 'utf8', str(ROOT / 'scripts/install_windows.py'),
                     '--install-dir', str(target), '--no-start', '--skip-firewall', '--no-shortcuts']
        subprocess.run(installer, check=True)
        (target / 'recordings').mkdir(exist_ok=True)
        marker = target / 'recordings/preserve.txt'
        marker.write_text('keep real data', encoding='utf-8')
        secret = (target / '.runtime/lan-access.json').read_bytes()
        subprocess.run(installer, check=True)
        assert marker.read_text() == 'keep real data'
        assert (target / '.runtime/lan-access.json').read_bytes() == secret
        python = str(target / 'runtime/python/python.exe')
        launcher = str(target / 'scripts/workbench.py')
        env = dict(os.environ, MOTORBENCH_NO_BROWSER='1')
        started = False
        try:
            subprocess.run([python, '-X', 'utf8', launcher, 'start'], env=env,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            started = True
            with urllib.request.urlopen('http://127.0.0.1:5173/') as response:
                assert '扭矩工作台' in response.read().decode()
            with urllib.request.urlopen('http://127.0.0.1:5173/api/health') as response:
                health = json.load(response)
            assert health['app'] == 'motor-test-bench-hardware'
            assert not any(row['connected'] for row in health['channels'].values())
            with urllib.request.urlopen('http://127.0.0.1:5173/__workbench/status') as response:
                assert json.load(response)['lan'] is True
            assert subprocess.run(installer, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode != 0
            print('PASS: install/update preservation, start one application service, HTTP/API, no simulated devices, refuse live overwrite')
        except Exception:
            for log in (target / '.runtime').glob('*.log'):
                print(log.name + ':\n' + log.read_text(encoding='utf-8', errors='replace')[-12000:])
            raise
        finally:
            try:
                subprocess.run([python, '-X', 'utf8', launcher, 'stop'], check=started, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as error:
                print(error.stdout.decode('utf-8', 'replace'))
                raise
        for port in (5173, 8766, 8892):
            with socket.socket() as sock:
                assert sock.connect_ex(('127.0.0.1', port)) != 0, f'{port} still listening'
        print('PASS: clean shutdown of owned Windows processes')
    verify_lan()


def verify_lan():
    from workbench.service import Handler,devices
    from scripts.lan_access import Access
    class RemoteClient(Handler):
        def _local(self): return False
    server=ThreadingHTTPServer(('127.0.0.1',0),RemoteClient)
    server.lan_access=Access({'password':'example-password','secret':'x'*64})
    server.closing=threading.Event()
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result
        origin = f'http://127.0.0.1:{server.server_port}'
        assert request('GET', '/api/v1/health')[0] == 401
        assert b'password' in request('GET', '/')[2]
        assert request('POST', '/login', 'password=example-password', {'Origin': 'http://outside.invalid'})[0] == 403
        code, headers, _ = request('POST', '/login', 'password=example-password', {'Origin': origin})
        assert code == 303 and 'HttpOnly' in headers['Set-Cookie']
        cookie = headers['Set-Cookie'].split(';')[0]
        assert request('GET', '/api/v1/health', headers={'Cookie': cookie})[0] == 200
        headers = {'Cookie': cookie, 'Origin': origin, 'X-MotorBench-Token': devices.TOKEN}
        assert request('POST', '/api/v1/control/heartbeat', '{}', headers)[0] == 200
        assert request('POST', '/api/v1/control/heartbeat', '{}', {**headers, 'Origin': 'http://outside.invalid'})[0] == 403
        assert request('GET', '/', headers={'Host': 'outside.invalid'})[0] == 403
        assert request('GET', '/api/v1/health', headers={'Cookie': cookie + 'invalid'})[0] == 401
        print('PASS: LAN password gate, signed session, same-origin local API, unauthorized/foreign-origin rejection')
    finally:
        server.closing.set();server.shutdown();server.server_close()


if __name__ == '__main__':
    main()
