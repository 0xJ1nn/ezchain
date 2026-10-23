"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// The renderer is the normal Next.js app. It gets only a tiny desktop control surface;
// no Node, no filesystem and no token ever cross this bridge.
contextBridge.exposeInMainWorld("ezchainDesktop", {
	version: process.env.EZCHAIN_DESKTOP_VERSION || "0.1.0",
	platform: process.platform,
	restartServices: () => ipcRenderer.invoke("desktop:restart"),
	openLogs: () => ipcRenderer.invoke("desktop:open-logs"),
	openDataFolder: () => ipcRenderer.invoke("desktop:open-data"),
});
