// Unit tests for the desktop shell's security primitives (apps/desktop/sender.js).
//
// Run with: node --test apps/desktop/test/
//
// The CSP assertions matter twice: they pin the policy's shape (self-only scripts apart
// from the inline bootstrap Next requires, no eval) and they fail when the shell's copy
// drifts from apps/web/lib/csp.ts, which the Next server sends as a response header.

import assert from "node:assert/strict";
import { test } from "node:test";

import { CSP_DIRECTIVES, contentSecurityPolicy, validateSenderFrame } from "../sender.js";
import { CONTENT_SECURITY_POLICY } from "../../web/lib/csp.ts";

const ORIGIN = "http://127.0.0.1:41234";

function frame(overrides = {}) {
	return { origin: ORIGIN, url: `${ORIGIN}/positions`, parent: null, ...overrides };
}

test("the shell policy and the web policy are the same string", () => {
	assert.equal(contentSecurityPolicy(), CONTENT_SECURITY_POLICY);
});

test("the policy allows only this origin for scripts, with no eval in production", () => {
	const policy = contentSecurityPolicy();
	assert.match(policy, /(^|; )default-src 'self'(;|$)/);
	assert.match(policy, /(^|; )script-src 'self' 'unsafe-inline'(;|$)/);
	assert.doesNotMatch(policy, /unsafe-eval/);
	assert.doesNotMatch(policy, /unsafe-inline[^;]*script-src/);
	assert.match(policy, /(^|; )object-src 'none'(;|$)/);
	assert.match(policy, /(^|; )frame-ancestors 'none'(;|$)/);
	assert.match(policy, /(^|; )base-uri 'self'(;|$)/);
});

test("no directive admits a wildcard or a remote host", () => {
	for (const directive of CSP_DIRECTIVES) {
		assert.doesNotMatch(directive, /\s\*(\s|$)/, `${directive} allows a wildcard`);
		assert.doesNotMatch(directive, /https?:\/\//, `${directive} names a remote origin`);
	}
});

test("the top frame on the app origin is trusted", () => {
	assert.deepEqual(validateSenderFrame(frame(), ORIGIN), {
		ok: true,
		reason: "top frame on the app origin",
	});
});

test("a null or missing frame is refused", () => {
	for (const value of [null, undefined, "frame", 42]) {
		const verdict = validateSenderFrame(value, ORIGIN);
		assert.equal(verdict.ok, false);
		assert.equal(verdict.reason, "no sender frame");
	}
});

test("a subframe is refused even on the app origin", () => {
	const verdict = validateSenderFrame(frame({ parent: { url: `${ORIGIN}/` } }), ORIGIN);
	assert.equal(verdict.ok, false);
	assert.match(verdict.reason, /subframe/);
});

test("a different origin is refused", () => {
	const foreign = frame({ origin: "http://127.0.0.1:9999", url: "http://127.0.0.1:9999/" });
	const verdict = validateSenderFrame(foreign, ORIGIN);
	assert.equal(verdict.ok, false);
	assert.match(verdict.reason, /is not http:\/\/127\.0\.0\.1:41234/);
});

test("a same-origin frame whose url moved off-origin is refused", () => {
	const verdict = validateSenderFrame(frame({ url: "https://example.com/" }), ORIGIN);
	assert.equal(verdict.ok, false);
	assert.match(verdict.reason, /sender url origin/);
});

test("a frame with an unparsable url is refused", () => {
	const verdict = validateSenderFrame(frame({ url: "" }), ORIGIN);
	assert.equal(verdict.ok, false);
	assert.match(verdict.reason, /is not a URL/);
});

test("a missing app origin refuses every sender", () => {
	for (const origin of ["", null, undefined]) {
		const verdict = validateSenderFrame(frame(), origin);
		assert.equal(verdict.ok, false);
		assert.equal(verdict.reason, "the app origin is not known");
	}
});
