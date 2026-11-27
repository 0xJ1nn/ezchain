"use strict";

// Package configuration is a manifest, not a claim that a build ran. These
// checks pin the artifact shapes and prove no signing identity is configured;
// a target that cannot be built on this host is reported as unverified in the
// evidence, never as a pass.

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");

const config = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
const build = config.build;
const version = config.version;

function names(target) {
	return (Array.isArray(target) ? target : [target]).map((item) => (typeof item === "string" ? item : item.target));
}

function artifact(target, arch, ext) {
	return build[target].artifactName.replace("${version}", version).replace("${arch}", arch).replace("${ext}", ext);
}

test("every platform declares its unsigned artifact shape", () => {
	assert.ok(names(build.linux.target).includes("AppImage"));
	assert.ok(names(build.linux.target).includes("deb"));
	assert.equal(artifact("linux", "x86_64", "AppImage"), `ezCHAIN-${version}-x86_64.AppImage`);

	assert.ok(names(build.mac.target).includes("dmg"));
	assert.ok(names(build.mac.target).includes("zip"));
	assert.equal(artifact("mac", "arm64", "dmg"), `ezCHAIN-${version}-arm64.dmg`);

	assert.ok(names(build.win.target).includes("nsis"));
	assert.equal(artifact("win", "x64", "exe"), `ezCHAIN-${version}-x64-setup.exe`);
});

test("no platform asks electron-builder to sign or notarize", () => {
	assert.equal(build.mac.identity, null);
	assert.equal(build.mac.hardenedRuntime, false);
	assert.equal(build.mac.gatekeeperAssess, false);
	assert.equal(build.win.signAndEditExecutable, false);
	for (const platform of ["mac", "win"]) {
		for (const field of ["certificateFile", "certificatePassword", "certificateSubjectName", "azureSignOptions"]) {
			assert.equal(build[platform][field], undefined, `${platform}.${field} must not exist`);
		}
	}
	assert.equal(build.mac.notarize, undefined);
});

test("the runtime files the shell reads are staged into every package", () => {
	const staged = new Map(build.extraResources.map((item) => [item.to, item.from]));
	for (const name of ["backend", "web.tar.gz", "icon.png", "LICENSE", "NOTICE", "TRADEMARKS.md"]) {
		assert.ok(staged.has(name), `${name} must be an extraResource`);
	}
	assert.equal(staged.get("backend"), "build/backend");
	assert.equal(staged.get("web.tar.gz"), "build/web.tar.gz");
	for (const file of ["main.cjs", "preload.cjs", "sender.js", "package.json"]) {
		assert.ok(build.files.includes(file), `${file} must be in files`);
	}
});
