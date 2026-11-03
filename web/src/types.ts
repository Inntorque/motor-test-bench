import type {PowerChannel} from '../../browser/rs485.ts';
import type {CurrentMotor} from '../../browser/control.ts';
export type WorkbenchDevice = Partial<ReturnType<PowerChannel['status']> & ReturnType<CurrentMotor['status']>>;
export type DeviceMap = Record<string, WorkbenchDevice>;
