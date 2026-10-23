"""Local Ubuntu/Windows launcher. Stops only processes owned by this launcher."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workbench_config import local_config, xige_root, motor_arguments
from scripts.lan_access import credentials, lan_urls
RUNTIME = ROOT / '.runtime'
STATE = RUNTIME / 'services.json'
WIN = os.name == 'nt'


def request(path, data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-MotorBench-Token'] = token
    req = urllib.request.Request('http://127.0.0.1:8766' + path,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.3)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(entries):
    RUNTIME.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(entries, indent=2), encoding='utf-8')


def command_line(pid):
    if WIN:
        result = subprocess.run(['powershell', '-NoProfile', '-Command',
            '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); '
            f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").CommandLine'],
            capture_output=True, text=True, encoding='utf-8')
        return result.stdout.strip()
    try:
        return Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
    except FileNotFoundError:
        return ''


def spawn(name, argv, port, entries, cwd=ROOT, env=None):
    if listening(port):
        if name == 'bridge' and request('/api/health').get('app') != 'motor-test-bench-hardware':
            raise RuntimeError('8766 被其他程序占用')
        if name == 'web':
            with urllib.request.urlopen('http://127.0.0.1:5173/', timeout=3) as r:
                if '扭矩工作台' not in r.read().decode('utf-8'):
                    raise RuntimeError('5173 被其他程序占用')
        if name == 'motor':
            with urllib.request.urlopen('http://127.0.0.1:8892/api/status', timeout=3) as r:
                if Path(json.load(r).get('project_root', '')).resolve() != Path(cwd).resolve():
                    raise RuntimeError('8892 不是所配置的电机参考工程，拒绝复用')
        # Recognize this project's existing development service units.
        unit = {'bridge': 'ai-job-torque-workbench-bridge.service',
                'web': 'ai-job-torque-workbench-web.service'}.get(name)
        if not WIN and unit and shutil.which('systemctl'):
            info = subprocess.run(['systemctl', '--user', 'show', unit,
                '--property=WorkingDirectory', '--property=ActiveState'], capture_output=True, text=True)
            if f'WorkingDirectory={ROOT}' in info.stdout.splitlines() and 'ActiveState=active' in info.stdout.splitlines():
                entries[name] = {'unit': unit, 'directory': str(ROOT)}
                save(entries)
        print(f'{name}: 端口 {port} 已运行，复用现有服务', flush=True)
        return
    log = open(RUNTIME / (name + '.log'), 'ab')
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if WIN else {'start_new_session': True}
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, **options)
    finally:
        log.close()
    entries[name] = {'pid': process.pid, 'identity': next((arg for arg in argv if arg.endswith('.py')), argv[1])}
    save(entries)
    for _ in range(100):
        if listening(port):
            print(f'{name}: 就绪（{port}）', flush=True)
            return
        if process.poll() is not None:
            break
        time.sleep(.1)
    raise RuntimeError(f'{name} 启动失败，请查看 {RUNTIME / (name + ".log")}')


def start():
    RUNTIME.mkdir(exist_ok=True)
    entries = state()
    if not (ROOT / 'dist' / 'index.html').exists():
        raise RuntimeError('缺少网页构建产物，请先执行 npm ci 和 npm run build；部署时携带 dist 目录')
    python = ROOT / '.venv' / ('Scripts/python.exe' if WIN else 'bin/python')
    bundled = ROOT / 'runtime' / 'python' / 'python.exe'
    python = str(bundled if WIN and bundled.exists() else python if python.exists() else Path(sys.executable))
    if subprocess.run([python, '-c', 'import serial'], capture_output=True).returncode:
        raise RuntimeError(f'缺少 pySerial，请执行 "{python}" -m pip install -r requirements.txt')
    env = dict(os.environ)
    config = local_config()
    lan = bool(config.get('web', {}).get('lan', False))
    if lan:
        credentials(ROOT)
        if listening(5173):
            try:
                with urllib.request.urlopen('http://127.0.0.1:5173/__workbench/status', timeout=3) as response:
                    web_status = json.load(response)
                if web_status.get('app') != 'motor-bench-web' or not web_status.get('lan') or Path(web_status.get('directory', '')).resolve() != ROOT:
                    raise ValueError('different service')
            except Exception:
                raise RuntimeError('5173 已由其他或非局域网服务占用，请先关闭原工作台再启动') from None
    xige = xige_root(config)
    env['MOTORBENCH_XIGE_ROOT'] = str(xige.resolve())
    foc = xige / 'scripts/foc_web.py'
    if foc.exists():
        foc_python = xige / '.venv' / ('Scripts/python.exe' if WIN else 'bin/python')
        motor_python = str(foc_python) if foc_python.exists() else python
        if not listening(8892) and subprocess.run([motor_python, '-c', 'import serial, can'], capture_output=True).returncode:
            raise RuntimeError(f'电机服务缺少依赖，请执行 "{motor_python}" -m pip install pyserial python-can')
        spawn('motor', [motor_python, '-X', 'utf8', '-u', str(foc), '--port', '8892', *motor_arguments(config)],
              8892, entries, cwd=xige, env=env)
    else:
        print('提示：未找到 xige_foc，请设置 MOTORBENCH_XIGE_ROOT；电源和传感器仍可使用。', flush=True)
    spawn('bridge', [python, '-X', 'utf8', '-u', str(ROOT / 'hardware_bridge.py')], 8766, entries, env=env)
    spawn('web', [python, '-X', 'utf8', '-u', str(ROOT / 'scripts' / 'web_server.py'), *(['--lan'] if lan else [])], 5173, entries)
    if not os.environ.get('MOTORBENCH_NO_BROWSER'):
        webbrowser.open('http://127.0.0.1:5173/')
    if lan:
        info = '\n'.join(['扭矩工作台 · 局域网访问', *lan_urls(),
            '口令：' + credentials(ROOT)['password'],
            '其他电脑连接同一专用局域网，在浏览器打开上述地址并输入口令。',
            '试验记录保存于本机 recordings；硬件接在本机。只使用信任的局域网，不向公网映射端口。'])
        (ROOT / '局域网访问说明.txt').write_text(info + '\n', encoding='utf-8-sig')
        (ROOT / 'LAN-ACCESS.txt').write_text(info + '\n', encoding='utf-8-sig')
        print(info)
    print('扭矩工作台：http://127.0.0.1:5173/（连接后由操作者开启输出）')


def stop():
    entries = state()
    if listening(8766):
        health = request('/api/health')
        request('/api/control/stop', {}, health['token'])
        deadline = time.monotonic()+20
        while True:
            safety = request('/api/control/status')
            if not safety.get('stopping'):break
            if time.monotonic()>=deadline:raise RuntimeError('仍在停机确认，保留服务，请检查设备')
            time.sleep(.1)
        if safety.get('blocked'):
            raise RuntimeError('输出关闭未确认，保留服务：'+str(safety.get('errors')))
        health = request('/api/health')
        for key in ('motor-supply','brake-supply'):
            if key in safety.get('unusedChannels',[]):continue
            channel = health['channels'][key]
            snapshot = channel.get('snapshot')
            if (not channel.get('connected') or not snapshot or snapshot.get('status',0)&128
                    or channel.get('outputUncertain')):
                raise RuntimeError(key+' 输出状态未知或仍开启，保留服务，禁止强制退出')
        active_test = request('/api/specialized/status').get('current') or {}
        if active_test.get('state') in ('running','settling'):
            request('/api/specialized/stop', {'id':active_test['id']}, health['token'])
        recording = request('/api/recordings/status?history=0').get('current') or {}
        if recording.get('running'):
            request('/api/recordings/stop', {'id':recording['id']}, health['token'])
    for name in ('web', 'bridge', 'motor'):
        entry = entries.get(name)
        if not entry:
            continue
        if entry.get('unit'):
            info = subprocess.run(['systemctl', '--user', 'show', entry['unit'],
                '--property=WorkingDirectory', '--value'], capture_output=True, text=True, check=True)
            if info.stdout.strip() != str(ROOT):
                raise RuntimeError(f'{name}: 服务目录已变化，拒绝关闭')
            subprocess.run(['systemctl', '--user', 'stop', entry['unit']], check=True)
            entries.pop(name)
            save(entries)
            continue
        pid = entry['pid']
        command = command_line(pid)
        if command and entry['identity'].lower() not in command.lower():
            raise RuntimeError(f'{name}: PID 已被其他进程使用，拒绝关闭')
        if command:
            if WIN:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], check=True)
            else:
                os.killpg(pid, signal.SIGTERM)
                for _ in range(50):
                    if not command_line(pid):
                        break
                    time.sleep(.1)
                if command_line(pid):
                    raise RuntimeError(f'{name} 尚未退出，请查看日志')
        entries.pop(name)
        save(entries)
    print('已关闭脚本管理的服务；其他外部服务由其原启动方式关闭。')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('start', 'stop'):
            raise RuntimeError('用法：python scripts/workbench.py start|stop')
        start() if sys.argv[1] == 'start' else stop()
    except Exception as error:
        print(f'错误：{error}', file=sys.stderr)
        sys.exit(1)
