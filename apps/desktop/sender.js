"use strict";

// The remote surface is a view, not a participant. This module is the whole boundary:
// it decides what a shell may ask a host it did not start, and it renders the read-only
// page that displays the answers. Nothing here imports Electron or touches the local
// database, so the refusal paths can be tested with the platform's plain test runner.

const ALLOWED_METHODS = Object.freeze(["GET", "HEAD"]);

// Only server-computed read models a workstation view needs. Approvals, execution, key
// management, plugin reload, mode changes and metrics are deliberately absent; a GET to
// them is refused just as a write method is. Each number stays computed by the host.
const READ_PATH_PREFIXES = Object.freeze([
	"/healthz",
	"/api/v1/agent/state",
	"/api/v1/agent/capabilities",
	"/api/v1/paper/account",
	"/api/v1/paper/positions",
	"/api/v1/paper/orders",
	"/api/v1/paper/fills",
	"/api/v1/paper/ledger",
	"/api/v1/paper/alerts",
	"/api/v1/system/kill-switch",
]);

// A shell sends no credential of its own. Any header that could carry the local
// deployment token, a venue key or a session cookie is dropped before a request leaves
// the process, even if a caller supplies one.
const CREDENTIAL_HEADER = /(authorization|cookie|token|secret|credential|api[-_]?key)/i;

class RemoteRequestDenied extends Error {
	constructor(code, message) {
		super(message);
		this.name = "RemoteRequestDenied";
		this.code = code;
	}
}

function deny(code, message) {
	throw new RemoteRequestDenied(code, message);
}

function normalizeHostUrl(raw) {
	if (typeof raw !== "string" || raw.trim() === "") {
		deny("HOST_URL_REQUIRED", "a host URL is required");
	}
	let parsed;
	try {
		parsed = new URL(raw.trim());
	} catch {
		deny("HOST_URL_INVALID", "the host URL is not a valid URL");
	}
	if (parsed.protocol !== "https:") {
		deny("HOST_URL_NOT_HTTPS", "a remote host must use HTTPS");
	}
	if (parsed.username || parsed.password) {
		deny("HOST_URL_HAS_CREDENTIALS", "a host URL must not contain credentials");
	}
	if (parsed.hash) {
		deny("HOST_URL_HAS_FRAGMENT", "a host URL must not contain a fragment");
	}
	if (!parsed.hostname) {
		deny("HOST_URL_INVALID", "the host URL has no hostname");
	}
	parsed.pathname = parsed.pathname.replace(/\/+$/, "");
	parsed.search = "";
	parsed.hash = "";
	return parsed.toString().replace(/\/$/, "");
}

function stripCredentials(headers) {
	const clean = {};
	if (!headers || typeof headers !== "object" || Array.isArray(headers)) return clean;
	for (const [name, value] of Object.entries(headers)) {
		if (CREDENTIAL_HEADER.test(name)) continue;
		if (typeof value === "string" && value.length <= 2048) clean[name] = value;
	}
	return clean;
}

function remoteUrl(baseUrl, path) {
	const base = normalizeHostUrl(baseUrl);
	if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
		deny("PATH_INVALID", "a read path must be an absolute path");
	}
	const target = new URL(path.slice(1), `${base}/`);
	if (target.origin !== new URL(base).origin) {
		deny("PATH_ESCAPES_HOST", "a read path must stay on the configured host");
	}
	return target.toString();
}

function buildRemoteRequest(options = {}) {
	const { baseUrl, method = "GET", path, headers = {} } = options;
	if (typeof method !== "string" || !ALLOWED_METHODS.includes(method.toUpperCase())) {
		deny("METHOD_NOT_READ_ONLY", `remote attach allows only ${ALLOWED_METHODS.join(" and ")}`);
	}
	// The URL parser does not decode %2f or %5c, so a path that merely starts with an
	// allowed prefix could reach a different endpoint once a host decodes it. Refuse
	// encoded separators and dot segments outright instead of trusting the host's router.
	if (typeof path === "string" && /%2f|%5c|%2e%2e/i.test(path)) {
		deny("PATH_ENCODED_TRAVERSAL", "a read path must not contain encoded separators or traversal segments");
	}
	const base = normalizeHostUrl(baseUrl);
	const url = remoteUrl(base, path);
	const basePath = new URL(base).pathname.replace(/\/+$/, "");
	const pathname = new URL(url).pathname;
	const relative = basePath && pathname.startsWith(basePath) ? pathname.slice(basePath.length) : pathname;
	const allowed = READ_PATH_PREFIXES.some(
		(prefix) => relative === prefix || relative.startsWith(`${prefix}/`),
	);
	if (!allowed) {
		deny("PATH_NOT_READ_ONLY", "the requested path is not on the read-only allowlist");
	}
	return {
		url,
		init: {
			method: method.toUpperCase(),
			headers: stripCredentials(headers),
			redirect: "error",
			cache: "no-store",
		},
	};
}

