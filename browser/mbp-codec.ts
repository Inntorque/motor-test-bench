import schema from '../protocol/schema.json' with {type: 'json'};
import {bytes, concat, pack, view, now, sleep} from './bytes.ts';
export {schema};
export const O = Object.fromEntries(Object.entries(schema.objects).map(([name, spec]) => [name, spec.id]));
export const OP = schema.opcodes;
export const CAN = schema.frames;
const integerFormats = {1: 'B', 2: 'H', 3: 'I', 4: 'Q', 5: 'i', 6: 'q', 7: 'B'};
export function tlv(id, type, value) {
  if (!Number.isInteger(id) || id < 0 || id > 65535 || !Number.isInteger(type) || type < 1 || type > 10) throw new Error('invalid TLV');
  if (type === 7 && value !== 0 && value !== 1 && value !== false && value !== true) throw new Error('invalid bool');
  if (integerFormats[type] && type !== 7) {
    const limits = {1:[0n,255n],2:[0n,65535n],3:[0n,4294967295n],4:[0n,18446744073709551615n],5:[-2147483648n,2147483647n],6:[-9223372036854775808n,9223372036854775807n]};
    const integer = BigInt(value);
    if (integer < limits[type][0] || integer > limits[type][1]) throw new Error('integer range');
  }
  const data = integerFormats[type] ? pack(integerFormats[type], value)
    : type === 8 ? new TextEncoder().encode(value) : bytes(value);
  return concat(pack('HBH', id, type, data.length), data);
}
export function objects(values) {
  return concat(...Object.entries(values).map(([id, value]) => {
    const spec = Object.values(schema.objects).find(object => object.id === Number(id));
    if (!spec) throw new Error(`Unknown object ${id}`);
    return tlv(Number(id), schema.types[spec.type], value);
  }));
}
export function decodeTlvs(body) {
  const result: Record<number, [number, unknown]> = {}, source = view(body);
  let offset = 0;
  while (offset < body.length) {
    if (body.length - offset < 5) throw new Error('truncated TLV');
    const id = source.getUint16(offset, true), type = body[offset + 2], length = source.getUint16(offset + 3, true);
    offset += 5;
    if (Object.hasOwn(result, id) || offset + length > body.length || type < 1 || type > 10) throw new Error('invalid TLV');
    const raw = body.slice(offset, offset + length), data = view(raw);
    offset += length;
    let value: unknown = raw;
    if (integerFormats[type]) {
      const expected = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8, 7: 1}[type];
      if (length !== expected) throw new Error('integer size');
      value = type === 1 || type === 7 ? data.getUint8(0) : type === 2 ? data.getUint16(0, true)
        : type === 3 ? data.getUint32(0, true) : type === 4 ? data.getBigUint64(0, true)
        : type === 5 ? data.getInt32(0, true) : data.getBigInt64(0, true);
      if (type === 7 && value !== 0 && value !== 1) throw new Error('invalid bool');
    } else if (type === 8) value = new TextDecoder('utf-8', {fatal: true}).decode(raw);
    else if (type === 10) value = decodeTlvs(raw);
    result[id] = [type, value];
  }
  return result;
}
export const values = body => Object.fromEntries(Object.entries(decodeTlvs(body)).map(([id, [, value]]) => [id, value]));
export function packet(op, request, body = new Uint8Array(), flags = 0) {
  if (body.length > 1008) throw new Error('PDU too large');
  return concat(new TextEncoder().encode('MTBP'), pack('BBBBIHH', ...schema.wire, op, flags, request, body.length, 0), body);
}
export function unpack(data) {
  if (data.length < 16) throw new Error('short header');
  const source = view(data);
  if (new TextDecoder().decode(data.slice(0, 4)) !== 'MTBP' || data[7] & ~3 || source.getUint16(14, true) || source.getUint16(12, true) !== data.length - 16) throw new Error('invalid header');
  return {major: data[4], minor: data[5], opcode: data[6], flags: data[7], request: source.getUint32(8, true), body: data.slice(16)};
}
export class IsoTp {
  declare cancelled;
  declare deliver;
  declare errors: number;
  declare flow: { status: number; block: number; gap: number; } | null | undefined;
  declare maximum: number;
  declare rx;
  declare rxBlock: number | undefined;
  declare rxDeadline: number | undefined;
  declare rxSize: number | undefined;
  declare rxSn;
  declare send;

