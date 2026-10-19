"""Portable paths shared by the launcher and device bridge."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def local_config():
    path = ROOT / 'workbench.local.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def xige_root(config=None):
    config = local_config() if config is None else config
    configured = os.environ.get('MOTORBENCH_XIGE_ROOT') or config.get('xigeRoot')
    if configured:
        path = Path(configured).expanduser()
        return (path if path.is_absolute() else ROOT / path).resolve()
    # Supports a portable sibling checkout and the existing Desktop layout.
    for base in (ROOT / 'vendor', ROOT.parent, ROOT.parent.parent, Path.home() / 'Desktop'):
        for path in (base / 'xige_foc', base / 'xige_foc' / 'xige_foc'):
            if (path / 'scripts' / 'foc_web.py').is_file():
                return path.resolve()
    return (ROOT.parent / 'xige_foc').resolve()


def motor_arguments(config=None):
    config = local_config() if config is None else config
    motor = config.get('motor', {})
    args = []
    for key in ('interface', 'channel', 'baud', 'node'):
        value = os.environ.get('MOTORBENCH_CAN_' + key.upper(), motor.get(key))
        if value is not None and str(value).strip():
            args.extend(['--' + key, str(value)])
    return args
