import {build} from 'vite';
import './generate_browser_metadata.mts';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)),'..');
for (const [entry,format] of [['bootstrap','iife'],['worker','es']]) {
  await build({configFile:false,root,publicDir:false,build:{outDir:resolve(root,'.runtime/browser-native-bundle'),emptyOutDir:entry === 'bootstrap',minify:true,
    lib:{entry:resolve(root,`browser/${entry}.ts`),name:'InntorqueBrowser',formats:[format],fileName:() => `${entry}.js`}}});
}
await build({configFile:false,root:resolve(root,'gateway/ui/bootloader'),base:'./',
  build:{outDir:resolve(root,'.runtime/bootloader-ui'),emptyOutDir:true}});
