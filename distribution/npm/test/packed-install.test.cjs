"use strict";

// Install from the packed artifacts, not from the source tree. A package that
// only works when it is symlinked from the checkout is not a distribution.
// The native binary is staged into the platform package exactly as the release
// script does; the repository itself never carries one.

const assert = require("node:assert/strict");
const test = require("node:test");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const { platformKey, platformPackage } = require(path.join(root, "cli", "platform-map.cjs"));

const key = platformKey(process.platform, process.arch);
const pkg = platformPackage(process.platform, process.arch);

function pack(dir, destination) {
	const out = execFileSync("npm", ["pack", "--pack-destination", destination, dir], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	}).trim();
	const name = out.split("\n").filter(Boolean).pop();
	return path.join(destination, name);
}

function run(bin, extra = []) {
	return execFileSync(process.execPath, [bin, ...extra], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	});
}

test("a packed install runs the staged platform binary through the meta launcher", { skip: !pkg ? "host os/cpu has no binary package" : false }, () => {
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ezchain-npm-"));
	try {
		const packDir = path.join(tmp, "pack");
		const appDir = path.join(tmp, "app");
		fs.mkdirSync(packDir, { recursive: true });
		fs.mkdirSync(appDir, { recursive: true });

		// Stage a native binary in a copy of the platform package, the same way the
		// release staging script does from a GitHub Release artifact.
		const source = path.join(root, pkg.split("/")[1]);
		const staged = path.join(tmp, pkg.split("/")[1]);
		fs.cpSync(source, staged, { recursive: true });
		const nativeName = process.platform === "win32" ? "ezchain-native.exe" : "ezchain-native";
		const native = path.join(staged, "bin", nativeName);
		fs.writeFileSync(native, `#!/usr/bin/env node\nprocess.stdout.write("EZCHAIN_NPM_SMOKE ${pkg}\\n");\n`, {
			mode: 0o755,
		});

		const metaTarball = pack(path.join(root, "cli"), packDir);
		const platformTarball = pack(staged, packDir);
		fs.writeFileSync(
			path.join(appDir, "package.json"),
			`${JSON.stringify({ name: "packed-install-smoke", version: "1.0.0", private: true })}\n`,
		);

		// The optional dependency is not on a registry in this local smoke, so the
		// matching platform tarball is installed explicitly. Both artifacts come from
		// npm pack; nothing is linked from the source tree.
		execFileSync(
			"npm",
			[
				"install",
				"--prefix",
				appDir,
				"--omit=optional",
				"--no-audit",
				"--no-fund",
				"--no-package-lock",
				metaTarball,
				platformTarball,
			],
			{ encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
		);

		const installedMeta = path.join(appDir, "node_modules", "@ezchain", "cli");
		const installedPlatform = path.join(appDir, "node_modules", "@ezchain", `cli-${key}`);
		assert.ok(fs.existsSync(path.join(installedMeta, "package.json")), "meta package installed from tarball");
		assert.ok(fs.existsSync(path.join(installedPlatform, "package.json")), "platform package installed from tarball");
		const installedPkg = JSON.parse(fs.readFileSync(path.join(installedMeta, "package.json"), "utf8"));
		for (const script of ["preinstall", "install", "postinstall", "prepare"]) {
			assert.equal(installedPkg.scripts?.[script], undefined, `installed meta has no ${script}`);
		}

		const launcher = path.join(installedMeta, "bin", "ezchain.cjs");
		assert.equal(run(launcher).trim(), `EZCHAIN_NPM_SMOKE ${pkg}`);

		// A platform package with no staged binary fails closed instead of launching
		// an unrelated executable from PATH.
		fs.rmSync(path.join(installedPlatform, "bin", nativeName));
		assert.throws(() => run(launcher), (error) => String(error.stderr).includes("not staged"));
	} finally {
		fs.rmSync(tmp, { recursive: true, force: true });
	}
});
