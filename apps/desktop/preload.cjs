"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// The renderer is either the normal Next.js app or the local read-only view. It gets only
// a tiny desktop control surface; no Node, no filesystem and no token ever cross this
// bridge. The remote calls below carry a path, never a credential, and the main process
// refuses anything that is not a read.
contextBridge.exposeInMainWorld("ezchainDesktop", {
	version: process.env.EZCHAIN_DESKTOP_VERSION || "0.1.0",
	platform: process.platform,
	restartServices: () => ipcRenderer.invoke("desktop:restart"),
	openLogs: () => ipcRenderer.invoke("desktop:open-logs"),
	openDataFolder: () => ipcRenderer.invoke("desktop:open-data"),
	chooseLocal: () => ipcRenderer.invoke("desktop:choose-local"),
	remoteConnect: (url) => ipcRenderer.invoke("desktop:remote-connect", url),
	remoteConfig: () => ipcRenderer.invoke("desktop:remote-config"),
	remoteRead: (requestPath) => ipcRenderer.invoke("desktop:remote-read", requestPath),
});
