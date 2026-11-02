"""Build a relocatable Pages gateway and immutable per-application UI archives."""
import argparse
import html
import io
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateway.catalog import config, digest, engine_version


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as result:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            result.writestr(info, data)
    return output.getvalue()


def build(destination):
    source = ROOT / 'gateway'
    icon_bundle = ROOT / '.runtime/gateway-icon-bundle/icon-motion.js'
    if not icon_bundle.is_file():
        raise ValueError('Missing animated icon bundle; run npm run build first')
    destination = destination.resolve()
    if destination != ROOT / 'site' and not destination.is_relative_to(ROOT / '.runtime'):
        raise ValueError('Build output must be site/ or a directory within .runtime/')
    if destination.exists():
        if not (destination / '.nojekyll').is_file():
            raise ValueError('Refusing to replace an unrecognized build directory')
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ['styles.css', 'open.js', 'open.css', 'inntorque-logo.png']:
        shutil.copy2(source / name, destination / name)
    shutil.copy2(icon_bundle, destination / 'icon-motion.js')
    shutil.copytree(source / 'icons', destination / 'icons', dirs_exist_ok=True)
    version = digest(icon_bundle.read_bytes() + (source / 'home.js').read_bytes()
                     + (source / 'styles.css').read_bytes()
                     + b''.join(path.read_bytes() for path in sorted((source / 'icons').glob('*.svg'))))[:12]
    (destination / 'home.js').write_text((source / 'home.js').read_text(encoding='utf-8').replace('__ICON_VERSION__', version), encoding='utf-8')
    page = (source / 'index.html').read_text(encoding='utf-8')
    for asset in ['styles.css', 'home.js']:
        page = page.replace('./' + asset, './' + asset + '?v=' + version)
    (destination / 'index.html').write_text(page, encoding='utf-8')
    catalog = config()
    for app in catalog['apps']:
        if not (source / 'icons' / f'{app["id"]}.svg').is_file():
            raise ValueError('Missing application icon: ' + app['id'])
        ui = ROOT / ('dist' if app['id'] == 'dynamometer' else 'gateway/ui/' + app['id'])
        files = {path.relative_to(ui).as_posix(): path.read_bytes()
                 for path in sorted(ui.rglob('*')) if path.is_file()}
        if 'index.html' not in files:
            raise ValueError('Missing UI: ' + app['id'])
        metadata = {'id': app['id'], 'hostApi': app['hostApi'], 'engine': engine_version(app['id']),
                    'files': {name: digest(data) for name, data in files.items()}}
        app['version'] = digest(json.dumps(metadata, sort_keys=True).encode())[:16]
        metadata['version'] = app['version']
        files['ui.json'] = json.dumps(metadata, sort_keys=True).encode()
        bundle = archive(files)
        app.update(engine=metadata['engine'], sha256=digest(bundle), bytes=len(bundle),
                   bundle=f'releases/{app["id"]}/{app["version"]}.zip')
        target = destination / app['bundle']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bundle)
        launcher = destination / 'apps' / app['id']
        launcher.mkdir(parents=True, exist_ok=True)
        (launcher / 'index.html').write_text(f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>打开{html.escape(app['name'])} · Inntorque</title><link rel="stylesheet" href="../../open.css">
<script type="module" src="../../open.js"></script></head><body data-app="{app['id']}"><main>
<h1 id="message">正在连接本机服务…</h1><section id="help" hidden>
<p>USB-CAN 和串口设备接在这台测试电脑上。本机连接服务启动后，重新打开此 APP。</p>
<p>已安装：双击“启动测试总网关”。首次使用：<a href="../../downloads/Inntorque-Local-Services-Windows-x64.zip">下载 Windows 独立运行包</a>，或<a href="../../downloads/Inntorque-Local-Services-Source.zip">下载 Ubuntu / Python 运行包</a>。</p>
<p>切换或更新 APP 前，可在 <a href="{html.escape(catalog['serviceUrl'])}/">本机服务页面</a>正常关闭当前 APP。</p>
<p>浏览器询问本地网络访问时，请允许本站连接本机服务。</p><p id="detail"></p></section>
<a href="../../">返回总网关</a></main></body></html>''', encoding='utf-8')
    (destination / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (destination / '.nojekyll').touch()
    print(json.dumps({'directory': str(destination), 'apps': [{key: app[key] for key in ['id', 'version', 'bytes']} for app in catalog['apps']]}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'site')
    build(parser.parse_args().output)
