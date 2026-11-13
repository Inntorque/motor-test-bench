"""Shared, deterministic application metadata for builds and local services."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def config(root=ROOT):
    data = json.loads((root / 'gateway/apps.json').read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('Unsupported application catalog')
    ids, ports = set(), set()
    for app in data['apps']:
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', app['id']):
            raise ValueError('Invalid application ID')
        if app['id'] in ids:
            raise ValueError('Duplicate application ID')
        ids.add(app['id'])
        if app.get('browserOnly'):
            continue
        if app['port'] in ports or not 1024 <= app['port'] <= 65535:
            raise ValueError('Duplicate application ID or invalid port')
        ports.add(app['port'])
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def engine_files(app_id, root=ROOT):
    if app_id.startswith('foc-'):
        ui = root / 'gateway/ui'
        files = [path for folder in (ui / app_id, ui / 'foc-shared') for path in folder.rglob('*') if path.is_file()]
        files.extend(root / 'browser' / name for name in ('foc.ts', 'foc-protocol.ts'))
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(files)}
    if app_id == 'bootloader':
        base = root / 'gateway/ui/bootloader'
        return {path.relative_to(base).as_posix(): path.read_bytes() for path in sorted(base.rglob('*')) if path.is_file()}
    if app_id != 'dynamometer':
        base = root / 'gateway/hosts' / app_id
        return {path.relative_to(base).as_posix(): path.read_bytes()
                for path in sorted(base.rglob('*.py')) if '__pycache__' not in path.parts}
    files = list(root.glob('*.py'))
    for name in ['workbench', 'protocol']:
        files.extend((root / name).rglob('*.py'))
    files.extend(root / 'scripts' / name for name in ['web_server.py', 'lan_access.py', 'instance_lock.py'])
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(files) if '__pycache__' not in path.parts}


def engine_version(app_id, root=ROOT):
    files = engine_files(app_id, root)
    if not files:
        raise ValueError(f'Missing local engine: {app_id}')
    return digest(json.dumps({name: digest(data) for name, data in files.items()}, sort_keys=True).encode())
