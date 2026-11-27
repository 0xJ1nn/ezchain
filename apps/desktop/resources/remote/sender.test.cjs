"use strict";

// Refusal paths for the remote attach. A shell is a view: it must never generate a
// credential, never send a write, and never render a window that can place an order.

const assert = require("node:assert/strict");
const test = require("node:test");
const sender = require("../../sender.js");

const HOST = "https://host.example";
const TEST_BEARER = `Bearer ${"TEST_TOKEN_DO_NOT_USE"}`;

function denied(code, fn) {
	assert.throws(fn, (error) => {
		assert.equal(error.name, "RemoteRequestDenied");
		assert.equal(error.code, code);
		return true;
	});
}

test("a remote role owns no sidecar, token or watchdog", () => {
	const local = sender.rolePlan("local");
	assert.equal(local.spawnSidecars, true);
	assert.equal(local.generateToken, true);
	assert.equal(local.watchdog, true);

	const remote = sender.rolePlan("remote");
	assert.equal(remote.spawnSidecars, false);
	assert.equal(remote.generateToken, false);
	assert.equal(remote.watchdog, false);

	denied("UNKNOWN_ROLE", () => sender.rolePlan("unknown"));
});

test("a host URL is HTTPS and carries no embedded credential", () => {
	assert.equal(sender.normalizeHostUrl(`${HOST}/`), HOST);
	assert.equal(sender.normalizeHostUrl(`${HOST}/ez/`), `${HOST}/ez`);

	denied("HOST_URL_NOT_HTTPS", () => sender.normalizeHostUrl("http://host.example"));
	denied("HOST_URL_HAS_CREDENTIALS", () => sender.normalizeHostUrl("https://user:pass@host.example"));
	denied("HOST_URL_HAS_FRAGMENT", () => sender.normalizeHostUrl(`${HOST}/#part`));
	denied("HOST_URL_INVALID", () => sender.normalizeHostUrl("not a url"));
	denied("HOST_URL_REQUIRED", () => sender.normalizeHostUrl(""));
});

test("the connection probe is a read-only GET to the health endpoint", () => {
	const probe = sender.hostProbeRequest(`${HOST}/`);
	assert.equal(probe.url, `${HOST}/healthz`);
	assert.equal(probe.init.method, "GET");
	assert.equal(probe.init.redirect, "error");
});

test("a remote client cannot place an unapproved order", () => {
	for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
		denied("METHOD_NOT_READ_ONLY", () =>
			sender.buildRemoteRequest({ baseUrl: HOST, method, path: "/api/v1/paper/orders" }),
		);
	}
	denied("METHOD_NOT_READ_ONLY", () =>
		sender.buildRemoteRequest({ baseUrl: HOST, method: "post", path: "/api/v1/paper/orders" }),
	);

	// Even a read of the order list is pinned to the allowlist, and approving an order
	// is not a read at all.
	const orders = sender.buildRemoteRequest({ baseUrl: HOST, path: "/api/v1/paper/orders" });
	assert.equal(orders.url, `${HOST}/api/v1/paper/orders`);
	denied("PATH_NOT_READ_ONLY", () =>
		sender.buildRemoteRequest({ baseUrl: HOST, path: "/api/v1/paper/approvals/1/approve" }),
	);
});

test("a remote client cannot read credentials or operator-only state", () => {
	for (const path of [
		"/api/v1/keys",
		"/api/v1/provider-keys",
		"/api/v1/agent/keys",
		"/api/v1/plugins/reload",
		"/metrics",
		"/api/v1/trade-policy",
	]) {
		denied("PATH_NOT_READ_ONLY", () => sender.buildRemoteRequest({ baseUrl: HOST, path }));
	}
	for (const path of sender.READ_PATH_PREFIXES) {
		assert.ok(!/keys|metrics|approvals|reload|kill-switch$/.test(path) || path.endsWith("kill-switch"));
	}
});

test("encoded separators and traversal cannot bypass the read allowlist", () => {
	// The URL parser leaves %2f and %5c encoded, so a path that starts with an allowed
	// prefix still passes a raw prefix match even though a host may decode it elsewhere.
	for (const path of [
		"/api/v1/paper/account/..%2f..%2fadmin",
		"/api/v1/paper/account/%2F..%2F..%2Fapi%2Fv1%2Fkeys",
		"/api/v1/paper/account/..%5c..%5cadmin",
		"/api/v1/paper/account/%2e%2e/%2e%2e/api/v1/keys",
	]) {
		denied("PATH_ENCODED_TRAVERSAL", () => sender.buildRemoteRequest({ baseUrl: HOST, path }));
	}
});

test("no local credential is ever attached to a remote request", () => {
	const request = sender.buildRemoteRequest({
		baseUrl: HOST,
		path: "/api/v1/paper/account",
		headers: {
			accept: "application/json",
			authorization: TEST_BEARER,
			"x-api-key": "TEST_KEY_DO_NOT_USE",
			cookie: "session=TEST_SESSION_DO_NOT_USE",
		},
	});
	assert.deepEqual(request.init.headers, { accept: "application/json" });
	assert.equal(Object.keys(request.init.headers).some((name) => /auth|token|key|cookie/i.test(name)), false);
	assert.deepEqual(sender.stripCredentials({ "X-Auth-Token": "TEST_VALUE_DO_NOT_USE" }), {});
});

test("a read path cannot escape the configured host or its prefix", () => {
	denied("PATH_INVALID", () => sender.buildRemoteRequest({ baseUrl: HOST, path: "//other.example/x" }));
	denied("PATH_INVALID", () => sender.buildRemoteRequest({ baseUrl: HOST, path: "api/v1/paper/account" }));
	// The URL parser keeps an at-sign path on the configured host, and the allowlist
	// still refuses it; either way no request is aimed at another origin.
	denied("PATH_NOT_READ_ONLY", () =>
		sender.buildRemoteRequest({ baseUrl: HOST, path: "/@other.example/api/v1/paper/account" }),
	);
	const prefixed = sender.buildRemoteRequest({
		baseUrl: `${HOST}/ez`,
		path: "/api/v1/paper/account",
	});
	assert.equal(prefixed.url, `${HOST}/ez/api/v1/paper/account`);
});

test("the read-only views contain no write path and no credential", () => {
	for (const html of [sender.FIRST_RUN_HTML, sender.REMOTE_VIEWER_HTML]) {
		assert.equal(/<form\b/i.test(html), false);
		assert.equal(/\bPOST\b/.test(html), false);
		assert.equal(/api-token|api[_-]?key|authorization|bearer/i.test(html), false);
		assert.equal(/fetch\s*\(/.test(html), false);
	}
	assert.match(sender.REMOTE_VIEWER_HTML, /remoteRead\(/);
	// Each read model is isolated: one non-200 response must not blank the whole view.
	assert.match(sender.REMOTE_VIEWER_HTML, /readPanel\(/);
	assert.match(sender.REMOTE_VIEWER_HTML, /Host unreachable/);
	assert.match(sender.REMOTE_VIEWER_HTML, /Retry/);
	assert.match(sender.FIRST_RUN_HTML, /finishSetup\(/);
	assert.match(sender.FIRST_RUN_HTML, /backupNow\(/);
});
