#!/usr/bin/env node
"use strict";

// npm installs the binary package that matches this host as an optional
// dependency. There is no install script and no file copy: the launcher resolves
// the package npm already placed in node_modules and execs the binary staged
// in it. An absent package or binary is refused, never silently substituted.

const { createRequire } = require("node:module");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { platformPackage } = require("../platform-map.cjs");

const pkg = platformPackage(process.platform, process.arch);
if (!pkg) {
	console.error(`ezchain: unsupported host ${process.platform}/${process.arch}`);
	process.exit(1);
}

const req = createRequire(__filename);
let packageDir;
try {
	packageDir = path.dirname(req.resolve(`${pkg}/package.json`));
} catch {
	console.error(`ezchain: ${pkg} is not installed; reinstall @ezchain/cli on this host`);
	process.exit(1);
}

const nativeName = process.platform === "win32" ? "ezchain-native.exe" : "ezchain-native";
const native = path.join(packageDir, "bin", nativeName);
if (!fs.existsSync(native)) {
	console.error(`ezchain: the native binary is not staged in ${pkg} (expected ${native}). Install a published package from GitHub Releases.`);
	process.exit(1);
}

const child = spawn(native, process.argv.slice(2), { stdio: "inherit" });
child.on("error", (error) => {
	console.error(`ezchain: could not start ${native}: ${error.message}`);
	process.exit(1);
});
child.on("exit", (code, signal) => {
	if (signal) process.kill(process.pid, signal);
	else process.exit(code ?? 1);
});
