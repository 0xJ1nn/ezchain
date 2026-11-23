"use strict";

// The remote surface is a view, not a participant. This module is the whole boundary:
// it decides what a shell may ask a host it did not start, and it renders the read-only
// page that displays the answers. It also holds the first-run setup decisions and the
// consistent-copy backup as pure functions over paths, so both the refusal paths and the
// setup can be tested with the platform's plain test runner. Nothing here imports Electron.

const fs = require("node:fs");
const path = require("node:path");

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

// Reconfiguration is a trusted operation. The dashboard loads the web app and the setup
// window loads a local page, both from data- or loopback URLs, so an origin check cannot
// tell them apart. A WebContents id can: only the window the supervisor created to hold
// the setup page may change the role, stop the host or move its data.
function isSetupSender(senderId, setupWindowId) {
	return Number.isInteger(setupWindowId) && senderId === setupWindowId;
}

// Re-applying a setup that owned a local host must stop its sidecars and watchdog before
// new ones start. The ports are ephemeral, so without a teardown the old pair is orphaned
// with the ledger still open and no watchdog watching it. A clean boot owns nothing.
function needsLocalTeardown(previousRole, running) {
	return (
		previousRole === "local" ||
		Boolean(running && (running.apiChild || running.webChild || running.watchdog))
	);
}

// First-run setup. host.json in Electron's user data directory is the single home for
// every decision. The data directory, token location and backup directory are values in
// that one file; the supervisor derives every runtime path from them and hands the same
// paths to the API. The token's path is shown to the operator, its value never leaves the
// supervisor and is never a member of a summary or a log line.
const SETUP_MODE = "paper";

class SetupError extends Error {
	constructor(code, message) {
		super(message);
		this.name = "SetupError";
		this.code = code;
	}
}

// A clean profile. The data directory defaults to Electron's user data directory and the
// backup directory lives inside it, so a first run has paths that already exist.
function initialSetup(configDir) {
	const home = path.resolve(configDir);
	return Object.freeze({
		mode: SETUP_MODE,
		dataDir: home,
		backupDir: path.join(home, "backups"),
		role: "local",
		url: null,
		configured: false,
	});
}

// Merge a saved choice over the defaults. A profile written before setup existed held only
// a role and a URL; those still resolve. A saved live request is ignored because v1 offers
// paper only, and the API is pinned to paper independently in backendEnv.
function normalizeSetup(saved, configDir) {
	const fallback = initialSetup(configDir);
	if (!saved || typeof saved !== "object" || Array.isArray(saved)) return fallback;
	let role = saved.role === undefined || saved.role === null ? "local" : saved.role;
	rolePlan(role);
	let url = null;
	if (role === "remote") {
		if (typeof saved.url !== "string") {
			throw new SetupError("SETUP_URL_REQUIRED", "a remote setup needs a host URL");
		}
		url = normalizeHostUrl(saved.url);
	}
	const dataDir =
		typeof saved.dataDir === "string" && saved.dataDir.trim()
			? path.resolve(saved.dataDir)
			: fallback.dataDir;
	const backupDir =
		typeof saved.backupDir === "string" && saved.backupDir.trim()
			? path.resolve(saved.backupDir)
			: path.join(dataDir, "backups");
	return Object.freeze({ mode: SETUP_MODE, dataDir, backupDir, role, url, configured: true });
}

// Every path the supervisor and the API use is derived here, from the one configuration.
// Logs stay beside the configuration because the supervisor writes them before setup.
function setupPaths(setup, configDir) {
	const home = path.resolve(configDir);
	const dataDir = path.resolve(setup.dataDir);
	return Object.freeze({
		configDir: home,
		configFile: path.join(home, "host.json"),
		dataDir,
		dbFile: path.join(dataDir, "ezchain.db"),
		tokenFile: path.join(dataDir, "api-token"),
		backupDir: path.resolve(setup.backupDir),
		logsDir: path.join(home, "logs"),
		logFile: path.join(home, "logs", "desktop.log"),
		runtimeRoot: path.join(dataDir, "runtime"),
	});
}

// The values the screen displays. There is deliberately no token value here: the screen
// can say where the token is stored without ever holding what is stored there.
function setupSummary(setup, paths) {
	return Object.freeze({
		mode: setup.mode,
		dataDir: paths.dataDir,
		dbPath: paths.dbFile,
		tokenPath: paths.tokenFile,
		backupDir: paths.backupDir,
		configPath: paths.configFile,
		role: setup.role,
		url: setup.url || null,
	});
}

