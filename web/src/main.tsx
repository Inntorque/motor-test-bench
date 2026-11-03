import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./styles.css";
import {installApiClient,apiFetch} from './apiClient.ts';
import {startDeviceStore} from './deviceStore.ts';

installApiClient();
startDeviceStore(apiFetch);

createRoot(document.getElementById("root")).render(<App />);
