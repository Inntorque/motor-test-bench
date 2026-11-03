import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const generated = spawnSync(python,['-B',fileURLToPath(new URL('./generate_browser_metadata.py',import.meta.url))],
  {cwd:fileURLToPath(new URL('../',import.meta.url)),stdio:'inherit'});
if (generated.error) throw generated.error;
if (generated.status !== 0) throw new Error('Browser metadata generation failed');
