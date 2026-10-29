import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import {installApiClient,apiFetch} from './apiClient.js';
import {startDeviceStore} from './deviceStore.js';

installApiClient();
startDeviceStore(apiFetch);

createRoot(document.getElementById("root")).render(<App />);
