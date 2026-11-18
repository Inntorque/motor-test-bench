import {execFileSync} from 'node:child_process';
import {readFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
const root=resolve(import.meta.dirname,'..'),out=resolve(root,'.runtime/protocol-validation');await mkdir(out,{recursive:true});
execFileSync(process.execPath,[resolve(root,'scripts/generate_protocol.mjs'),'--check'],{stdio:'inherit'});
const exe=resolve(out,process.platform==='win32'?'wire-test.exe':'wire-test');
execFileSync(process.env.CC||'cc',['-std=c99','-Wall','-Wextra','-Werror','-Iprotocol/sdk','protocol/sdk/mbp_wire.c','protocol/sdk/test_wire.c','-o',exe],{cwd:root,stdio:'inherit'});
const output=execFileSync(exe,{encoding:'utf8'}).trim().split(/\r?\n/);
const golden=JSON.parse(await readFile(resolve(root,'protocol/golden.json'),'utf8'));
if(JSON.stringify(output)!==JSON.stringify([golden.identify_request,golden.stop,golden.iq_negative_1234ma]))throw new Error('C SDK 与标准报文不一致');
console.log('C SDK golden vectors and bounds checks passed.');
const simulator=resolve(out,process.platform==='win32'?'sdk-sim.exe':'sdk-sim');
execFileSync(process.env.CC||'cc',['-std=c11','-Wall','-Wextra','-Werror','-Iprotocol/sdk/device',
  'protocol/sdk/device/mb_sdk.c','protocol/examples/simulator.c','tests/fixtures/sdk-stdio.c','-o',simulator],{cwd:root,stdio:'inherit'});
execFileSync(process.execPath,['tests/sdk-integration.mjs',simulator],{cwd:root,stdio:'inherit',timeout:30000});
