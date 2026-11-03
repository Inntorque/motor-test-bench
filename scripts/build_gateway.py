"""Build standalone browser applications and reference UI archives for Pages."""
import argparse
import html
import io
import json
import re
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
    native = ROOT / '.runtime/browser-native-bundle'
    if not (native / 'worker.js').is_file():
        raise ValueError('Missing browser runtime; run npm run build first')
    native_version = digest(b''.join(path.read_bytes() for path in sorted(native.glob('*.js'))))[:16]
    legacy = json.loads((ROOT / '.runtime/browser-native-data/legacy.json').read_text(encoding='utf-8'))
    shutil.copytree(native, destination / 'browser')
    for name in ['styles.css', 'inntorque-logo.png']:
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
        page_root = destination / 'apps' / app['id']
        shutil.copytree(ui, page_root)
        index = page_root / 'index.html'
        page = index.read_text(encoding='utf-8')
        if app['id'] != 'dynamometer':
            data = legacy[app['id']]
            heartbeat_start = page.index('  try {\n    var aliveSrc = ')
            heartbeat_end = page.index('\n  pollControl();', heartbeat_start)
            page = page[:heartbeat_start] + page[heartbeat_end:]
            page = page.replace('__CONTROL_TOKEN__', 'browser-direct').replace('__COMMAND_EPOCH__', '0')
            page = page.replace('__MUTATING_ROUTES__', json.dumps(data['mutatingRoutes']))
            tuning = [{key: item[key] for key in ('id', 'default', 'min', 'max')}
                      for item in data['emptyStatus']['params']]
            page = page.replace('__TUNE_CATALOG__', json.dumps(tuning))
            page = page.replace('href="/guide?name=project"', 'href="./guide/"')
            page = page.replace('关闭服务', '断开设备').replace('停止输出并关闭后台网页服务', '停止输出并释放浏览器设备连接')
            page = page.replace('网页服务已关闭，可关闭此标签页。重新使用请双击启动网页。', '设备已断开，可关闭此标签页。重新使用请刷新本网页。')
            page = page.replace('一键编译并 CAN-OTA', '固件编译请使用维护工程')
            page = page.replace('编译当前工程并升级设备', '浏览器不运行本机固件工具链')
            page = page.replace('执行前会先发送停机命令并释放网页的 CAN 适配器；Makefile 会递增固件版本、编译 A/B 槽并更新设备。', '固件编译和升级由独立维护工程完成；本网页仅直接连接设备进行控制、读取和测试。')
            page = page.replace('$("btn_ota_onekey").disabled = otaBusy;', '$("btn_ota_onekey").disabled = true;')
        script = f'<script src="../../browser/bootstrap.js?v={native_version}" data-app="{app["id"]}"></script>'
        page, count = re.subn(r'(<head\b[^>]*>)', lambda match: match[1]+'\n'+script, page, count=1, flags=re.I)
        if count != 1 or re.search(r'__(?:CONTROL_TOKEN|COMMAND_EPOCH|MUTATING_ROUTES|TUNE_CATALOG)__', page):
            raise ValueError('Incomplete standalone browser page: ' + app['id'])
        index.write_text(page, encoding='utf-8')
        app.update(runtime='browser', entry=f'apps/{app["id"]}/',
                   browserVersion=digest(page.encode()+native_version.encode())[:16])
        app.pop('port', None)
        guide = page_root / 'guide'
        guide.mkdir(exist_ok=True)
        guide.joinpath('index.html').write_text('<meta charset="utf-8"><pre style="white-space:pre-wrap">'+
            html.escape((ROOT / 'docs/gateway-deployment.md').read_text(encoding='utf-8'))+'</pre>', encoding='utf-8')
    catalog.pop('serviceUrl', None)
    catalog.update(runtime='browser', browserRuntimeVersion=native_version)
    (destination / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (destination / '.nojekyll').touch()
    print(json.dumps({'directory': str(destination), 'apps': [{key: app[key] for key in ['id', 'version', 'bytes']} for app in catalog['apps']]}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'site')
    build(parser.parse_args().output)
