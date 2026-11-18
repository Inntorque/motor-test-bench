import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./styles.css";
import "./desktop.css";
import {installApiClient,apiFetch} from './apiClient.ts';
import {startDeviceStore} from './deviceStore.ts';

installApiClient();
startDeviceStore(apiFetch);

function DesktopReady() {
  React.useEffect(() => {
    void apiFetch('/api/health',{forceNetwork:true}).then(response => {
      if (!response.ok) throw new Error('测功机通信初始化失败');
      return window.inntorqueDesktop?.ready?.();
    }).catch(console.error);
  }, []);
  return null;
}

createRoot(document.getElementById("root")).render(<><App /><DesktopReady /></>);