  constructor(send, deliver, maximum = 1024) {
    this.send = send; this.deliver = deliver; this.maximum = maximum;
    this.reset(); this.errors = 0;
  }
  reset() { this.rx = new Uint8Array(); this.rxSize = 0; this.rxSn = 1; this.rxBlock = 0; this.flow = null; this.cancelled = (this.cancelled || 0) + 1; }
  async start(data) {
    if (!data.length || data.length > this.maximum) throw new Error('ISO-TP size');
    const generation = this.cancelled;
    if (data.length <= 7) return this.send(concat(Uint8Array.of(data.length), data, new Uint8Array(7 - data.length)));
    this.flow = null;
    await this.send(concat(Uint8Array.of(0x10 | data.length >> 8, data.length & 255), data.slice(0, 6)));
    let offset = 6, sequence = 1;
    while (offset < data.length) {
      const deadline = now() + 1;
      let waits = 0;
      while (!this.flow || this.flow.status === 1) {
        if (this.flow?.status === 1) { if (++waits > 3) throw new Error('ISO-TP WAIT limit'); this.flow = null; }
        if (generation !== this.cancelled) throw new Error('ISO-TP cancelled');
        if (now() > deadline) throw new Error('ISO-TP flow control timeout');
        await sleep(1);
      }
      const {status, block, gap} = this.flow; this.flow = null;
      if (status !== 0 || gap > 127 && (gap < 0xf1 || gap > 0xf9)) throw new Error('ISO-TP flow control rejected');
      const interval = gap <= 127 ? gap : (gap - 0xf0) / 10;
      for (let i = 0; i < (block || 65535) && offset < data.length; i++) {
        if (generation !== this.cancelled) throw new Error('ISO-TP cancelled');
        const chunk = data.slice(offset, offset + 7);
        await this.send(concat(Uint8Array.of(0x20 | sequence), chunk, new Uint8Array(7 - chunk.length)));
        offset += chunk.length; sequence = (sequence + 1) & 15;
        if (interval && offset < data.length) await sleep(interval);
      }
    }
  }
  receive(data) {
    if (data.length !== 8) { this.errors++; return; }
    const kind = data[0] >> 4;
    if (kind === 0) {
      const length = data[0] & 15;
      if (length < 1 || length > 7) { this.errors++; return; }
      this.rxSize = 0; this.deliver(data.slice(1, length + 1));
    } else if (kind === 1) {
      const length = ((data[0] & 15) << 8) | data[1];
      if (length < 8 || length > this.maximum) { this.errors++; void this.send(Uint8Array.of(0x32, 0, 0, 0, 0, 0, 0, 0)); return; }
      this.rx = data.slice(2); this.rxSize = length; this.rxSn = 1; this.rxBlock = 0; this.rxDeadline = now() + 1;
      void this.send(Uint8Array.of(0x30, 8, 1, 0, 0, 0, 0, 0));
    } else if (kind === 2) {
      if (!this.rxSize || now() > this.rxDeadline || (data[0] & 15) !== this.rxSn) { this.rxSize = 0; this.errors++; return; }
      this.rx = concat(this.rx, data.slice(1, 1 + Math.min(7, this.rxSize - this.rx.length)));
      this.rxSn = (this.rxSn + 1) & 15; this.rxBlock++; this.rxDeadline = now() + 1;
      if (this.rx.length === this.rxSize) { this.rxSize = 0; this.deliver(this.rx); }
      else if (this.rxBlock === 8) { this.rxBlock = 0; void this.send(Uint8Array.of(0x30, 8, 1, 0, 0, 0, 0, 0)); }
    } else if (kind === 3) this.flow = {status: data[0] & 15, block: data[1], gap: data[2]};
  }
}
export class SampleAssembler {
  fields = {}; flags = {}; sequence = null; last = null; dropped = 0; duplicates = 0;
  receive(data) {
    if (data.length !== 8) throw new Error('sample DLC');
    const source = view(data), seq = source.getUint16(0, true), field = data[2], flags = data[3], value = source.getUint32(4, true);
    if (flags & ~7) throw new Error('sample flags');
    if (seq !== this.sequence) { if (Object.keys(this.fields).length) this.dropped++; this.sequence = seq; this.fields = {}; this.flags = {}; }
    if (field === 254) {
      if (this.last === seq) { this.duplicates++; return null; }
      const mask = Object.keys(this.fields).reduce((result, id) => (result | 1 << Number(id)) >>> 0, 0);
      if (value !== mask || (value & 3) !== 3 || Boolean(value & 64) !== Boolean(value & 128)) { this.dropped++; this.fields = {}; return null; }
      const result = {seq, fields: this.fields, flags: this.flags}; this.fields = {}; this.last = seq; return result;
    }
    if (field >= 32) throw new Error('sample field');
    if (Object.hasOwn(this.fields, field)) { this.duplicates++; this.fields = {}; this.sequence = null; return null; }
    this.fields[field] = value; this.flags[field] = flags;
    return null;
  }
}
