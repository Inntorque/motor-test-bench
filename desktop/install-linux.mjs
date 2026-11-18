import {cp, mkdir, writeFile, chmod} from 'node:fs/promises';
import {homedir} from 'node:os';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';

if (process.platform !== 'linux') throw new Error('此安装入口仅用于 Linux');
const root = dirname(fileURLToPath(import.meta.url));
const source = resolve(root, `../artifacts/InntorqueBench-linux-${process.arch}`);
const destination = resolve(homedir(), '.local/opt/inntorque-bench');
await mkdir(destination, {recursive: true});
await cp(source, destination, {recursive: true});
const sandbox = resolve(destination, 'chrome-sandbox');
execFileSync('sudo', ['-n', 'chown', 'root:root', sandbox], {stdio: 'inherit'});
execFileSync('sudo', ['-n', 'chmod', '4755', sandbox], {stdio: 'inherit'});
const shortcut = `[Desktop Entry]\nType=Application\nName=Inntorque 测功机\nComment=电机与制动器加载试验上位机\nExec="${destination}/启动测功机.sh"\nIcon=${destination}/resources/app/icon.png\nTerminal=false\nCategories=Science;Engineering;\nStartupNotify=true\nStartupWMClass=InntorqueBench\n`;
const applications = resolve(homedir(), '.local/share/applications');
const desktop = execFileSync('xdg-user-dir', ['DESKTOP'], {encoding: 'utf8'}).trim();
await mkdir(applications, {recursive: true});
for (const file of [resolve(applications, 'inntorque-bench.desktop'), resolve(desktop, 'Inntorque 测功机.desktop')]) {
  await writeFile(file, shortcut);
  await chmod(file, 0o755);
}
execFileSync('gio', ['set', resolve(desktop, 'Inntorque 测功机.desktop'), 'metadata::trusted', 'true']);
console.log(`已安装：${destination}\n桌面快捷方式：${desktop}/Inntorque 测功机.desktop`);
