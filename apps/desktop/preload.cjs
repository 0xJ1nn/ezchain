"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// The setup window is the only renderer that receives setup controls. The supervisor marks
// it with a command-line flag when it creates the window; the dashboard and the remote view
// are separate windows without it, so no page script there can change the role, stop the
// host, move the data directory or take a backup. The main process checks the sender again.
const isSetupWindow = process.argv.includes("--ezchain-setup-window");

const bridge = {
	version: process.env.EZCHAIN_DESKTOP_VERSION || "0.1.0",
	platform: process.platform,
};

if (isSetupWindow) {
	Object.assign(bridge, {
		setupPreview: (draft) => ipcRenderer.invoke("desktop:setup-preview", draft),
		finishSetup: (draft) => ipcRenderer.invoke("desktop:finish-setup", draft),
		backupNow: () => ipcRenderer.invoke("desktop:backup-now"),
	});
} else {
	Object.assign(bridge, {
		restartServices: () => ipcRenderer.invoke("desktop:restart"),
		openLogs: () => ipcRenderer.invoke("desktop:open-logs"),
		openDataFolder: () => ipcRenderer.invoke("desktop:open-data"),
		remoteConfig: () => ipcRenderer.invoke("desktop:remote-config"),
		remoteRead: (requestPath) => ipcRenderer.invoke("desktop:remote-read", requestPath),
	});
}

contextBridge.exposeInMainWorld("ezchainDesktop", bridge);
