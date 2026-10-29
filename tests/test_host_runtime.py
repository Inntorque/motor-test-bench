"""Portable host/package checks using imports and in-memory state only."""
import ast
import json
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HostRuntimeTests(unittest.TestCase):
    def test_config_migration_keeps_old_node_only_in_backup(self):
        import workbench_config

        original = {'motor': {'node': 0, 'channel': '/dev/ttyACM0'}, 'xigeRoot': '/old/project'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'workbench.local.json'
            path.write_text(json.dumps(original), encoding='utf-8')
            with patch.object(workbench_config, 'ROOT', root):
                workbench_config.migrate_config()
                migrated = workbench_config.local_config()
                workbench_config.migrate_config()
            self.assertEqual(migrated, {'schema': 2, 'motor': {'nativeNode': 1, 'channel': '/dev/ttyACM0'}})
            backups = list((root / '.runtime/config-migration').glob('*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8')), original)

    def test_host_imports_without_legacy_server_or_device_io(self):
        code="from workbench.service import Handler; from workbench.devices.motor import MotorDevice; m=MotorDevice(); assert m.bus is None; print('native host imports')"
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_windows_package_includes_local_module_dependencies(self):
        tree = ast.parse((ROOT / 'scripts' / 'build_windows_package.py').read_text(encoding='utf-8'))
        modules = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'MODULES' for target in node.targets))
        available = {p.stem for p in ROOT.glob('*.py')}
        included = {Path(name).stem for name in modules}
        for name in modules:
            source = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            dependencies = {node.module.split('.')[0] for node in ast.walk(source)
                            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
            for node in ast.walk(source):
                if isinstance(node, ast.Import):
                    dependencies.update(item.name.split('.')[0] for item in node.names)
            with self.subTest(module=name):
                self.assertFalse((dependencies & available) - included)


if __name__ == '__main__':
    unittest.main()
