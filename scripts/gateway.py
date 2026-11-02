"""Start or stop only this installation's Inntorque companion service."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateway.catalog import config
from gateway.local_services import listening
from scripts.instance_lock import InstanceLock, InstanceBusy


def status(port=5181):
    with urlopen(f'http://127.0.0.1:{port}/status', timeout=3) as response:
        value = json.load(response)
    if value.get('app') != 'inntorque-local-services' or value.get('hostApi') != 1 or value.get('directory') != str(ROOT):
        raise RuntimeError(f'端口 {port} 被其他服务占用，未结束占用进程')
    return value


def start(no_browser=False):
    if not listening(5181):
        folder = ROOT / '.runtime/gateway'
        folder.mkdir(parents=True, exist_ok=True)
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
        with open(folder / 'service.log', 'ab') as log:
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-u', '-m', 'gateway.local_services'],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        deadline = time.monotonic() + 10
        while not listening(5181) and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('本机服务启动失败，请查看 .runtime/gateway/service.log')
            time.sleep(.1)
        value = status()
        if value['pid'] != process.pid:
            raise RuntimeError('端口被另一个进程接管，未复用')
    else:
        status()
    if not no_browser:
        webbrowser.open(config()['siteUrl'])
    print('Inntorque 本机服务已就绪，打开测试总网关选择 APP。')


def stop():
    if not listening(5181):
        print('本机连接服务未运行。')
        return
    value = status()
    request = Request('http://127.0.0.1:5181/shutdown', data=urlencode({'token': value['token']}).encode())
    with urlopen(request, timeout=50) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError('本机连接服务未确认关闭')
    deadline = time.monotonic() + 10
    while listening(5181) and time.monotonic() < deadline:
        time.sleep(.1)
    if listening(5181):
        raise RuntimeError('本机服务仍在退出，请查看日志；未强制结束进程')
    print('当前 APP 与本机服务已正常关闭。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop'])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        with InstanceLock(ROOT / '.runtime/gateway-launcher.lock'):
            start(args.no_browser) if args.action == 'start' else stop()
    except InstanceBusy:
        print('本机服务正在启动或关闭，重复操作不会创建第二个实例。')
    except Exception as error:
        print(f'错误：{error}', file=sys.stderr)
        sys.exit(1)