// The environment the API is started with. The database URL, backup directory and
// execution mode come from the same setup that produced the displayed paths, so a value
// the operator approved cannot differ from a value the API receives.
function backendEnv(setup, paths, ports, token) {
	if (!setup || setup.mode !== SETUP_MODE) {
		throw new SetupError("SETUP_MODE_INVALID", "only paper mode is supported in this release");
	}
	return {
		EZCHAIN_ENVIRONMENT: "desktop",
		EZCHAIN_DATABASE_URL: `sqlite+pysqlite:///${paths.dbFile}`,
		EZCHAIN_AUTO_CREATE_SCHEMA: "true",
		EZCHAIN_AUTH_ENABLED: "true",
		EZCHAIN_API_TOKEN: token,
		EZCHAIN_EXECUTION_MODE: SETUP_MODE,
		EZCHAIN_BACKUP_DIR: paths.backupDir,
		EZCHAIN_DATA_MODE: "live", // live market data, not live execution
		EZCHAIN_ENGINE_INLINE: "true",
		EZCHAIN_ENGINE_INTERVAL_SECONDS: "30",
		EZCHAIN_MARKET_SCAN_INLINE: "true",
		EZCHAIN_MARKET_SCAN_INTERVAL_SECONDS: "90",
		EZCHAIN_LOG_LEVEL: "INFO",
		EZCHAIN_DESKTOP_API_PORT: String(ports.api),
		EZCHAIN_PUBLIC_URL: `http://127.0.0.1:${ports.web}`,
		EZCHAIN_CORS_ORIGINS: JSON.stringify([`http://127.0.0.1:${ports.web}`]),
	};
}

// A backup path is checked when it is chosen, not at the first backup. The reason travels
// back to the screen so the operator can fix the exact cause.
function validateBackupDir(raw) {
	if (typeof raw !== "string" || raw.trim() === "") {
		return { ok: false, reason: "a backup path is required" };
	}
	const dir = path.resolve(raw);
	try {
		if (!fs.statSync(dir).isDirectory()) {
			return { ok: false, reason: `backup path is not a directory: ${dir}` };
		}
		fs.accessSync(dir, fs.constants.W_OK);
		return { ok: true, path: dir };
	} catch (error) {
		const detail = error && error.code === "ENOENT" ? "backup path does not exist" : String(error && error.message || error);
		return { ok: false, reason: `${detail}: ${dir}` };
	}
}

// A consistent copy is made through SQLite's own VACUUM INTO: a read-only connection to
// the live database writes a compact, standalone snapshot that restores by replacing the
// database file. No table is copied by hand, so WAL state is folded in and the copy opens
// with its page structure intact.
function backupDatabase({ dbFile, backupDir, now = new Date() }) {
	const check = validateBackupDir(backupDir);
	if (!check.ok) throw new Error(`backup refused: ${check.reason}`);
	if (typeof dbFile !== "string" || !fs.existsSync(dbFile)) {
		throw new Error(`backup refused: no database at ${dbFile}`);
	}
	const stamp = now.toISOString().replace(/\.\d{3}Z$/, "Z").replace(/[-:]/g, "");
	let file = path.join(check.path, `ezchain-${stamp}.db`);
	for (let suffix = 1; fs.existsSync(file); suffix += 1) {
		file = path.join(check.path, `ezchain-${stamp}-${suffix}.db`);
	}
	const { DatabaseSync } = require("node:sqlite");
	const db = new DatabaseSync(dbFile, { readOnly: true });
	let snapshot = null;
	try {
		db.prepare("VACUUM INTO ?").run(file);
		// VACUUM INTO makes the copy consistent and folds WAL state into it. Check the
		// result before reporting it, so a bad snapshot fails here rather than on restore.
		snapshot = new DatabaseSync(file, { readOnly: true });
		const check = snapshot.prepare("PRAGMA integrity_check").get();
		if (!check || check.integrity_check !== "ok") {
			throw new Error(`backup snapshot failed integrity_check: ${check && check.integrity_check}`);
		}
	} catch (error) {
		try {
			fs.rmSync(file, { force: true });
		} catch {
			// Removing a partial copy is best-effort; the original error is the cause.
		}
		throw error;
	} finally {
		if (snapshot) snapshot.close();
		db.close();
	}
	return { file, bytes: fs.statSync(file).size };
}

