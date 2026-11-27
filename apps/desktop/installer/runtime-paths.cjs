"use strict";

// PyInstaller writes a bare executable on POSIX and an .exe on Windows. The
// name is a property of the frozen artifact, so the supervisor resolves it from
// the host rather than special-casing a spawn call.

const path = require("node:path");

function backendBinaryName(platform) {
	switch (platform) {
		case "win32":
			return "ezchain-api.exe";
		case "darwin":
		case "linux":
			return "ezchain-api";
		default:
			throw new Error(`unsupported platform: ${platform}`);
	}
}

function backendBinPath(resourcesRoot, platform) {
	const join = platform === "win32" ? path.win32.join : path.posix.join;
	return join(resourcesRoot, "backend", "ezchain-api", backendBinaryName(platform));
}

module.exports = { backendBinaryName, backendBinPath };
