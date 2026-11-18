export interface StopEvidence {
  sensorSpeedRpm?: number; confirmed?: boolean; state?: string; motorRotorRpm?: number;
  positionSpanDeg?: number; durationS?: number; samples?: number;
}
export interface TorqueConfig {startNm: number; stepNm: number; maxNm: number; intervalS: number}
export interface RunState {
  motorName?: string; characteristicSchema?: number; characteristicPoints?: import('./characteristic-points.ts').CharacteristicPoint[];
  acquisitionManaged?: boolean; recordingError?: string; stopErrors?: string[]; running?: boolean; phase?: string; reason?: string; stopConfirmed?: boolean | null;
  id?: string; mode?: string; motorMode?: string; recordingId?: string; scriptId?: number;
  targetA?: number; elapsed?: number; steps?: number; stepA?: number; intervalS?: number;
  durationS?: number; limitA?: number; deviceLimitA?: number; limitReached?: boolean;
  peakIqA?: number; lastTargetA?: number; stopMethod?: string; startedAt?: number;
  loadElapsedS?: number; loadRequested?: boolean; loadStartedAt?: number; curveStartedAt?: number; brakeEnabled?: boolean;
  targetNm?: number; measuredNm?: number; brakeCurrentA?: number; loadSteps?: number; loadStages?: {targetNm: number; startedAt: number; scheduledAt?: number}[];
  torqueConfig?: TorqueConfig; stopConfirmation?: StopEvidence; stallDetectedAt?: number;
  stallEndpoint?: {torque: number; speed: number; receivedAt: number; speedDecimals: number};
  measurementEndedAt?: number;
  postStopTailState?: 'collecting' | 'zero' | 'timeout' | 'disconnected' | 'cancelled' | 'unconfirmed';
  postStopZero?: {receivedAt: number; torque: number; speed: number};
  maximumConfig?: {busLimitA: number; busVoltageV: number; maxPhaseA: number; negativeIdMinA: number; maxSpeedRpm: number};
}
