export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export const now = () => performance.now() / 1000;
export const utc = () => Date.now() / 1000;
export const concat = (...parts) => {
  const result = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
  let offset = 0;
  for (const part of parts) { result.set(part, offset); offset += part.length; }
  return result;
};
export const bytes = value => value instanceof Uint8Array ? value : new Uint8Array(value);
export const hex = value => [...value].map(n => n.toString(16).padStart(2, '0')).join('');
export const view = value => new DataView(value.buffer, value.byteOffset, value.byteLength);
export const random32 = () => crypto.getRandomValues(new Uint32Array(1))[0] || 1;
export const random64 = () => (BigInt(random32()) << 32n) | BigInt(random32());
export const jsonValue = value => JSON.parse(JSON.stringify(value, (_, item) =>
  typeof item === 'bigint' ? item.toString() : item instanceof Uint8Array ? hex(item) : item));
export function pack(format, ...values) {
  const widths = {B: 1, H: 2, I: 4, i: 4, Q: 8, q: 8, f: 4};
  const data = new Uint8Array([...format].reduce((size, field) => size + widths[field], 0));
  const target = view(data);
  let offset = 0;
  [...format].forEach((field, index) => {
    const value = values[index];
    if (field === 'B') target.setUint8(offset, value);
    if (field === 'H') target.setUint16(offset, value, true);
    if (field === 'I') target.setUint32(offset, value, true);
    if (field === 'i') target.setInt32(offset, value, true);
    if (field === 'Q') target.setBigUint64(offset, BigInt(value), true);
    if (field === 'q') target.setBigInt64(offset, BigInt(value), true);
    if (field === 'f') target.setFloat32(offset, value, true);
    offset += widths[field];
  });
  return data;
}
export function crc16(data) {
  let crc = 0xffff;
  for (const value of data) {
    crc ^= value;
    for (let bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (crc & 1 ? 0xa001 : 0);
  }
  return pack('H', crc);
}
export function crc32(data) {
  let crc = 0xffffffff;
  for (const value of data) {
    crc ^= value;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
export function finite(value, name, low = -Infinity, high = Infinity) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < low || n > high) throw new Error(`${name} 超出范围`);
  return n;
}
export class SerialQueue {
  tail = Promise.resolve();
  run(action) {
    const result = this.tail.then(action);
    this.tail = result.catch(() => {});
    return result;
  }
}
