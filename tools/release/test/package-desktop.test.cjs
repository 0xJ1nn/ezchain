"use strict";

// A package target that cannot be built here is refused before any build runs.
// The refusal is the evidence that it is unverified, not a silent skip.

const assert = require("node:assert/strict");
const test = require("node:test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const repo = path.resolve(__dirname, "..", "..", "..");
const script = path.join(repo, "tools", "package-desktop.sh");

function run(args) {
	return execFileSync("bash", [script, ...args], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	});
}

test("an unknown target is refused", () => {
	assert.throws(() => run(["--target", "bogus", "--check-only"]), (error) => String(error.stderr).includes("unknown target"));
});

test("only targets buildable on this host pass the check", () => {
	const supported = { linux: "linux", mac: "darwin", win: "win32" };
	for (const [target, host] of Object.entries(supported)) {
		if (process.platform === host) {
			const out = run(["--target", target, "--check-only"]);
			assert.ok(out.includes("unsigned"), `${target}: configured without signing`);
			assert.ok(out.includes(target), `${target}: check names the target`);
		} else {
			assert.throws(
				() => run(["--target", target, "--check-only"]),
				(error) => String(error.stderr).includes("cannot be built on this host"),
				`${target}: refused on ${process.platform}`,
			);
		}
	}
});

test("the all target refuses when any platform cannot be built here", () => {
	if (process.platform === "win32") return; // all three are buildable on Windows CI hosts
	assert.throws(
		() => run(["--target", "all", "--check-only"]),
		(error) => String(error.stderr).includes("cannot be built on this host"),
	);
});
