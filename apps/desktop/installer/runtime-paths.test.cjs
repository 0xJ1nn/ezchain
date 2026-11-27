"use strict";

// The frozen sidecar is a plain executable on POSIX and an .exe on Windows.
// Resolving its name from the host is a portability rule, not a workaround at
// one call site; the supervisor and the packaged app use the same function.

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");
const { backendBinaryName, backendBinPath } = require("./runtime-paths.cjs");

test("the frozen sidecar name follows the host executable convention", () => {
	assert.equal(backendBinaryName("win32"), "ezchain-api.exe");
	assert.equal(backendBinaryName("darwin"), "ezchain-api");
	assert.equal(backendBinaryName("linux"), "ezchain-api");
	assert.throws(() => backendBinaryName("unknown"), /unsupported platform/);
});

test("the sidecar path is built from the resources root and the host name", () => {
	assert.equal(backendBinPath("/opt/ezchain/resources", "darwin"), path.join("/opt/ezchain/resources", "backend", "ezchain-api", "ezchain-api"));
	assert.equal(
		backendBinPath("C:\\ezchain\\resources", "win32"),
		path.win32.join("C:\\ezchain\\resources", "backend", "ezchain-api", "ezchain-api.exe"),
	);
});
