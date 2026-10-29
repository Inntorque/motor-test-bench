"""Local Ubuntu/Windows launcher. Stops only processes owned by this launcher."""
import json
import os
from pathlib import Path
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
from workbench_config import local_config,migrate_config
from scripts.lan_access import credentials, lan_urls
from scripts.instance_lock import InstanceLock, InstanceBusy
RUNTIME = ROOT / '.runtime'
STATE = RUNTIME / 'services.json'
WIN = os.name == 'nt'


def request(path, data=None, token=None, *, port=5173, versioned=True):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-MotorBench-Token'] = token
    req = urllib.request.Request(f'http://127.0.0.1:{port}' + (path.replace('/api/','/api/v1/',1) if versioned else path),
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


def web_status(port, lan=None, pid=None):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/__workbench/status', timeout=3) as response:
            status = json.load(response)
    except (OSError, ValueError) as error:
        raise RuntimeError(f'端口 {port} 已被占用，无法确认是本工作台；请关闭占用程序后重试') from error
    if (not isinstance(status, dict) or status.get('app') != 'motor-bench-web'
            or status.get('hostApi') != 1 or status.get('directory') != str(ROOT)):
        raise RuntimeError(f'端口 {port} 已被旧版工作台或其他服务占用；请先结束试验并通过原启动方式关闭')
    if lan is not None and bool(status.get('lan')) != lan:
        raise RuntimeError('工作台已运行，但局域网配置不同；请先正常关闭工作台再切换')
    if pid is not None and status.get('pid') != pid:
        raise RuntimeError(f'端口 {port} 已由另一个进程接管，未将其记录为本次启动的服务')
    return status


def spawn(name, argv, port, entries, cwd=ROOT, env=None, lan=False):
    if listening(port):
        web_status(port, lan)
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
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            entries.pop(name)
            save(entries)
            break
        if listening(port):
            web_status(port, lan, process.pid)
            print(f'{name}: 就绪（{port}）', flush=True)
            return
        time.sleep(.1)
    raise RuntimeError(f'{name} 启动失败，请查看 {RUNTIME / (name + ".log")}')


def start():
    RUNTIME.mkdir(exist_ok=True)
    entries = state()
    config = local_config()
    lan = bool(config.get('web', {}).get('lan', False))
    running = listening(5173)
    if running:
        web_status(5173, lan)
    else:
        if listening(8766) or listening(8892):
            raise RuntimeError('旧版 8766/8892 服务仍在运行；请先关闭旧上位机和电机工程网页，释放 USB-CAN 后再启动新版')
        if not (ROOT / 'dist' / 'index.html').exists():
            raise RuntimeError('缺少网页构建产物，请先执行 npm ci 和 npm run build；部署时携带 dist 目录')
    python = ROOT / '.venv' / ('Scripts/python.exe' if WIN else 'bin/python')
    bundled = ROOT / 'runtime' / 'python' / 'python.exe'
    python = str(bundled if WIN and bundled.exists() else python if python.exists() else Path(sys.executable))
    if not running and subprocess.run([python, '-c', 'import serial'], capture_output=True).returncode:
        raise RuntimeError(f'缺少 pySerial，请执行 "{python}" -m pip install -r requirements.txt')
    env = dict(os.environ)
    if not running:
        migrate_config()
    if lan:
        credentials(ROOT)
    spawn('web',[python,'-X','utf8','-u',str(ROOT/'scripts/web_server.py'),*(['--lan'] if lan else [])],5173,entries,env=env,lan=lan)
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
    legacy=listening(8766)
    def stop_request(path,data=None,token=None):
        return request(path,data,token,port=8766 if legacy else 5173,versioned=not legacy)
    if legacy or listening(5173):
        health = stop_request('/api/health')
        if health.get('app')!='motor-test-bench-hardware':raise RuntimeError('通信服务身份不匹配，拒绝控制')
        stop_request('/api/control/stop', {}, health['token'])
        deadline = time.monotonic()+20
        while True:
            safety = stop_request('/api/control/status')
            if not safety.get('stopping'):break
            if time.monotonic()>=deadline:raise RuntimeError('仍在停机确认，保留服务，请检查设备')
            time.sleep(.1)
        if safety.get('blocked'):
            raise RuntimeError('输出关闭未确认，保留服务：'+str(safety.get('errors')))
        health = stop_request('/api/health')
        for key in ('motor-supply','brake-supply'):
            if key in safety.get('unusedChannels',[]):continue
            channel = health['channels'][key]
            snapshot = channel.get('snapshot')
            if (not channel.get('connected') or not snapshot or snapshot.get('status',0)&128
                    or channel.get('outputUncertain')):
                raise RuntimeError(key+' 输出状态未知或仍开启，保留服务，禁止强制退出')
        active_test = stop_request('/api/specialized/status').get('current') or {}
        if active_test.get('state') in ('running','settling'):
            stop_request('/api/specialized/stop', {'id':active_test['id']}, health['token'])
        recording = stop_request('/api/recordings/status?history=0').get('current') or {}
        if recording.get('running'):
            stop_request('/api/recordings/stop', {'id':recording['id']}, health['token'])
    for name in (('web','bridge','motor') if legacy else ('web',)):
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
        with InstanceLock(RUNTIME / 'launcher.lock'):
            start() if sys.argv[1] == 'start' else stop()
    except InstanceBusy:
        print('工作台正在启动或关闭，请稍候；重复启动不会创建第二个后台实例。')
    except Exception as error:
        print(f'错误：{error}', file=sys.stderr)
        sys.exit(1)
