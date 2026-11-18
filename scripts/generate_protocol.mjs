import {readFile,writeFile} from 'node:fs/promises';
const schema=JSON.parse(await readFile(new URL('../protocol/schema.json',import.meta.url),'utf8'));
const objectIds=Object.fromEntries(Object.entries(schema.objects).map(([name,spec])=>[name,spec.id]));
const groups=[['OP',schema.opcodes],['CAN',schema.frames],['TYPE',schema.types],['O',objectIds],...Object.entries(schema.enums)];
let wire='/* Generated from protocol/schema.json. Do not edit by hand. */\n#ifndef INNTORQUE_MBP_IDS_H\n#define INNTORQUE_MBP_IDS_H\n';
let device='#ifndef MB_PROTOCOL_H\n#define MB_PROTOCOL_H\n/* Generated from protocol/schema.json. */\n';
wire+=`#define MBP_WIRE_MAJOR ${schema.wire[0]}\n#define MBP_WIRE_MINOR ${schema.wire[1]}\n`;
device+=`#define MB_MAJOR ${schema.wire[0]}u\n#define MB_MINOR ${schema.wire[1]}u\n`;
for(const [prefix,entries] of groups){
  for(const [name,value] of Object.entries(entries)){
    const constant=`${name.toUpperCase()} ${value}u\n`;
    wire+=`#define MBP_${prefix}_${constant}`;
    device+=`#define MB_${prefix==='O'?'OBJ':prefix}_${constant}`;
  }
}
for(const [number,name] of Object.entries(schema.sample_fields))device+=`#define MB_FIELD_${name.toUpperCase()} ${number}u\n`;
device+=`#define MB_SDK_VERSION "${schema.sdk}"\n`;
for(const [file,body] of [['mbp_ids.h',wire],['device/mb_protocol.h',device]]){
  const path=new URL('../protocol/sdk/'+file,import.meta.url),header=body+'#endif\n';
  if(process.argv.includes('--check')){
    if(await readFile(path,'utf8')!==header)throw new Error(file+' 与 schema.json 不一致');
  }else await writeFile(path,header);
}
