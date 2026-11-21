// Static checks for the packaged installers, run with `node --test "test/*.mjs"`.
//
// A macOS or Windows package cannot be built on this host, so the parts that decide whether
// one *could* be built are checked here instead: the equivalent commands on each target, the
// entitlements files electron-builder will look for, and the fact that the inherit file is
// not the same file as the app's (Apple rejects `com.apple.security.inherit` on a main app).
//
// These are structural checks, not a build. A green run here means the configuration is
// coherent; `docs/DESKTOP.md` is where the absence of a launched package is recorded.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const pkg = JSON.parse(readFileSync(path.join(desktop, "package.json"), "utf8"));
const buildScript = readFileSync(path.resolve(desktop, "../../tools/build-desktop.sh"), "utf8");

// Minimal plist reader: the files here are flat dicts of `<key>` plus one value element.
function plistKeys(file) {
	const text = readFileSync(path.join(desktop, file), "utf8");
	assert.match(text, /^<\?xml version="1\.0"/, `${file} must be a plist`);
	const keys = [...text.matchAll(/<key>([^<]+)<\/key>/g)].map((m) => m[1]);
	assert.equal(new Set(keys).size, keys.length, `${file} repeats a key`);
	return keys;
}

// electron-builder ships a JSON Schema for `build`, and it is the only authority on which
// keys exist at which level. Using it instead of memory caught a real mistake in this
// change: `publisherName` is not a `win` property, it belongs under `signtoolOptions`, so
// the config above would have been rejected at build time on a Windows host — the one place
// this repository cannot try it. The check is reported as skipped, not passed, when the
// dependency tree is absent.
test("the build config satisfies electron-builder's own schema", (t) => {
	let Ajv;
	let schemePath;
	try {
		const fromDesktop = createRequire(path.join(desktop, "package.json"));
		const libPkg = createRequire(fromDesktop.resolve("electron-builder/package.json")).resolve(
			"app-builder-lib/package.json",
		);
		Ajv = createRequire(libPkg)("ajv");
		schemePath = path.join(path.dirname(libPkg), "scheme.json");
	} catch {
		t.skip("electron-builder is not installed; run bash tools/setup-web.sh");
		return;
	}
	const scheme = JSON.parse(readFileSync(schemePath, "utf8"));
	delete scheme.$schema; // a remote $schema would be fetched; the schema itself is local
	const validate = new Ajv({
		allErrors: true,
		strict: false,
		allowUnionTypes: true,
		validateFormats: false,
	}).compile(scheme);
	const ok = validate(pkg.build);
	assert.ok(ok, `electron-builder rejects build: ${JSON.stringify(validate.errors, null, 1)}`);
});

test("every desktop target this repository configures has an installer and a build case", () => {
	const { mac, win, linux } = pkg.build;
	assert.deepEqual(mac.target, ["dmg", "zip"]);
	assert.deepEqual(linux.target, ["AppImage", "deb"]);
	const winTargets = win.target.map((t) => (typeof t === "string" ? t : t.target));
	assert.deepEqual(winTargets, ["nsis", "portable"]);

	// A target in package.json that build-desktop.sh cannot invoke is a package nobody
	// can produce, so the script's case must name the same three platforms.
	for (const target of ["linux", "mac", "win"]) {
		assert.match(buildScript, new RegExp(`^  ${target}\\)$`, "m"), `${target} case`);
	}
	assert.match(buildScript, /electron-builder --win nsis portable/);
	assert.match(buildScript, /electron-builder --mac dmg zip/);
	assert.match(buildScript, /electron-builder --linux AppImage deb/);
});

test("the macOS entitlements are two files, and inherit is only on the child", () => {
	const app = plistKeys(pkg.build.mac.entitlements);
	const child = plistKeys(pkg.build.mac.entitlementsInherit);
	assert.notEqual(
		pkg.build.mac.entitlements,
		pkg.build.mac.entitlementsInherit,
		"Apple rejects com.apple.security.inherit in a main app's entitlements",
	);
	assert.ok(app.includes("com.apple.security.cs.allow-jit"), "V8 needs JIT");
	assert.ok(
		!app.includes("com.apple.security.inherit"),
		"the main app must not request inherit",
	);
	assert.ok(child.includes("com.apple.security.inherit"));
	// Neither file may ask for the capabilities that would undo the fuse hardening.
	for (const keys of [app, child]) {
		assert.ok(!keys.includes("com.apple.security.cs.disable-library-validation"));
		assert.ok(!keys.includes("com.apple.security.cs.allow-dyld-environment-variables"));
		assert.ok(!keys.includes("com.apple.security.app-sandbox"));
	}
});

test("Windows x64 is the only configured architecture, and it is stated", () => {
	const nsis = pkg.build.win.target.find((t) => typeof t === "object");
	assert.deepEqual(nsis.arch, ["x64"]);
	assert.equal(pkg.build.nsis.oneClick, false, "a silent install would hide the choice");
	assert.equal(
		pkg.build.nsis.deleteAppDataOnUninstall,
		false,
		"uninstalling must not delete a paper ledger",
	);
});

test("the build refuses a target this host cannot produce", () => {
	assert.match(buildScript, /cannot build the \$target package on a \$host host/);
	assert.match(buildScript, /PyInstaller does not\s*\n?\s*cross-compile|cross-compile/i);
	// The refusal must come before any work: the case block that builds lives after it.
	const refusal = buildScript.indexOf("cannot build the $target package");
	const firstBuild = buildScript.indexOf("step \"1/7 build and install the native bridge\"");
	assert.ok(refusal > 0 && firstBuild > 0 && refusal < firstBuild);
});

test("the gate runs these tests, unconditionally", () => {
	// A suite nothing runs is a comment. The desktop tests must be a step in tools/ci.sh
	// and must not be behind the web-diff heuristic: a shell or packaging change never
	// touches apps/web, and this is the only place a Windows-only defect can be caught.
	const gate = readFileSync(path.resolve(desktop, "../../tools/ci.sh"), "utf8");
	const stepIndex = gate.indexOf('step "9/10 desktop shell"');
	assert.ok(stepIndex > 0, "tools/ci.sh has no desktop step");
	const block = gate.slice(stepIndex, gate.indexOf('step "10/10', stepIndex));
	assert.ok(block.length > 0, "the desktop step has no body");
	assert.match(block, /cd apps\/desktop/);
	assert.match(block, /node --test "test\/\*\.mjs"/);
	assert.match(gate, /^set -euo pipefail$/m, "the gate script must fail fast");
	assert.doesNotMatch(block, /\|\| true/);
	assert.doesNotMatch(
		block,
		/web_gate/,
		"the desktop suite must not be conditional on a web diff",
	);
});

