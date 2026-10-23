"""Portable host/package checks using imports and in-memory state only."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'vendor' / 'xige_foc'


class HostRuntimeTests(unittest.TestCase):
    def test_vendor_inventory_matches_source_hashes(self):
        source = json.loads((HOST / 'SOURCE.json').read_text(encoding='utf-8'))
        actual = {p.name for p in (HOST / 'scripts').glob('*.py')}
        self.assertEqual(set(source['files']), actual)
        self.assertIn('max_output.py', actual)
        for name, digest in source['files'].items():
            with self.subTest(module=name):
                self.assertEqual(hashlib.sha256((HOST / 'scripts' / name).read_bytes()).hexdigest(), digest)

    def test_host_imports_from_portable_directory_without_device_io(self):
        code = '''
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import foc_web, run_recorder, max_output, zqwl
assert Path(foc_web.__file__).parent == Path(sys.argv[1])
assert foc_web.CONTROL_MODE_MAX_WEB == 7
assert foc_web.CONTROL_FIELDS['max_bus_a'][0] == 13
assert foc_web.CONTROL_FIELDS['max_id_min_a'][0] == 14
assert max_output.MIN_VERSION == 0x0A3C
assert run_recorder.MODE_NAMES[7] == 'maximum_output'
link = foc_web.Link(None, 0)
assert link.bus is None and link.max_output_snapshot is None
assert callable(max_output.monitor) and callable(max_output.export_trace)
print('Portable host imports and maximum-output interfaces passed')
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-c', code, str(HOST / 'scripts')],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
