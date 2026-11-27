"use strict";

// Regression: --target mac used to call build-desktop.sh, which hard-coded an
// Electron Linux build before packaging macOS. The staging step must derive its
// platform from the host and a macOS plan must never name a Linux target.

const assert = require("node:assert/strict");
const test = require("node:test");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const repo = path.resolve(__dirname, "..", "..", "..");
const builder = "tools/build-desktop.sh";
const packager = "tools/package-desktop.sh";

function run(script, args) {
	return execFileSync("bash", [path.join(repo, script), ...args], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	});
}

const hostPlatform = { darwin: "mac", linux: "linux", win32: "win" }[process.platform];
const hostTarget = { darwin: "mac", linux: "linux", win32: "win" }[process.platform];

test("the staging builder derives its electron-builder platform from the host", () => {
	assert.equal(run(builder, ["--print-platform"]).trim(), hostPlatform);
	const source = fs.readFileSync(path.join(repo, builder), "utf8");
	assert.equal(source.includes("--linux dir"), false, "no hard-coded Linux dir target remains");
	assert.match(source, /BUILDER_PLATFORM/, "the platform is derived once and reused");
});

test("a macOS dry run never invokes a Linux target", { skip: process.platform !== "darwin" ? "macOS target is refused on this host" : false }, () => {
	const plan = run(packager, ["--target", "mac", "--dry-run"]);
	assert.match(plan, /host platform mac/);
	assert.match(plan, /electron-builder --mac dmg zip/);
	assert.equal(plan.includes("--linux"), false, `macOS plan must not name Linux: ${plan}`);
});

test("the host target's dry run uses only that platform's targets", () => {
	const plan = run(packager, ["--target", hostTarget, "--dry-run"]);
	assert.match(plan, new RegExp(`host platform ${hostPlatform}`));
	for (const other of ["linux", "mac", "win"]) {
		if (other !== hostPlatform) assert.equal(plan.includes(`--${other} `), false, `${other} must not appear in a ${hostPlatform} plan`);
	}
});
