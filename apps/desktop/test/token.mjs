// The deployment token is durable state, so its file mode must be enforced on every path
// through the token store: reuse as well as first generation. Run with `node --test "test/*.mjs"`.
//
// main.cjs is an Electron entry point and cannot be imported in plain Node, so these tests
// lift its loadToken function out of the source and run it in a small sandbox with injected
// fs/crypto. That exercises the real implementation, not a copy of it.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const mainSource = fs.readFileSync(path.join(here, "..", "main.cjs"), "utf8");

function loadTokenFrom({ fs: fsImpl, crypto: cryptoImpl, tokenFile: tokenPath }) {
	const start = mainSource.indexOf("function loadToken()");
	const end = mainSource.indexOf("function freePort()", start);
	assert.ok(start !== -1 && end > start, "loadToken must be present in main.cjs");
	const body = mainSource.slice(start, end).trimEnd();
	const context = vm.createContext({ fs: fsImpl, crypto: cryptoImpl, tokenFile: tokenPath });
	return vm.runInContext(`(${body})`, context);
}

test("a reused token has its file mode tightened to 0600", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ezchain-token-"));
	const tokenFile = path.join(dir, "api-token");
	const token = "a".repeat(64);
	fs.writeFileSync(tokenFile, token, { mode: 0o644 });
	fs.chmodSync(tokenFile, 0o644); // loose even under a strict umask
	try {
		const loadToken = loadTokenFrom({ fs, crypto, tokenFile });
		assert.equal(loadToken(), token, "the valid existing token is reused");
		assert.equal(fs.statSync(tokenFile).mode & 0o777, 0o600);
	} finally {
		fs.rmSync(dir, { recursive: true, force: true });
	}
});

test("a reused token whose mode cannot be tightened is refused with a clear error", () => {
	const stubFs = {
		readFileSync: () => "a".repeat(64),
		chmodSync: () => {
			throw Object.assign(new Error("EPERM: operation not permitted"), { code: "EPERM" });
		},
	};
	const loadToken = loadTokenFrom({ fs: stubFs, crypto, tokenFile: "api-token" });
	assert.throws(loadToken, /cannot tighten.*mode 0600|mode 0600.*cannot/i);
});

test("a fresh token is generated with mode 0600", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ezchain-token-"));
	const tokenFile = path.join(dir, "api-token");
	try {
		const loadToken = loadTokenFrom({ fs, crypto, tokenFile });
		const token = loadToken();
		assert.equal(token.length, 64);
		assert.equal(fs.statSync(tokenFile).mode & 0o777, 0o600);
	} finally {
		fs.rmSync(dir, { recursive: true, force: true });
	}
});
