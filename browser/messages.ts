export type AppId = 'dynamometer' | 'sg-6010hb' | 'rm-7010';
export type IoMethod = 'ports' | 'can-config' | 'serial-open' | 'serial-close' | 'serial-write' | 'usb-open' | 'usb-close' | 'usb-send';
export interface IoArguments {
  device?: string; baudRate?: number; dataBits?: number; stopBits?: number;
  parity?: string; bitrate?: number; id?: number; data?: Uint8Array;
}
export type DeviceInput = Uint8Array | {id: number; data: Uint8Array};
export interface DeviceStateEvent {
  active?: boolean; body?: BlobPart; type?: string; name?: string;
  control?: {blocked?: boolean; reason?: string}; test?: {running?: boolean};
  devices?: Record<string, {snapshot?: {status?: number; motor?: {output_on?: boolean}}}>;
}
export type MainToWorker =
  | {kind: 'init'; app: AppId}
  | {kind: 'request'; id: number; path: string; method: string; body: Record<string, unknown>; headers: Record<string, string>}
  | {kind: 'io-result'; id: number; value?: unknown; error?: string}
  | {kind: 'input'; device: string; data: DeviceInput}
  | {kind: 'io-error'; device: string; error: string};
export type WorkerToMain =
  | {kind: 'ready'} | {kind: 'fatal'; error: string}
  | {kind: 'io-call'; id: number; method: IoMethod; args: IoArguments}
  | {kind: 'event'; event: string; data: DeviceStateEvent}
  | {kind: 'response'; id: number; status: number; result: unknown; epoch?: number};
export interface DeviceIo {
  call(method: IoMethod, args: IoArguments): Promise<unknown>;
  subscribe(device: string, callback: (data: DeviceInput) => void): () => void;
}
