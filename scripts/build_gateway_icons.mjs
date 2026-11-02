import {build} from 'vite';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {readFile, writeFile} from 'node:fs/promises';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
await build({
  configFile: false,
  root,
  publicDir: false,
  build: {
    outDir: resolve(root, '.runtime/gateway-icon-bundle'),
    emptyOutDir: true,
    minify: true,
    lib: {
      entry: resolve(root, 'gateway/icon-motion.js'),
      formats: ['es'],
      fileName: () => 'icon-motion.js',
    },
  },
});
const bundle = resolve(root, '.runtime/gateway-icon-bundle/icon-motion.js');
const license = await readFile(resolve(root, 'node_modules/three/LICENSE'), 'utf8');
await writeFile(bundle, `/* Three.js — MIT License\n${license}*/\n${await readFile(bundle, 'utf8')}`);
