import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';

const root = fileURLToPath(new URL('../', import.meta.url));
const local = fileURLToPath(new URL(process.platform === 'win32'
  ? '../.venv/Scripts/python.exe' : '../.venv/bin/python', import.meta.url));
const candidates = existsSync(local) ? [[local]] : process.platform === 'win32'
  ? [['py', '-3'], ['python']] : [['python3'], ['python']];
const python = candidates.find(([command, ...args]) =>
  spawnSync(command, [...args, '-c', 'import sys; sys.exit(sys.version_info < (3, 10))'], {stdio:'ignore'}).status === 0);
if (!python) {
  console.error('需要 Python 3.10+；请先安装 Python 并添加到 PATH。');
  process.exit(1);
}
const child = spawn(python[0], [...python.slice(1), 'scripts/workbench.py', process.argv[2] || 'start'],
  {cwd:root, stdio:'inherit'});
child.on('error', error => { console.error(error.message); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
