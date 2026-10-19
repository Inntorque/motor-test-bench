"""One-click per-user deployment. Uses the bundled, isolated Windows runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE))
from scripts.lan_access import credentials, lan_urls


def installed_running(target):
    state = target / '.runtime' / 'services.json'
    if not state.exists():
        return False
    entries = json.loads(state.read_text(encoding='utf-8'))
    for entry in entries.values():
        pid = entry.get('pid')
        if pid:
            result = subprocess.run(['powershell', '-NoProfile', '-Command',
                '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); '
                f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").CommandLine'],
                capture_output=True, text=True, encoding='utf-8')
            if result.returncode:
                raise RuntimeError('无法确认已有服务是否停止，暂不覆盖安装')
            if entry.get('identity', '').lower() in result.stdout.lower() and result.stdout.strip():
                return True
    return False


def install(args):
    if os.name != 'nt' or sys.maxsize < 2**32:
        raise RuntimeError('此包适用于 Windows 10/11 x64')
    manifest = json.loads((SOURCE / 'package-manifest.json').read_text(encoding='utf-8'))
    target = Path(args.install_dir).expanduser().resolve()
    if target == SOURCE or SOURCE in target.parents:
        raise RuntimeError('安装目录必须位于解压包目录之外')
    print('正在校验部署包…', flush=True)
    for name, digest in manifest['files'].items():
        path = (SOURCE / name).resolve()
        if not path.is_relative_to(SOURCE) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError('部署包文件损坏或不完整：' + name)
    if installed_running(target):
        raise RuntimeError('目标工作台仍在运行，请结束试验并运行关闭脚本后再更新')
    try:
        with urllib.request.urlopen('http://127.0.0.1:5173/__workbench/status', timeout=1) as response:
            active = json.load(response)
        if Path(active.get('directory', '')).resolve() == target:
            raise RuntimeError('目标工作台仍在运行，请关闭后再更新')
    except (OSError, ValueError):
        pass
    target.mkdir(parents=True, exist_ok=True)
    print('正在部署独立 Python 环境、通信模块及网页…', flush=True)
    for name in manifest['files']:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, destination)
    shutil.copy2(SOURCE / 'package-manifest.json', target / 'package-manifest.json')
    config_file = target / 'workbench.local.json'
    config = json.loads(config_file.read_text(encoding='utf-8-sig')) if config_file.exists() else {}
    config.setdefault('xigeRoot', 'vendor/xige_foc')
    config.setdefault('motor', {'interface': 'zqwl', 'baud': 500000, 'node': 0})
    config.setdefault('web', {})['lan'] = True
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    data = credentials(target)
    info = '\n'.join(['Torque Workbench / 扭矩工作台', *lan_urls(), '口令 / Password: ' + data['password'],
        '本机地址: http://127.0.0.1:5173/', '同一专用局域网的其他电脑访问上述局域网地址，输入口令。',
        '试验数据保存在本机 recordings。请勿将端口映射到公网。',
        'GitHub Pages 仅为网页展示；真实设备使用这里列出的本机或局域网地址。'])
    (target / 'LAN-ACCESS.txt').write_text(info + '\n', encoding='utf-8-sig')
    (target / '局域网访问说明.txt').write_text(info + '\n', encoding='utf-8-sig')
    python = target / 'runtime/python/python.exe'
    subprocess.run([str(python), '-X', 'utf8', '-c',
        'import sys; print("Runtime paths:", sys.path, flush=True); '
        'import serial, can, wrapt; import hardware_bridge, zqwl, run_recorder; print("Runtime ready")'],
        cwd=target, check=True)
    if not args.skip_firewall:
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(target / 'scripts/windows_firewall.ps1')])
        if result.returncode:
            print('防火墙配置未完成。局域网访问需运行 scripts/windows_firewall.ps1，允许管理员提示。', flush=True)
    if not args.no_shortcuts:
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(target / 'scripts/windows_shortcuts.ps1'), '-InstallDir', str(target)], check=True)
    from serial.tools import list_ports
    print('识别到的串口：', ', '.join(port.device for port in list_ports.comports()) or '暂无；连接适配器后检查设备管理器/厂家驱动')
    print('部署完成：' + str(target), flush=True)
    print('访问地址与口令：' + str(target / 'LAN-ACCESS.txt'), flush=True)
    print('Windows 网络类型需为“专用网络”；防火墙只允许同一局域网访问。', flush=True)
    if not args.no_start:
        subprocess.run([str(python), '-X', 'utf8', str(target / 'scripts/workbench.py'), 'start'], cwd=target, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-dir', default=str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Inntorque' / 'TorqueWorkbench'))
    parser.add_argument('--no-start', action='store_true')
    parser.add_argument('--skip-firewall', action='store_true')
    parser.add_argument('--no-shortcuts', action='store_true')
    try:
        install(parser.parse_args())
    except Exception as error:
        print('安装失败：' + str(error), file=sys.stderr)
        sys.exit(1)
