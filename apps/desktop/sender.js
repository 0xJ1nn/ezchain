"use strict";

// Security primitives for the desktop shell, deliberately free of Electron so node:test
// can exercise them: the Content-Security-Policy applied to the app's own pages, and the
// predicate that decides whether an IPC message came from the app's own top frame.
//
// The policy must stay identical to apps/web/lib/csp.ts, which the Next server sends as a
// response header. apps/desktop/test/sender-validation.mjs fails when the two drift.

// Mirrors apps/web/lib/csp.ts directive for directive. 'unsafe-inline' in script-src is
// required by the App Router's inline bootstrap/flight scripts; production has no eval.
const CSP_DIRECTIVES = [
	"default-src 'self'",
	"script-src 'self' 'unsafe-inline'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self'",
	"connect-src 'self'",
	"object-src 'none'",
	"base-uri 'self'",
	"form-action 'self'",
	"frame-ancestors 'none'",
];

/** The Content-Security-Policy applied to the renderer's own responses. */
function contentSecurityPolicy() {
	return CSP_DIRECTIVES.join("; ");
}

/**
 * Decide whether a sender frame may invoke a privileged IPC handler.
 *
 * `frame` is an Electron WebFrameMain (or any object with the same shape). A trusted
 * sender is the app's top-level frame: no parent frame, an origin and a URL both on the
 * loopback origin the sidecars are bound to. A null frame, a foreign origin and a subframe
 * are each refused with a reason the caller logs.
 */
function validateSenderFrame(frame, expectedOrigin) {
	if (!frame || typeof frame !== "object") {
		return { ok: false, reason: "no sender frame" };
	}
	if (typeof expectedOrigin !== "string" || expectedOrigin.length === 0) {
		return { ok: false, reason: "the app origin is not known" };
	}
	if (frame.parent) {
		return { ok: false, reason: "sender is a subframe" };
	}
	if (typeof frame.origin !== "string" || frame.origin !== expectedOrigin) {
		return {
			ok: false,
			reason: `sender origin ${String(frame.origin)} is not ${expectedOrigin}`,
		};
	}
	let url;
	try {
		url = new URL(String(frame.url));
	} catch {
		return { ok: false, reason: `sender url ${String(frame.url)} is not a URL` };
	}
	if (url.origin !== expectedOrigin) {
		return { ok: false, reason: `sender url origin ${url.origin} is not ${expectedOrigin}` };
	}
	return { ok: true, reason: "top frame on the app origin" };
}

module.exports = { CSP_DIRECTIVES, contentSecurityPolicy, validateSenderFrame };
