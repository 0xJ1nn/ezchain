// Static wiring checks for the desktop shell, run with `node --test "test/*.mjs"`.
//
// These are the two mistakes that only show up inside a packaged build, where no unit test
// can reach them:
//
//   1. main.cjs requires a file that `build.files` does not pack. Electron then dies at
//      startup with MODULE_NOT_FOUND, in the app, not in CI.
//   2. the RunAsNode fuse is disabled while the web sidecar is spawned as
//      `spawn(process.execPath, ..., { ELECTRON_RUN_AS_NODE: "1" })`. That combination starts
//      nothing: measured on Electron 45.0.0 with the packaged fuse wire, the child exits 1
//      and the window never gets a server. The shell must use an Electron utility process.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const mainSource = readFileSync(path.join(desktop, "main.cjs"), "utf8");
const pkg = JSON.parse(readFileSync(path.join(desktop, "package.json"), "utf8"));

// Comments legitimately describe the pattern being avoided, so the assertions read code only.
// A line comment or block-comment body is removed; string contents are left alone.
const code = mainSource
	.replace(/\/\*[\s\S]*?\*\//g, "")
	.split("\n")
	.map((line) => line.replace(/(^|[^:])\/\/.*$/, "$1"))
	.join("\n");

test("every local module main.cjs requires is in build.files", () => {
	// `require("./sender")` resolves to sender.js, which is what electron-builder packs.
	const required = [...code.matchAll(/require\("\.\/([\w.-]+)"\)/g)].map(
		(m) => (m[1].endsWith(".js") ? m[1] : `${m[1]}.js`),
	);
	assert.ok(required.length > 0, "main.cjs requires at least one local module");
	const packed = new Set(pkg.build.files);
	for (const name of required) {
		assert.ok(
			packed.has(name),
			`main.cjs requires ./${name} but build.files does not pack it: ${[...packed].join(", ")}`,
		);
	}
});

test("a packaged shell never needs the RunAsNode fuse", () => {
	const runAsNode = pkg.build.electronFuses?.runAsNode;
	assert.equal(runAsNode, false, "the packaged shell disables RunAsNode");

	// The fuse forbids ELECTRON_RUN_AS_NODE, so no code path may set it.
	assert.doesNotMatch(
		code,
		/ELECTRON_RUN_AS_NODE:\s*["']1["']/,
		"main.cjs sets ELECTRON_RUN_AS_NODE while the RunAsNode fuse is disabled; that child exits 1",
	);
	assert.doesNotMatch(
		code,
		/spawn\(\s*process\.execPath/,
		"main.cjs spawns the Electron binary as Node; use utilityProcess.fork instead",
	);
});

test("the web sidecar runs as an Electron utility process", () => {
	assert.match(
		code,
		/utilityProcess\.fork\(\s*webServer/,
		"spawnWeb must fork the Next server as a utility process",
	);
	// A utility process is already Node; asking it to become Node again is the mode the fuse
	// refuses, so the variable is removed from the child environment.
	assert.match(
		code,
		/delete env\.ELECTRON_RUN_AS_NODE/,
		"the web sidecar environment must not carry ELECTRON_RUN_AS_NODE",
	);
});

test("the IPC bridge is only reachable from the app window", () => {
	const exposed = readFileSync(path.join(desktop, "preload.cjs"), "utf8");
	const channels = [...code.matchAll(/handleFromApp\("([\w:-]+)"/g)].map((m) => m[1]);
	assert.ok(channels.length > 0, "the shell registers privileged handlers");
	for (const channel of channels) {
		assert.match(
			exposed,
			new RegExp(`ipcRenderer\\.invoke\\("${channel}"\\)`),
			`preload.cjs does not expose ${channel}, so the edit is dead`,
		);
	}
	// A raw ipcMain.handle at a call site would skip the sender check. The wrapper itself is
	// the only permitted one, so the count is exact rather than a loose "no match".
	const raw = [...code.matchAll(/ipcMain\.handle\(/g)].length;
	assert.equal(raw, 1, "ipcMain.handle must appear exactly once, inside handleFromApp");
});
