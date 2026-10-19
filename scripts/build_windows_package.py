"""Build a self-contained x64 Windows ZIP; no tools required on the target PC."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = '3.13.16'
PYTHON_SHA256 = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'
PYTHON_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
MODULES = ['hardware_bridge.py', 'motor_current.py', 'sensor_hex.py', 'specialized_tests.py',
           'automatic_tests.py', 'workbench_recordings.py', 'background_recording.py',
           'brake_current.py', 'loaded_timeline.py', 'workbench_config.py']


def extract(archive, destination, wheel=False):
    with zipfile.ZipFile(archive) as zip_file:
        for item in zip_file.infolist():
            if item.is_dir():
                continue
            parts = Path(item.filename).parts
            if wheel and parts[0].endswith('.data'):
                if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                    continue
                parts = parts[2:]
            path = destination.joinpath(*parts).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise ValueError('Unsafe archive path')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zip_file.read(item))


def build(output, cached_python=None):
    if not (ROOT / 'dist/index.html').exists():
        raise RuntimeError('Run npm ci and npm run build first')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / 'TorqueWorkbench-Windows-x64'
    # Never overwrite an arbitrary existing destination or any measurement data.
    package.mkdir()
    cache = output / 'downloads'
    cache.mkdir(exist_ok=True)
    archive = Path(cached_python) if cached_python else cache / 'python-embed.zip'
    if not archive.exists():
        with urllib.request.urlopen(PYTHON_URL, timeout=60) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
        raise RuntimeError('Official Python ZIP checksum mismatch')
    runtime = package / 'runtime/python'
    runtime.mkdir(parents=True)
    extract(archive, runtime)
    # The Windows path bootstrap uses native separators when resolving parent paths.
    # Isolated embedded Python does not automatically add the executed script's folder.
    (runtime / 'python313._pth').write_text(
        'python313.zip\n.\nLib\\site-packages\n..\\..\n..\\..\\vendor\\xige_foc\\scripts\nimport site\n',
        encoding='ascii')
    wheels = cache / 'wheels'
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '--platform', 'win_amd64',
                    '--python-version', '313', '--implementation', 'cp', '--abi', 'cp313',
                    '--dest', str(wheels), '-r', str(ROOT / 'requirements-windows.txt')], check=True)
    libraries = runtime / 'Lib/site-packages'
    for wheel in wheels.glob('*.whl'):
        extract(wheel, libraries, wheel=True)
    for name in MODULES + ['INSTALL-Windows.bat', '启动工作台-Windows.bat', '关闭工作台-Windows.bat',
                           'requirements.txt', 'requirements-motor.txt', 'requirements-windows.txt', 'workbench.example.json']:
        shutil.copy2(ROOT / name, package / name)
    shutil.copytree(ROOT / 'dist', package / 'dist')
    shutil.copytree(ROOT / 'vendor/xige_foc', package / 'vendor/xige_foc',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'logs', '.venv'))
    scripts = package / 'scripts'
    scripts.mkdir()
    for name in ['workbench.py', 'web_server.py', 'lan_access.py', 'install_windows.py',
                 'windows_firewall.ps1', 'windows_shortcuts.ps1', 'verify_windows_package.py']:
        shutil.copy2(ROOT / 'scripts' / name, scripts / name)
    shutil.copy2(ROOT / 'docs/windows-package.txt', package / '使用说明.txt')
    # cmd.exe / Windows PowerShell 5.1 compatibility, independent of the build OS.
    for path in package.rglob('*.bat'):
        path.write_bytes(path.read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8'))
    version = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    files = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(package.rglob('*')) if path.is_file()}
    manifest = {'sourceCommit': version, 'python': PYTHON_VERSION, 'pythonSource': PYTHON_URL,
                'pythonSha256': PYTHON_SHA256, 'files': files,
                'wheels': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in wheels.glob('*.whl')}}
    (package / 'package-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    target = output / 'TorqueWorkbench-Windows-x64.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as result:
        for path in sorted(package.rglob('*')):
            if path.is_file():
                result.write(path, path.relative_to(output).as_posix())
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{checksum}  {target.name}\n', encoding='ascii')
    print(f'Built {target} ({target.stat().st_size} bytes)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/windows')
    parser.add_argument('--python-zip')
    args = parser.parse_args()
    build(args.output, args.python_zip)
