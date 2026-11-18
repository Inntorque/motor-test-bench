import {build} from 'vite';
import {cp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createHash} from 'node:crypto';

const root = resolve(import.meta.dirname, '..');
const site = resolve(root, 'site');
await rm(site, {recursive:true, force:true});
await mkdir(site, {recursive:true});
for (const [entry,format] of [['bootstrap','iife'],['worker','es']]) {
  await build({configFile:false,root,publicDir:false,build:{outDir:resolve(site,'browser'),emptyOutDir:entry==='bootstrap',minify:true,
    lib:{entry:resolve(root,`browser/${entry}.ts`),name:'InntorqueBrowser',formats:[format],fileName:()=>`${entry}.js`}}});
}
await build({configFile:false,root,publicDir:false,define:{'process.env.NODE_ENV':'"production"'},build:{
  outDir:resolve(site,'export'),emptyOutDir:true,minify:true,
  lib:{entry:resolve(root,'desktop/characteristic-export.tsx'),formats:['es'],fileName:()=>'characteristic.mjs'},
}});
const pageRoot = resolve(site, 'apps/dynamometer');
await cp(resolve(root,'dist'), pageRoot, {recursive:true});
const version = createHash('sha256');
for (const name of ['bootstrap','worker']) version.update(await readFile(resolve(site,`browser/${name}.js`)));
const entry = resolve(pageRoot, 'index.html');
const html = await readFile(entry, 'utf8');
await writeFile(entry, html.replace('<head>', `<head>\n<script src="../../browser/bootstrap.js?v=${version.digest('hex').slice(0,16)}" data-app="dynamometer"></script>`));
console.log(`Desktop resources: ${site}`);
