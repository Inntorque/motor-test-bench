import {build} from 'vite';
import './generate_browser_metadata.mts';
import {resolve,dirname} from 'node:path';
import {copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)),'..');
for (const [entry,format] of [['bootstrap','iife'],['worker','es']]) {
  await build({configFile:false,root,publicDir:false,build:{outDir:resolve(root,'.runtime/browser-native-bundle'),emptyOutDir:entry === 'bootstrap',minify:true,
    lib:{entry:resolve(root,`browser/${entry}.ts`),name:'InntorqueBrowser',formats:[format],fileName:() => `${entry}.js`}}});
}
await build({configFile:false,root,publicDir:false,
  build:{outDir:resolve(root,'.runtime/foc-scene'),emptyOutDir:true,
    lib:{entry:resolve(root,'gateway/ui/foc-shared/scene.js'),formats:['es'],fileName:() => 'scene.js'}}});
await copyFile(resolve(root,'node_modules/three/LICENSE'),resolve(root,'.runtime/foc-scene/THREE-LICENSE.txt'));
await build({configFile:false,root:resolve(root,'gateway/ui/bootloader'),base:'./',
  build:{outDir:resolve(root,'.runtime/bootloader-ui'),emptyOutDir:true}});
