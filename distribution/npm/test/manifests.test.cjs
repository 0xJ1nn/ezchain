"use strict";

// The npm distribution is a meta-package plus one binary package per platform.
// These tests pin the contract without needing any platform binary: an install
// script that copies a binary at install time is a defect, and a missing
// optional dependency is a broken install on that platform.

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const metaPath = path.join(root, "cli", "package.json");
const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
const { platformKey, platformPackage, SUPPORTED } = require(path.join(root, "cli", "platform-map.cjs"));

const EXPECTED = [
	["darwin", "arm64", "@ezchain/cli-darwin-arm64"],
	["darwin", "x64", "@ezchain/cli-darwin-x64"],
	["linux", "x64", "@ezchain/cli-linux-x64"],
	["linux", "arm64", "@ezchain/cli-linux-arm64"],
	["win32", "x64", "@ezchain/cli-win32-x64"],
];

const FORBIDDEN_SCRIPTS = ["preinstall", "install", "postinstall", "prepare"];

test("the meta package declares every platform as an optional dependency", () => {
	assert.equal(meta.name, "@ezchain/cli");
	assert.equal(meta.version, "0.1.0");
	assert.deepEqual(Object.keys(meta.optionalDependencies ?? {}).sort(), EXPECTED.map(([, , name]) => name).sort());
	for (const [, , name] of EXPECTED) assert.equal(meta.optionalDependencies[name], meta.version);
	assert.deepEqual(meta.bin, { ezchain: "bin/ezchain.cjs" });
});

test("no package installs, copies or downloads anything at install time", () => {
	for (const file of [metaPath, ...EXPECTED.map(([, , name]) => path.join(root, name.split("/")[1], "package.json"))]) {
		const pkg = JSON.parse(fs.readFileSync(file, "utf8"));
		for (const key of FORBIDDEN_SCRIPTS) {
			assert.equal(pkg.scripts?.[key], undefined, `${pkg.name}: ${key} must not exist`);
		}
	}
});

test("each platform package declares exactly one os/cpu pair", () => {
	for (const [os, cpu, name] of EXPECTED) {
		const dir = path.join(root, name.split("/")[1]);
		const pkg = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
		assert.equal(pkg.name, name);
		assert.equal(pkg.version, meta.version);
		assert.deepEqual(pkg.os, [os]);
		assert.deepEqual(pkg.cpu, [cpu]);
		assert.equal(pkg.bin, undefined, "the binary package does not shadow the meta launcher");
		assert.ok(
			fs.readFileSync(path.join(dir, ".gitignore"), "utf8").includes("ezchain-native"),
			`${name}: the staged binary must be gitignored`,
		);
	}
});

test("the launcher maps every supported host to exactly one binary package", () => {
	assert.deepEqual(SUPPORTED.map(([os, cpu]) => platformKey(os, cpu)).sort(), EXPECTED.map(([os, cpu]) => `${os}-${cpu}`).sort());
	for (const [os, cpu, name] of EXPECTED) {
		assert.equal(platformPackage(os, cpu), name);
		assert.equal(meta.optionalDependencies[platformPackage(os, cpu)], meta.version);
	}
	assert.equal(platformPackage("freebsd", "x64"), null);
	assert.equal(platformPackage("linux", "ia32"), null);
});
