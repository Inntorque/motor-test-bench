"""Import explicitly selected host scripts without changing their source projects."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def import_host(source_root, app_id):
    source = source_root / 'scripts'
    destination = ROOT / 'gateway/hosts' / app_id / 'scripts'
    destination.mkdir(parents=True, exist_ok=True)
    names = ['foc_web.py', 'web_client.py', 'zqwl.py', 'usb_gs.py', 'ota_host.py', 'ota_onekey.py']
    if app_id == 'sg-6010hb':
        names += ['run_recorder.py', 'max_output.py']
    manifest, imported = {}, {}
    for name in names:
        raw = (source / name).read_bytes()
        text = raw.decode('utf-8-sig').replace('\r\n', '\n')
        if name == 'foc_web.py':
            tree = ast.parse(text)
            page = next(node for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'INDEX_HTML' for target in node.targets))
            html = ast.literal_eval(page.value)
            ui = ROOT / 'gateway/ui' / app_id
            ui.mkdir(parents=True, exist_ok=True)
            (ui / 'index.html').write_text(html, encoding='utf-8')
            lines = text.splitlines(keepends=True)
            text = ''.join(lines[:page.lineno - 1]) + "INDEX_HTML = ''\n" + ''.join(lines[page.end_lineno:])
            transformed = ast.parse(text)
            page.value = ast.Constant('')
            if ast.dump(tree) != ast.dump(transformed):
                raise ValueError('Host logic changed while extracting HTML')
        (destination / name).write_text(text, encoding='utf-8')
        manifest[name] = hashlib.sha256(raw).hexdigest()
        imported[name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
    (destination.parent / 'source-manifest.json').write_text(
        json.dumps({'sourceProject': source_root.name, 'files': manifest, 'importedFiles': imported,
                    'uiSha256': hashlib.sha256((ROOT / 'gateway/ui' / app_id / 'index.html').read_bytes()).hexdigest()}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sg-root', type=Path, required=True)
    parser.add_argument('--rm-root', type=Path, required=True)
    args = parser.parse_args()
    import_host(args.sg_root, 'sg-6010hb')
    import_host(args.rm_root, 'rm-7010')
