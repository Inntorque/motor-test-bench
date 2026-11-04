export type DataBytes = Uint8Array<ArrayBuffer>;
export type Progress = (message: string, value: number) => void;
export interface SwdOperation {ap: 0 | 1; read: 0 | 1; address: number; value?: number}
export interface ChipIdentity {model: string; cpuid: string; dbgId: string; flashKiB: number; sramKiB: number}
export interface BootManifest {
  schema: number; target: string; base: number; bootSize: number; flashSize: number;
  pageSize: number; stack: number; bytes: number; sha256: string; hexSha256?: string; release: string;
  source?: {name: string; format: 'bin' | 'hex'; sha256: string};
}
export interface OperationRecord {
  id: string; chip: ChipIdentity; manifest?: BootManifest; stage: string;
  mode?: 'direct'; backup?: false; before?: DataBytes; backupSha256?: string;
  programmed?: boolean; programmingStarted?: boolean; verified?: boolean; afterSha256?: string; error?: string;
}
export interface BackupRecord extends OperationRecord {before: DataBytes; backupSha256: string}
