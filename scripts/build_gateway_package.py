"""Package local services with the existing portable Windows runtime or Python sources."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_windows_package import MODULES, build as build_windows

NAMES = ['启动测试总网关-Windows.bat', '关闭测试总网关-Windows.bat',
         '启动测试总网关-Ubuntu.sh', '关闭测试总网关-Ubuntu.sh', 'gateway.local.example.json']


def common(package):
    shutil.copytree(ROOT / 'gateway', package / 'gateway', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in NAMES:
        shutil.copy2(ROOT / name, package / name)
    shutil.copy2(ROOT / 'scripts/gateway.py', package / 'scripts/gateway.py')
    (package / 'docs').mkdir(exist_ok=True)
    for name in ['gateway-deployment.md', 'gateway-icons.md']:
        shutil.copy2(ROOT / 'docs' / name, package / 'docs' / name)
    files = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(package.rglob('*')) if path.is_file() and path.name != 'package-manifest.json'}
    manifest = package / 'package-manifest.json'
    data = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    data.update(files=files, package='inntorque-local-services', hostApi=1)
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build(output, windows=False):
    output.mkdir(parents=True, exist_ok=True)
    (ROOT / '.runtime').mkdir(exist_ok=True)
    name = 'Inntorque-Local-Services-Windows-x64' if windows else 'Inntorque-Local-Services-Source'
    with tempfile.TemporaryDirectory(dir=ROOT / '.runtime') as temporary:
        work = Path(temporary)
        if windows:
            build_windows(work, ROOT / '.runtime/gateway-python-embed.zip')
            package = work / 'TorqueWorkbench-Windows-x64'
        else:
            package = work / name
            package.mkdir()
            for filename in MODULES + ['requirements.txt', 'requirements-motor.txt', 'workbench.example.json']:
                shutil.copy2(ROOT / filename, package / filename)
            for directory in ['workbench', 'protocol', 'profiles', 'dist']:
                shutil.copytree(ROOT / directory, package / directory, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (package / 'scripts').mkdir()
            for filename in ['web_server.py', 'instance_lock.py', 'lan_access.py']:
                shutil.copy2(ROOT / 'scripts' / filename, package / 'scripts' / filename)
        common(package)
        target = output / (name + '.zip')
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package.rglob('*')):
                if path.is_file():
                    info = zipfile.ZipInfo((Path(name) / path.relative_to(package)).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                    info.external_attr = (0o100000 | stat.S_IMODE(path.stat().st_mode)) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        (output / (name + '.sha256')).write_text(checksum + '  ' + target.name + '\n', encoding='ascii')
        print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'site/downloads')
    parser.add_argument('--windows', action='store_true')
    args = parser.parse_args()
    build(args.output, args.windows)
