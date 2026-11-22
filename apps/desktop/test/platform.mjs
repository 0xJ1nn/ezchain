// Portability checks for the desktop shell, run with `node --test "test/*.mjs"`.
//
// Each assertion here corresponds to a bug that only appears on a platform this host does
// not run. They are unit tests over pure functions, so they run everywhere:
//
//   * the frozen sidecar is `ezchain-api.exe` on Windows, and a literal name makes the
//     packaged app fail to start with "backend sidecar missing";
//   * `sqlite+pysqlite:///C:\Users\...` reaches sqlite with backslashes, which it treats
//     as escapes; the URL must use forward slashes;
//   * Windows ships bsdtar outside PATH, so the extractor must resolve it.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { sidecarName, sqliteUrl, tarBinary, webRuntimeReady } from "../platform.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const mainSource = readFileSync(path.join(desktop, "main.cjs"), "utf8");
const pkg = JSON.parse(readFileSync(path.join(desktop, "package.json"), "utf8"));

test("the sidecar name carries the platform's executable suffix", () => {
	assert.equal(sidecarName("win32"), "ezchain-api.exe");
	assert.equal(sidecarName("linux"), "ezchain-api");
	assert.equal(sidecarName("darwin"), "ezchain-api");
	// A literal name is what broke Windows; the shell must call the helper instead.
	assert.doesNotMatch(
		mainSource,
		/"ezchain-api"\s*\)/,
		"main.cjs hard-codes the sidecar name instead of using sidecarName()",
	);
	assert.match(mainSource, /sidecarName\(process\.platform\)/);
});

test("the sqlite URL is absolute and uses forward slashes on every platform", () => {
	assert.equal(
		sqliteUrl("/home/user/.config/ezCHAIN/ezchain.db", "linux"),
		"sqlite+pysqlite:////home/user/.config/ezCHAIN/ezchain.db",
	);
	assert.equal(
		sqliteUrl("/Users/user/Library/ezchain.db", "darwin"),
		"sqlite+pysqlite:////Users/user/Library/ezchain.db",
	);
	assert.equal(
		sqliteUrl("C:\\Users\\user\\AppData\\Roaming\\ezCHAIN\\ezchain.db", "win32"),
		"sqlite+pysqlite:///C:/Users/user/AppData/Roaming/ezCHAIN/ezchain.db",
	);
	for (const url of [
		sqliteUrl("/home/user/x.db", "linux"),
		sqliteUrl("C:\\Users\\user\\x.db", "win32"),
	]) {
		assert.doesNotMatch(url, /\\/, `${url} carries a backslash`);
	}
	assert.match(mainSource, /sqliteUrl\(dbFile, process\.platform\)/);
	assert.doesNotMatch(
		mainSource,
		/sqlite\+pysqlite:\/\/\/\$\{dbFile\}/,
		"main.cjs interpolates the database path straight into the URL again",
	);
});

test("the tar binary is resolved on Windows and named on POSIX", () => {
	assert.equal(tarBinary({ platform: "linux" }), "tar");
	assert.equal(tarBinary({ platform: "darwin" }), "tar");
	// Windows: prefer the shipped bsdtar, fall back to PATH when it is not there.
	const found = tarBinary({
		platform: "win32",
		systemRoot: "C:\\Windows",
		exists: (file) => file === "C:\\Windows\\System32\\tar.exe",
	});
	assert.equal(found, "C:\\Windows\\System32\\tar.exe");
	const missing = tarBinary({
		platform: "win32",
		systemRoot: "C:\\Windows",
		exists: () => false,
	});
	assert.equal(missing, "tar");
});

test("a runtime is only ready when the marker names this version", () => {
	const files = { "/r/apps/web/server.js": "x", "/r/.complete": "1.4.0\n" };
	const ready = webRuntimeReady({
		serverPath: "/r/apps/web/server.js",
		markerPath: "/r/.complete",
		version: "1.4.0",
		exists: (file) => file in files,
		read: (file) => files[file],
	});
	assert.equal(ready, true, "a matching marker is ready");

	for (const [name, overrides] of [
		["a newer app version", { version: "1.5.0" }],
		["a missing server", { serverPath: "/r/nope.js" }],
		["a missing marker", { markerPath: "/r/nope" }],
	]) {
		assert.equal(
			webRuntimeReady({
				serverPath: "/r/apps/web/server.js",
				markerPath: "/r/.complete",
				version: "1.4.0",
				exists: (file) => file in files,
				read: (file) => files[file],
				...overrides,
			}),
			false,
			`${name} must not count as ready`,
		);
	}
});

test("every local module main.cjs requires is packed", () => {
	const required = [...mainSource.matchAll(/require\("\.\/([\w.-]+)"\)/g)].map((m) =>
		m[1].endsWith(".js") ? m[1] : `${m[1]}.js`,
	);
	assert.ok(required.includes("platform.js"), "platform.cjs must be packed");
	for (const name of required) {
		assert.ok(
			pkg.build.files.includes(name),
			`main.cjs requires ./${name} but build.files does not pack it`,
		);
	}
});
