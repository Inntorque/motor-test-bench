"""Read existing dashboard descriptors without opening CAN or serial devices."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APPS = ('sg-6010hb', 'rm-7010')


def extract(app):
    source = ROOT / 'gateway/hosts' / app / 'scripts/foc_web.py'
    sys.path.insert(0, str(source.parent))
    spec = importlib.util.spec_from_file_location('foc_web', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    link = module.Link(None, 0)
    return {'constants': {key: value for key, value in vars(module).items()
                          if key.isupper() and isinstance(value, (int, float, str))},
            'paramTable': module.PARAM_TABLE, 'profiles': module.MOTOR_PROFILES,
            'profileDefault': module.MOTOR_PROFILE_DEFAULT, 'controlFields': module.CONTROL_FIELDS,
            'otaStateNames': module.OTA_STATE_NAMES, 'motorStateNames': module.MOTOR_STATE_NAMES,
            'mutatingRoutes': sorted(module.MUTATING_ROUTES), 'emptyStatus': link.status()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', choices=APPS)
    args = parser.parse_args()
    if args.app:
        print(json.dumps(extract(args.app), ensure_ascii=False))
    else:
        data = {app: json.loads(subprocess.check_output(
            [sys.executable, '-B', str(Path(__file__).resolve()), '--app', app], text=True, encoding='utf-8'))
                for app in APPS}
        output = ROOT / '.runtime/browser-native-data/legacy.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(output)
