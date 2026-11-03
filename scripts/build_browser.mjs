import {build} from 'vite';
import {spawnSync} from 'node:child_process';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)),'..');
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const generated = spawnSync(python,['-B',resolve(root,'scripts/generate_browser_metadata.py')],{cwd:root,stdio:'inherit'});
if (generated.error) throw generated.error;
if (generated.status !== 0) throw new Error('Browser metadata generation failed');
for (const [entry,format] of [['bootstrap','iife'],['worker','es']]) {
  await build({configFile:false,root,publicDir:false,build:{outDir:resolve(root,'.runtime/browser-native-bundle'),emptyOutDir:entry === 'bootstrap',minify:true,
    lib:{entry:resolve(root,`browser/${entry}.js`),name:'InntorqueBrowser',formats:[format],fileName:() => `${entry}.js`}}});
}
await build({configFile:false,root:resolve(root,'gateway/ui/bootloader'),base:'./',
  build:{outDir:resolve(root,'.runtime/bootloader-ui'),emptyOutDir:true}});
