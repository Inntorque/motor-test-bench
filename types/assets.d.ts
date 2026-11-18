/// <reference types="vite/client" />
interface Window {
  INNTORQUE_BROWSER_DIRECT: boolean;
  inntorqueSelectRs485?: () => Promise<string>;
  inntorqueDesktop?: {ready?: () => Promise<void>; activity: (active: boolean) => void; saveData: (packet: unknown) => Promise<unknown>; openDataFolder: () => Promise<void>; dataDirectory: () => Promise<string>};
  inntorqueCanClose?: (finish?: boolean) => Promise<boolean>;
}
declare var INNTORQUE_BROWSER_DIRECT: boolean;
