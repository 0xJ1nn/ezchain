"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// The renderer is the setup screen, the normal Next.js app or the local read-only view.
// It gets only a tiny desktop control surface; no Node, no filesystem and no token value
// ever cross this bridge. Setup carries paths and choices, remote calls carry a path and
// never a credential, and the main process refuses anything that is not a read.
contextBridge.exposeInMainWorld("ezchainDesktop", {
	version: process.env.EZCHAIN_DESKTOP_VERSION || "0.1.0",
	platform: process.platform,
	restartServices: () => ipcRenderer.invoke("desktop:restart"),
	openLogs: () => ipcRenderer.invoke("desktop:open-logs"),
	openDataFolder: () => ipcRenderer.invoke("desktop:open-data"),
	setupPreview: (draft) => ipcRenderer.invoke("desktop:setup-preview", draft),
	finishSetup: (draft) => ipcRenderer.invoke("desktop:finish-setup", draft),
	backupNow: () => ipcRenderer.invoke("desktop:backup-now"),
	openSetup: () => ipcRenderer.invoke("desktop:open-setup"),
	remoteConfig: () => ipcRenderer.invoke("desktop:remote-config"),
	remoteRead: (requestPath) => ipcRenderer.invoke("desktop:remote-read", requestPath),
});
