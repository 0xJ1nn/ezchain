"use strict";

// The promotion smoke: install the packed npm packages with a real native CLI
// staged in the platform package and run read-only operator commands. A marker
// fixture is not evidence; this test only runs when a real binary is present.

const assert = require("node:assert/strict");
const test = require("node:test");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const { platformPackage } = require(path.join(root, "cli", "platform-map.cjs"));
const pkg = platformPackage(process.platform, process.arch);
const nativeName = process.platform === "win32" ? "ezchain-native.exe" : "ezchain-native";
const native = pkg ? path.join(root, pkg.split("/")[1], "bin", nativeName) : "";
const missing = !pkg || !fs.existsSync(native) ? "no staged native binary on this host" : false;

function pack(dir, destination) {
	const out = execFileSync("npm", ["pack", "--pack-destination", destination, dir], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	}).trim();
	return path.join(destination, out.split("\n").filter(Boolean).pop());
}

function cli(appDir, args) {
	return execFileSync(process.execPath, [path.join(appDir, "node_modules", "@ezchain", "cli", "bin", "ezchain.cjs"), ...args], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	});
}

test("a real packed CLI answers version, paper mode and help", { skip: missing }, () => {
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ezchain-real-npm-"));
	try {
		const packDir = path.join(tmp, "pack");
		const appDir = path.join(tmp, "app");
		const staged = path.join(tmp, pkg.split("/")[1]);
		fs.mkdirSync(packDir, { recursive: true });
		fs.mkdirSync(appDir, { recursive: true });
		fs.cpSync(path.join(root, pkg.split("/")[1]), staged, { recursive: true });

		const metaTarball = pack(path.join(root, "cli"), packDir);
		const platformTarball = pack(staged, packDir);
		fs.writeFileSync(
			path.join(appDir, "package.json"),
			`${JSON.stringify({ name: "real-install-smoke", version: "1.0.0", private: true })}\n`,
		);
		execFileSync(
			"npm",
			["install", "--prefix", appDir, "--omit=optional", "--no-audit", "--no-fund", "--no-package-lock", metaTarball, platformTarball],
			{ encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
		);

		const version = cli(appDir, ["version"]).trim();
		const packageVersion = JSON.parse(fs.readFileSync(path.join(root, "cli", "package.json"), "utf8")).version;
		assert.equal(version, `ezchain-cli ${packageVersion}`, "the real CLI reports the packaged npm version");
		assert.match(cli(appDir, ["mode"]), /^mode: PAPER$/m);
		assert.match(cli(appDir, ["help"]), /ezchain-cli — read-only operator CLI/);
		assert.match(cli(appDir, ["gate"]), /port gates/);
	} finally {
		fs.rmSync(tmp, { recursive: true, force: true });
	}
});
