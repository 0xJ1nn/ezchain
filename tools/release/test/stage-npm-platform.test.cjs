"use strict";

// The staging script accepts a real native binary or builds the shipped CLI on
// the host. It must refuse a cross-build, stage into the matching package and
// pack a pair of artifacts. No fixture that merely prints a marker is involved.

const assert = require("node:assert/strict");
const test = require("node:test");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const repo = path.resolve(__dirname, "..", "..", "..");
const script = path.join(repo, "tools", "release", "stage-npm-platform.sh");

function run(args) {
	return execFileSync("bash", [script, ...args], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	});
}

test("an unknown target is refused", () => {
	assert.throws(() => run(["--target", "bogus", "--binary", "/bin/echo"]), (error) => String(error.stderr).includes("unknown target"));
});

test("building the CLI for another host is refused", () => {
	const host = `${process.platform}-${process.arch}`;
	const other = host === "darwin-arm64" ? "win32-x64" : "darwin-arm64";
	assert.throws(
		() => run(["--target", other, "--build-cli"]),
		(error) => String(error.stderr).includes("cannot build"),
	);
});

test("a real host binary is staged and both packages are packed", () => {
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ezchain-stage-"));
	try {
		const stage = path.join(tmp, "npm");
		const out = path.join(tmp, "pack");
		fs.mkdirSync(path.join(stage, "cli"), { recursive: true });
		fs.mkdirSync(path.join(stage, "cli-darwin-arm64", "bin"), { recursive: true });
		fs.mkdirSync(out, { recursive: true });
		for (const name of ["package.json", "platform-map.cjs", "README.md"]) {
			fs.copyFileSync(path.join(repo, "distribution", "npm", "cli", name), path.join(stage, "cli", name));
		}
		fs.mkdirSync(path.join(stage, "cli", "bin"), { recursive: true });
		fs.copyFileSync(path.join(repo, "distribution", "npm", "cli", "bin", "ezchain.cjs"), path.join(stage, "cli", "bin", "ezchain.cjs"));
		for (const name of ["package.json", ".gitignore"]) {
			fs.copyFileSync(path.join(repo, "distribution", "npm", "cli-darwin-arm64", name), path.join(stage, "cli-darwin-arm64", name));
		}

		const output = run(["--target", "darwin-arm64", "--binary", "/bin/echo", "--stage-root", stage, "--out", out]);
		assert.ok(fs.existsSync(path.join(stage, "cli-darwin-arm64", "bin", "ezchain-native")), "binary staged");
		assert.match(output, /artifact: .*ezchain-cli-0\.1\.0\.tgz/);
		assert.match(output, /artifact: .*ezchain-cli-darwin-arm64-0\.1\.0\.tgz/);
		assert.match(output, /sha256: [0-9a-f]{64}/);
		assert.equal(fs.readdirSync(out).filter((name) => name.endsWith(".tgz")).length, 2);

		const sourceIgnore = fs.readFileSync(path.join(repo, "distribution", "npm", "cli-darwin-arm64", ".gitignore"), "utf8");
		assert.ok(sourceIgnore.includes("ezchain-native"), "source package still gitignores the binary");
	} finally {
		fs.rmSync(tmp, { recursive: true, force: true });
	}
});