function hostProbeRequest(rawUrl) {
	return buildRemoteRequest({ baseUrl: rawUrl, path: "/healthz" });
}

// A role is a plan, not a mood: remote never spawns a sidecar, never generates a token and
// never starts a watchdog. The host owns those things, so closing a client or sleeping a
// laptop cannot stop a strategy that runs there.
function rolePlan(role) {
	if (role === "local") {
		return Object.freeze({ role: "local", spawnSidecars: true, generateToken: true, watchdog: true });
	}
	if (role === "remote") {
		return Object.freeze({ role: "remote", spawnSidecars: false, generateToken: false, watchdog: false });
	}
	deny("UNKNOWN_ROLE", "the workstation role must be local or remote");
}

// The first-run chooser. The operator decides here, without editing a file. A URL is
// tested before it is saved; an unreachable host is a retry state, not a blank window.
const FIRST_RUN_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Choose a workstation</title><style>
*{box-sizing:border-box}body{margin:0;background:#161615;color:#f2f2ef;font:14px/1.5 system-ui,sans-serif;padding:28px}
h1{font-size:20px;margin:0 0 4px}.lead{color:#a3a39c;margin:0 0 20px}
.card{border:1px solid #2c2c29;border-radius:8px;padding:16px;margin-bottom:14px}
h2{font-size:15px;margin:0 0 6px}.note{color:#a3a39c;font-size:12px;margin:0 0 12px}
button{background:#f2f2ef;color:#161615;border:0;border-radius:5px;padding:9px 14px;font-weight:600;cursor:pointer}
button[disabled]{opacity:.5;cursor:default}.row{display:flex;gap:8px;align-items:center}
input{flex:1;min-width:0;background:#232321;color:#f2f2ef;border:1px solid #2c2c29;border-radius:5px;padding:9px}
#status{margin-top:12px;font-size:13px;color:#f2c14e;min-height:20px}
</style></head><body>
<h1>Choose how this workstation runs</h1>
<p class="lead">A host keeps the ledger and the strategy engine. This window only displays them.</p>
<div class="card"><h2>Start a local host</h2>
<p class="note">Run the API and engine on this machine, as the app has always done. Closing the window keeps them in the tray.</p>
<button id="local">Use this machine</button></div>
<div class="card"><h2>Connect to a remote host</h2>
<p class="note">Attach read-only to a host you own. Its address is tested over HTTPS before it is saved.</p>
<div class="row"><input id="url" type="url" placeholder="https://host.example" spellcheck="false" autocomplete="off">
<button id="connect">Test and connect</button></div></div>
<div id="status" role="status" aria-live="polite"></div>
<script>
(function () {
  var bridge = window.ezchainDesktop;
  var status = document.getElementById("status");
  var url = document.getElementById("url");
  var connect = document.getElementById("connect");
  var local = document.getElementById("local");
  function say(text) { status.textContent = text; }
  local.addEventListener("click", function () {
    local.disabled = true; say("Starting the local host…");
    bridge.chooseLocal().catch(function (error) { local.disabled = false; say(String(error && error.message || error)); });
  });
  connect.addEventListener("click", function () {
    connect.disabled = true; say("Testing the connection…");
    bridge.remoteConnect(url.value).then(function () { say("Connected. Opening the read-only view…"); })
      .catch(function (error) { connect.disabled = false; say("Could not reach that host: " + String(error && error.message || error) + ". Check the address and try again."); });
  });
})();
</script></body></html>`;

// The remote window is this page, not the host's web app. It has no order form and no
// write path: every value comes through a read-only bridge call to an allowlisted path.
const REMOTE_VIEWER_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ezCHAIN remote workstation</title><style>
*{box-sizing:border-box}body{margin:0;background:#161615;color:#f2f2ef;font:14px/1.5 system-ui,sans-serif;padding:24px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:19px;margin:0}.mark{font:700 20px ui-monospace,monospace;letter-spacing:.08em}
.badge{border:1px solid #2c2c29;border-radius:999px;padding:4px 10px;font-size:12px;color:#a3a39c}
.banner{border:1px solid #2c2c29;background:#1d1d1b;border-radius:6px;padding:10px 12px;margin:16px 0;color:#a3a39c;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.panel{border:1px solid #2c2c29;border-radius:8px;padding:14px}
.panel h2{font-size:13px;color:#a3a39c;margin:0 0 10px;font-weight:600}
.stat{display:flex;justify-content:space-between;gap:12px;padding:3px 0}.stat b{font-weight:600}
.muted{color:#a3a39c}button{background:#f2f2ef;color:#161615;border:0;border-radius:5px;padding:7px 12px;font-weight:600;cursor:pointer}
.empty{color:#f2c14e}
</style></head><body>
<header><div><div class="mark">ezCHAIN</div><h1>Remote workstation</h1></div>
<div><span class="badge" id="host">read only</span> <button id="retry">Retry</button></div></header>
<p class="banner">Views are computed by the host. Approvals, execution and keys stay there; this window cannot send an order or read its credentials.</p>
<main class="grid" id="panels"></main>
<script>
(function () {
  var bridge = window.ezchainDesktop;
  var panels = document.getElementById("panels");
  var host = document.getElementById("host");
  var state = { host: "" };
  var UNAVAILABLE = { unavailable: true };
  function node(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }
  function panel(title, rows) {
    var box = node("section", "panel");
    box.appendChild(node("h2", "", title));
    if (rows === UNAVAILABLE) {
      box.appendChild(node("div", "muted", "Unavailable on this host"));
      return box;
    }
    rows.forEach(function (row) {
      var line = node("div", "stat");
      line.appendChild(node("span", "muted", row[0]));
      line.appendChild(node("b", "", row[1] === undefined || row[1] === null ? "—" : String(row[1])));
      box.appendChild(line);
    });
    return box;
  }
  function read(path) {
    return bridge.remoteRead(path).then(function (reply) { return JSON.parse(reply.body); });
  }
  // Each read model is isolated: one failed read (a 405 on kill-switch, say) blanks only
  // its own panel. The health read is the connection probe; only its failure, or a bridge
  // failure, replaces the whole view with the retry state.
  function readPanel(path) {
    return read(path).catch(function () { return UNAVAILABLE; });
  }
  function render() {
    return Promise.all([
      read("/healthz"),
      readPanel("/api/v1/agent/state"),
      readPanel("/api/v1/paper/account"),
      readPanel("/api/v1/paper/positions"),
      readPanel("/api/v1/paper/fills"),
      readPanel("/api/v1/system/kill-switch"),
    ]).then(function (values) {
      var health = values[0], agent = values[1], account = values[2];
      var positions = values[3], fills = values[4], kill = values[5];
      var list = positions === UNAVAILABLE || !Array.isArray(positions) ? [] : positions;
      var recent = fills === UNAVAILABLE || !Array.isArray(fills) ? [] : fills;
      var mode = (agent !== UNAVAILABLE && agent && agent.mode) ||
        (account !== UNAVAILABLE && account && account.mode) || "unknown";
      panels.replaceChildren(
        panel("Host", [["Status", health && health.status || "unknown"], ["Mode", mode]]),
        panel("Paper account", account === UNAVAILABLE ? UNAVAILABLE : [["Equity", account && account.equity_usd], ["Cash", account && account.cash_usd], ["Realized", account && account.realized_usd]]),
        panel("Positions", positions === UNAVAILABLE || fills === UNAVAILABLE ? UNAVAILABLE : [["Open", list.length], ["Fills shown", recent.length]]),
        panel("Kill switch", kill === UNAVAILABLE ? UNAVAILABLE : [["Armed", kill && kill.armed], ["Updated", kill && kill.updated_at]]),
      );
      host.textContent = "Connected · read only · " + (state.host || "host");
    }).catch(function () {
      host.textContent = "Host unreachable";
      panels.replaceChildren(node("p", "empty", "The host could not be reached. No strategy on the host was stopped. Check the connection and retry."));
    });
  }
  document.getElementById("retry").addEventListener("click", render);
  bridge.remoteConfig().then(function (config) {
    state.host = config && config.url || "";
    return render();
  }).catch(function () { render(); });
  setInterval(render, 15000);
})();
</script></body></html>`;

module.exports = {
	ALLOWED_METHODS,
	READ_PATH_PREFIXES,
	RemoteRequestDenied,
	FIRST_RUN_HTML,
	REMOTE_VIEWER_HTML,
	buildRemoteRequest,
	hostProbeRequest,
	normalizeHostUrl,
	rolePlan,
	stripCredentials,
};