// The setup screen. It shows the resolved values, never the token value, and checks a
// chosen backup path before anything is saved. The main process owns generation, storage
// and validation; this page only collects choices and reports what the supervisor did.
const FIRST_RUN_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Set up ezCHAIN</title><style>
:root{color-scheme:dark;--bg:#08090b;--panel:#0e1013;--line:#2e343b;--text:#f3f5f7;--muted:#aab1bb;--green:#1fd897;--amber:#f3b53f;--mono:ui-monospace,"SF Mono",monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,sans-serif;padding:24px;max-width:660px}
h1{font-size:20px;margin:0 0 4px}.lead{color:var(--muted);margin:0 0 18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:12px}
h2{font-size:12px;color:var(--muted);margin:0 0 8px;text-transform:uppercase;letter-spacing:.06em}
.value{font-family:var(--mono);font-size:12px;overflow-wrap:anywhere}
.note{color:var(--muted);font-size:12px;margin:6px 0 0}
button{background:var(--green);color:#04130d;border:0;border-radius:6px;padding:9px 14px;font-weight:600;cursor:pointer}
button.secondary{background:var(--panel);color:var(--text);border:1px solid var(--line)}
button[disabled]{opacity:.5;cursor:default}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input[type=text],input[type=url]{flex:1;min-width:220px;background:#13161a;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:9px;font:12px var(--mono)}
label{display:block;font-size:12px;color:var(--muted);margin-top:10px}
.pill{display:inline-block;border:1px solid var(--green);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--green)}
#status{margin-top:12px;font-size:13px;color:var(--amber);min-height:20px}#status.ok{color:var(--green)}
#resolved{font:12px/1.6 var(--mono);color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere}
</style></head><body>
<h1>Set up this workstation</h1>
<p class="lead">These choices are made once. The same file is what the app reads on every start.</p>
<div class="card"><h2>Mode</h2><span class="pill">PAPER</span>
<p class="note">Paper trading only in this release. Live mode is not offered here and no key material is created.</p></div>
<div class="card"><h2>Data directory</h2>
<label for="data">Directory for the database and deployment token</label>
<div class="row"><input id="data" type="text" spellcheck="false" autocomplete="off"></div>
<p class="note">Resolved path: <span class="value" id="data-resolved"></span></p></div>
<div class="card"><h2>Backup path</h2>
<label for="backup">Directory for consistent SQLite snapshots</label>
<div class="row"><input id="backup" type="text" spellcheck="false" autocomplete="off"><button class="secondary" id="back-up">Back up now</button></div>
<p class="note">Resolved path: <span class="value" id="backup-resolved"></span></p>
<p class="note" id="backup-result" role="status" aria-live="polite"></p></div>
<div class="card"><h2>Connection</h2>
<label class="row"><input type="radio" name="role" value="local" checked><span>Use this machine</span></label>
<label class="row"><input type="radio" name="role" value="remote"><span>Connect to a host</span></label>
<div class="row"><input id="url" type="url" placeholder="https://host.example" spellcheck="false" autocomplete="off"></div></div>
<div class="card"><h2>Resolved configuration</h2><div id="resolved"></div>
<p class="note">The deployment token is generated at setup, stored at the path above, and never displayed; its value stays in the supervisor and is never written to a log.</p></div>
<div class="row"><button id="finish">Finish setup</button></div>
<div id="status" role="status" aria-live="polite"></div>
<script>
(function () {
  var bridge = window.ezchainDesktop;
  var data = document.getElementById("data");
  var backup = document.getElementById("backup");
  var url = document.getElementById("url");
  var status = document.getElementById("status");
  var resolved = document.getElementById("resolved");
  var backupResult = document.getElementById("backup-result");
  var finish = document.getElementById("finish");
  var backUp = document.getElementById("back-up");
  function say(text, ok) { status.textContent = text; status.className = ok ? "ok" : ""; }
  function role() { return document.querySelector('input[name="role"]:checked').value; }
  function draft() { return { dataDir: data.value, backupDir: backup.value, role: role(), url: url.value }; }
  function show(summary) {
    document.getElementById("data-resolved").textContent = summary.dataDir;
    document.getElementById("backup-resolved").textContent = summary.backupDir;
    resolved.textContent =
      "Mode: " + summary.mode + "\n" +
      "Data directory: " + summary.dataDir + "\n" +
      "Database: " + summary.dbPath + "\n" +
      "Deployment token: stored at " + summary.tokenPath + "\n" +
      "Backup path: " + summary.backupDir + "\n" +
      "Connection: " + (summary.role === "remote" ? summary.url : "this machine");
  }
  function preview() { return bridge.setupPreview(draft()).then(show); }
  [data, backup, url].forEach(function (el) { el.addEventListener("input", function () { preview().catch(function () {}); }); });
  document.querySelectorAll('input[name="role"]').forEach(function (el) { el.addEventListener("change", preview); });
  bridge.setupPreview({}).then(function (summary) {
    data.value = summary.dataDir;
    backup.value = summary.backupDir;
    url.value = summary.url || "";
    if (summary.role === "remote") document.querySelector('input[value="remote"]').checked = true;
    return show(summary);
  }).catch(function (error) { say(String(error && error.message || error)); });
  backUp.addEventListener("click", function () {
    backUp.disabled = true; backupResult.textContent = "";
    bridge.backupNow().then(function (r) {
      backupResult.textContent = "Backup: " + r.file + " (" + r.bytes + " bytes)";
    }).catch(function (error) { backupResult.textContent = "Backup refused: " + String(error && error.message || error); })
      .finally(function () { backUp.disabled = false; });
  });
  finish.addEventListener("click", function () {
    finish.disabled = true; say("Saving setup…");
    bridge.finishSetup(draft()).then(function () { say("Setup saved. Opening the dashboard…", true); })
      .catch(function (error) { finish.disabled = false; say(String(error && error.message || error)); });
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
	SetupError,
	SETUP_MODE,
	FIRST_RUN_HTML,
	REMOTE_VIEWER_HTML,
	backupDatabase,
	backendEnv,
	buildRemoteRequest,
	hostProbeRequest,
	initialSetup,
	isSetupSender,
	needsLocalTeardown,
	normalizeHostUrl,
	normalizeSetup,
	rolePlan,
	setupPaths,
	setupSummary,
	stripCredentials,
	validateBackupDir,
};
