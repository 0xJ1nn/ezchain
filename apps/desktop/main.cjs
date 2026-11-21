"use strict";

// ezCHAIN desktop supervisor.
//
// The app owns two private sidecars: the FastAPI paper API on an embedded SQLite database
// and the traced Next.js server. Both bind to 127.0.0.1 on ephemeral ports and are never
// exposed. Closing the window hides it to the tray; the services keep running and are
// restarted if they crash. Quit stops them.

const {
	app,
	BrowserWindow,
	Menu,
	Tray,
	dialog,
	ipcMain,
	nativeImage,
	session,
	shell,
	utilityProcess,
} = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { contentSecurityPolicy, validateSenderFrame } = require("./sender");

const APP_NAME = "ezCHAIN";
const isDev = !app.isPackaged;
app.setName(APP_NAME);
// Chromium's sandbox for every renderer, including any frame rendered before a
// BrowserWindow's own webPreferences are read.
app.enableSandbox();
const dataDir = app.getPath("userData");
const logsDir = path.join(dataDir, "logs");
const logFile = path.join(logsDir, "desktop.log");
const dbFile = path.join(dataDir, "ezchain.db");
const tokenFile = path.join(dataDir, "api-token");
const iconPath = isDev
	? path.join(__dirname, "resources", "icon.png")
	: path.join(process.resourcesPath, "icon.png");
const resourcesRoot = isDev ? path.join(__dirname, "build") : process.resourcesPath;
const backendBin = path.join(resourcesRoot, "backend", "ezchain-api", "ezchain-api");
const webTar = path.join(resourcesRoot, "web.tar.gz");
const webRoot = isDev
	? path.join(resourcesRoot, "web")
	: path.join(dataDir, "runtime", `web-${app.getVersion()}`);
const webServer = path.join(webRoot, "apps", "web", "server.js");

// One-time migration from the early package-name data directory. The database and token
// are the only durable state; logs can start fresh.
const legacyDataDir = path.join(app.getPath("appData"), "@ezchain", "desktop");
if (
	dataDir !== legacyDataDir &&
	!fs.existsSync(path.join(dataDir, "ezchain.db")) &&
	fs.existsSync(path.join(legacyDataDir, "ezchain.db"))
) {
	fs.mkdirSync(dataDir, { recursive: true });
	for (const name of ["ezchain.db", "ezchain.db-wal", "ezchain.db-shm", "api-token"]) {
		const source = path.join(legacyDataDir, name);
		if (fs.existsSync(source)) fs.copyFileSync(source, path.join(dataDir, name));
	}
}

fs.mkdirSync(logsDir, { recursive: true });
try {
	if (fs.statSync(logFile).size > 5 * 1024 * 1024) {
		fs.renameSync(logFile, `${logFile}.1`);
	}
} catch {
	// First run or a rotated log that does not exist yet.
}
let logStream = fs.createWriteStream(logFile, { flags: "a" });
function rotateLog() {
	try {
		if (fs.statSync(logFile).size <= 5 * 1024 * 1024) return;
		logStream.end();
		fs.renameSync(logFile, `${logFile}.1`);
		logStream = fs.createWriteStream(logFile, { flags: "a" });
	} catch {
		// Rotation is best-effort; a failed rename must not stop the supervisor.
	}
}
function log(message) {
	const line = `${new Date().toISOString()} ${message}`;
	logStream.write(`${line}\n`);
	if (isDev) console.log(line);
}

// Every response the app makes — the renderer document, its inline bootstrap scripts and
// all subresources — carries the shared policy as a header, so it is enforced even if the
// Next server's own copy were compiled out. The policy is the production one from
// apps/desktop/sender.js (mirrors apps/web/lib/csp.ts); the app has no eval and no remote
// origin.
function appOrigin() {
	return webPort ? `http://127.0.0.1:${webPort}` : null;
}

const SECURITY_HEADERS = {
	"Content-Security-Policy": contentSecurityPolicy(),
	"X-Content-Type-Options": "nosniff",
	"Referrer-Policy": "same-origin",
	"X-Frame-Options": "DENY",
};
const SECURITY_HEADERS_LOWER = new Set(
	Object.keys(SECURITY_HEADERS).map((name) => name.toLowerCase()),
);

function hardenSession() {
	const ses = session.defaultSession;
	ses.webRequest.onHeadersReceived((details, callback) => {
		// Header names are case-insensitive. Drop any existing copy first so the policy is
		// sent once, with our value, rather than duplicated or overridden by the origin.
		const responseHeaders = {};
		for (const [name, value] of Object.entries(details.responseHeaders ?? {})) {
			const lower = name.toLowerCase();
			if (SECURITY_HEADERS_LOWER.has(lower)) continue;
			responseHeaders[name] = value;
		}
		for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
			responseHeaders[name] = [value];
		}
		callback({ responseHeaders });
	});
	// The app needs no camera, microphone, geolocation, notifications, clipboard read or
	// any other permission prompt: deny every request and every check.
	ses.setPermissionRequestHandler((_contents, permission, callback) => {
		log(`permission denied: ${permission}`);
		callback(false);
	});
	ses.setPermissionCheckHandler(() => false);
}

// Only the app's own top frame may invoke a privileged IPC handler. A null frame, a
// subframe or another origin is refused and logged; the renderer gets a rejection, not an
// action. `event.senderFrame` is the authoritative frame, not `event.sender`.
function handleFromApp(channel, handler) {
	ipcMain.handle(channel, async (event, ...args) => {
		const verdict = validateSenderFrame(event.senderFrame, appOrigin());
		if (!verdict.ok) {
			log(`ipc ${channel} refused: ${verdict.reason}`);
			throw new Error("refused: sender is not the app window");
		}
		return handler(event, ...args);
	});
}

let mainWindow = null;
let splash = null;
let tray = null;
let apiPort = null;
let webPort = null;
let apiToken = null;
let apiChild = null;
let webChild = null;
let starting = false;
let restarting = false;
let quitting = false;
let watchdogTimer = null;
const intentionalStop = { api: false, web: false };
const restartTimers = { api: null, web: null };
let apiBackoff = 1000;
let webBackoff = 1000;

function loadToken() {
	try {
		const existing = fs.readFileSync(tokenFile, "utf8").trim();
		if (existing.length >= 32 && existing.isWellFormed()) return existing;
	} catch {
		// Generate below.
	}
	const token = crypto.randomBytes(32).toString("hex");
	fs.writeFileSync(tokenFile, token, { mode: 0o600 });
	fs.chmodSync(tokenFile, 0o600);
	return token;
}

function freePort() {
	return new Promise((resolve, reject) => {
		const server = net.createServer();
		server.once("error", reject);
		server.listen(0, "127.0.0.1", () => {
			const address = server.address();
			const port = typeof address === "object" && address ? address.port : null;
			server.close(() => (port ? resolve(port) : reject(new Error("no port"))));
		});
	});
}

function databaseUrl() {
	return `sqlite+pysqlite:///${dbFile}`;
}

function childEnv(extra) {
	return {
		...process.env,
		PYTHONUNBUFFERED: "1",
		PYTHONDONTWRITEBYTECODE: "1",
		...extra,
	};
}

function pipeChild(name, child) {
	child.stdout?.on("data", (chunk) => log(`[${name}] ${chunk.toString().trimEnd()}`));
	child.stderr?.on("data", (chunk) => log(`[${name}] ${chunk.toString().trimEnd()}`));
	child.on("error", (error) => log(`[${name}] spawn error: ${error.message}`));
}

function spawnBackend() {
	if (!fs.existsSync(backendBin)) {
		throw new Error(`backend sidecar missing at ${backendBin}`);
	}
	const child = spawn(backendBin, [], {
		cwd: dataDir,
		env: childEnv({
			EZCHAIN_ENVIRONMENT: "desktop",
			EZCHAIN_DATABASE_URL: databaseUrl(),
			EZCHAIN_AUTO_CREATE_SCHEMA: "true",
			EZCHAIN_AUTH_ENABLED: "true",
			EZCHAIN_API_TOKEN: apiToken,
			EZCHAIN_DATA_MODE: "live",
			EZCHAIN_ENGINE_INLINE: "true",
			EZCHAIN_ENGINE_INTERVAL_SECONDS: "30",
			EZCHAIN_MARKET_SCAN_INLINE: "true",
			EZCHAIN_MARKET_SCAN_INTERVAL_SECONDS: "90",
			EZCHAIN_LOG_LEVEL: "INFO",
			EZCHAIN_DESKTOP_API_PORT: String(apiPort),
			EZCHAIN_PUBLIC_URL: `http://127.0.0.1:${webPort}`,
			EZCHAIN_CORS_ORIGINS: JSON.stringify([`http://127.0.0.1:${webPort}`]),
		}),
		stdio: ["ignore", "pipe", "pipe"],
	});
	apiChild = child;
	pipeChild("api", child);
	child.on("exit", (code, signal) => {
		log(`api exited code=${code} signal=${signal}`);
		if (apiChild === child) apiChild = null;
		if (intentionalStop.api) {
			intentionalStop.api = false;
			return;
		}
		if (!quitting) scheduleRestart("api");
	});
}

function ensureWebRuntime() {
	if (isDev) return;
	const marker = path.join(webRoot, ".complete");
	if (fs.existsSync(webServer) && fs.existsSync(marker)) return;
	if (!fs.existsSync(webTar)) {
		throw new Error(`web runtime archive missing at ${webTar}`);
	}
	const temp = `${webRoot}.tmp`;
	fs.rmSync(temp, { recursive: true, force: true });
	fs.mkdirSync(temp, { recursive: true });
	log(`extracting web runtime to ${webRoot}`);
	const result = require("node:child_process").spawnSync("tar", ["-xzf", webTar, "-C", temp], {
		stdio: "ignore",
	});
	if (result.status !== 0) {
		fs.rmSync(temp, { recursive: true, force: true });
		throw new Error(`web runtime extraction failed with status ${result.status}`);
	}
	fs.writeFileSync(path.join(temp, ".complete"), app.getVersion());
	fs.rmSync(webRoot, { recursive: true, force: true });
	fs.renameSync(temp, webRoot);
}

function spawnWeb() {
	ensureWebRuntime();
	if (!fs.existsSync(webServer)) {
		throw new Error(`web sidecar missing at ${webServer}`);
	}
	// The web sidecar is a Node process running Next's server.js. It must NOT be a second
	// copy of the Electron binary: apps/desktop/package.json disables the RunAsNode fuse,
	// which is exactly the fuse `spawn(process.execPath, ..., {ELECTRON_RUN_AS_NODE: "1"})`
	// depends on. With that fuse off the same command exits 1 and starts nothing (measured on
	// Electron 45.0.0 with the packaged fuse set applied), so the sidecar is an Electron
	// utility process instead: a real Node runtime, parented and reaped by the app, and
	// unaffected by the fuse.
	const env = childEnv({
		NODE_ENV: "production",
		PORT: String(webPort),
		HOSTNAME: "127.0.0.1",
		NEXT_TELEMETRY_DISABLED: "1",
		EZCHAIN_WEB_MODE: "live",
		EZCHAIN_API_URL: `http://127.0.0.1:${apiPort}`,
		EZCHAIN_API_TOKEN: apiToken,
		EZCHAIN_PUBLIC_URL: `http://127.0.0.1:${webPort}`,
		EZCHAIN_DESKTOP: "1",
	});
	// A utility process is already a Node runtime. Inheriting this variable would ask it to
	// become one a second time, which is the mode the fuse exists to refuse.
	delete env.ELECTRON_RUN_AS_NODE;
	const child = utilityProcess.fork(webServer, [], {
		cwd: path.dirname(webServer),
		env,
		stdio: "pipe",
		serviceName: "ezchain-web",
	});
	webChild = child;
	pipeChild("web", child);
	child.on("exit", (code, signal) => {
		log(`web exited code=${code} signal=${signal}`);
		if (webChild === child) webChild = null;
		if (intentionalStop.web) {
			intentionalStop.web = false;
			return;
		}
		if (!quitting) scheduleRestart("web");
	});
}

function scheduleRestart(which) {
	if (quitting || restarting) return;
	if (restartTimers[which]) return;
	if (starting) {
		// A start is in progress; retry once it has settled rather than spawning a duplicate.
		restartTimers[which] = setTimeout(() => {
			restartTimers[which] = null;
			scheduleRestart(which);
		}, 1000);
		return;
	}
	const delay = which === "api" ? apiBackoff : webBackoff;
	if (which === "api") apiBackoff = Math.min(apiBackoff * 2, 30000);
	else webBackoff = Math.min(webBackoff * 2, 30000);
	log(`scheduling ${which} restart in ${delay}ms`);
	restartTimers[which] = setTimeout(async () => {
		restartTimers[which] = null;
		if (starting || restarting || quitting) return;
		try {
			if (which === "api" && !apiChild) {
				spawnBackend();
				await waitFor(`http://127.0.0.1:${apiPort}/healthz`, 60000);
				apiBackoff = 1000;
			}
			if (which === "web" && !webChild) {
				spawnWeb();
				await waitFor(`http://127.0.0.1:${webPort}/desktop-health`, 60000);
				webBackoff = 1000;
			}
		} catch (error) {
			log(`${which} restart failed: ${error.message}`);
			scheduleRestart(which);
		}
	}, delay);
}

async function waitFor(url, timeoutMs) {
	const deadline = Date.now() + timeoutMs;
	let last = "";
	while (Date.now() < deadline) {
		try {
			const response = await fetch(url, { signal: AbortSignal.timeout(2500) });
			if (response.ok) return;
			last = `HTTP ${response.status}`;
		} catch (error) {
			last = error instanceof Error ? error.message : String(error);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
	throw new Error(`timed out waiting for ${url} (${last})`);
}

async function startServices() {
	if (starting) return;
	starting = true;
	try {
		apiToken = loadToken();
		apiPort = await freePort();
		webPort = await freePort();
		log(`starting services: api=${apiPort} web=${webPort} data=${dataDir}`);
		spawnBackend();
		await waitFor(`http://127.0.0.1:${apiPort}/readyz`, 90000);
		spawnWeb();
		await waitFor(`http://127.0.0.1:${webPort}/desktop-health`, 90000);
		log("services ready");
	} finally {
		starting = false;
	}
}

// The API sidecar is a native executable (node:child_process), the web sidecar is an
// Electron utility process. They share a lifecycle but not a kill API: ChildProcess takes a
// signal and exposes exitCode/signalCode; UtilityProcess has kill() only and its pid goes
// undefined once it exits. These two helpers are the difference, kept in one place.
function isRunning(child) {
	if (!child) return false;
	if (typeof child.exitCode === "number" || typeof child.signalCode === "string") return false;
	return typeof child.pid === "number";
}

function killChild(child, signal) {
	// A ChildProcess takes a signal; a UtilityProcess has kill() only and ignores the
	// argument. `exitCode` is the discriminator, and it is what isRunning already reads.
	try {
		if ("exitCode" in child) {
			child.kill(signal);
		} else {
			child.kill();
		}
	} catch (error) {
		log(`kill failed for pid=${child?.pid}: ${error.message}`);
	}
}

function terminateForRestart(child) {
	killChild(child, "SIGTERM");
	setTimeout(() => {
		if (isRunning(child)) {
			log(`sidecar pid=${child.pid} ignored SIGTERM; sending SIGKILL`);
			killChild(child, "SIGKILL");
		}
	}, 5000).unref?.();
}

async function stopChild(child, name) {
	if (!child) return;
	intentionalStop[name] = true;
	log(`stopping ${name} pid=${child.pid}`);
	killChild(child, "SIGTERM");
	const exited = await Promise.race([
		new Promise((resolve) => child.once("exit", () => resolve(true))),
		new Promise((resolve) => setTimeout(() => resolve(false), 5000)),
	]);
	if (!exited) {
		log(`${name} did not exit; sending SIGKILL`);
		killChild(child, "SIGKILL");
	}
	intentionalStop[name] = false;
}

async function restartServices() {
	if (starting || restarting) return;
	restarting = true;
	for (const which of ["api", "web"]) {
		if (restartTimers[which]) {
			clearTimeout(restartTimers[which]);
			restartTimers[which] = null;
		}
	}
	await stopChild(webChild, "web");
	webChild = null;
	await stopChild(apiChild, "api");
	apiChild = null;
	try {
		await startServices();
		if (mainWindow) mainWindow.loadURL(`http://127.0.0.1:${webPort}/`);
	} catch (error) {
		log(`restart failed: ${error.message}`);
		dialog.showErrorBox(`${APP_NAME} could not restart`, `${error.message}\n\nLogs: ${logFile}`);
	} finally {
		restarting = false;
	}
}

function createSplash() {
	splash = new BrowserWindow({
		width: 420,
		height: 260,
		frame: false,
		resizable: false,
		alwaysOnTop: true,
		show: true,
		backgroundColor: "#161615",
		webPreferences: { sandbox: true, contextIsolation: true },
	});
	const html = `<!doctype html><meta charset="utf-8"><style>
		body{margin:0;background:#161615;color:#f2f2ef;font:14px system-ui;display:grid;place-items:center;height:100vh}
		main{text-align:center} .mark{font:700 28px/1 ui-monospace,monospace;letter-spacing:.08em}
		.sub{color:#a3a39c;margin-top:12px} .bar{width:220px;height:2px;background:#2c2c29;margin:24px auto 0;overflow:hidden}
		.bar i{display:block;width:80px;height:2px;background:#f2f2ef;animation:m 1.2s infinite ease-in-out}
		@keyframes m{0%{transform:translateX(-90px)}100%{transform:translateX(230px)}}
	</style><main><div class="mark">ezCHAIN</div><div class="sub">Starting the paper workstation…</div><div class="bar"><i></i></div></main>`;
	splash.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
	splash.on("closed", () => (splash = null));
}

function createWindow() {
	if (mainWindow) {
		mainWindow.show();
		mainWindow.focus();
		return;
	}
	mainWindow = new BrowserWindow({
		width: 1440,
		height: 900,
		minWidth: 900,
		minHeight: 600,
		title: APP_NAME,
		backgroundColor: "#161615",
		autoHideMenuBar: true,
		show: false,
		webPreferences: {
			preload: path.join(__dirname, "preload.cjs"),
			contextIsolation: true,
			nodeIntegration: false,
			sandbox: true,
			webSecurity: true,
		},
	});
	const origin = `http://127.0.0.1:${webPort}`;
	mainWindow.loadURL(`${origin}/`);
	mainWindow.once("ready-to-show", () => {
		if (splash) splash.close();
		mainWindow.show();
		mainWindow.maximize();
		setTimeout(async () => {
			const bounds = mainWindow.getBounds();
			const viewport = await mainWindow.webContents.executeJavaScript(
				"({w: innerWidth, h: innerHeight, dpr: devicePixelRatio})",
			);
			log(`window bounds=${JSON.stringify(bounds)} viewport=${JSON.stringify(viewport)}`);
		}, 1500);
	});
	mainWindow.on("close", (event) => {
		if (quitting) return;
		event.preventDefault();
		mainWindow.hide();
	});
	mainWindow.webContents.setWindowOpenHandler(({ url }) => {
		if (url.startsWith("http://127.0.0.1:") || url.startsWith("https://")) {
			shell.openExternal(url);
		}
		return { action: "deny" };
	});
	mainWindow.webContents.on("will-navigate", (event, url) => {
		let same = false;
		try {
			same = new URL(url).origin === appOrigin();
		} catch {
			same = false;
		}
		if (!same) {
			event.preventDefault();
			if (url.startsWith("http")) shell.openExternal(url);
		}
	});
	mainWindow.webContents.on("render-process-gone", (_event, details) => {
		log(`renderer gone: ${details.reason}`);
		if (!quitting && mainWindow) mainWindow.reload();
	});
	mainWindow.on("unresponsive", () => {
		log("renderer unresponsive; reloading");
		if (!quitting && mainWindow) mainWindow.reload();
	});
}

function createTray() {
	try {
		const image = nativeImage.createFromPath(iconPath).resize({ width: 22, height: 22 });
		if (image.isEmpty()) return;
		tray = new Tray(image);
		tray.setToolTip(APP_NAME);
		const rebuild = () => {
			const login = app.getLoginItemSettings().openAtLogin;
			tray.setContextMenu(
				Menu.buildFromTemplate([
					{ label: "Open ezCHAIN", click: () => mainWindow?.show() },
					{ label: "Restart services", click: () => void restartServices() },
					{ type: "separator" },
					{ label: "Open logs", click: () => shell.openPath(logFile) },
					{ label: "Open data folder", click: () => shell.openPath(dataDir) },
					...(process.platform === "linux"
						? []
						: [
								{
									label: "Start at login",
									type: "checkbox",
									checked: login,
									click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }),
								},
							]),
					{ type: "separator" },
					{ label: "Quit", click: () => app.quit() },
				]),
			);
		};
		rebuild();
		tray.on("click", () => mainWindow?.show());
	} catch (error) {
		log(`tray unavailable: ${error.message}`);
	}
}

function createMenu() {
	Menu.setApplicationMenu(
		Menu.buildFromTemplate([
			{
				label: "File",
				submenu: [
					{ label: "Open logs", click: () => shell.openPath(logFile) },
					{ label: "Open data folder", click: () => shell.openPath(dataDir) },
					{ type: "separator" },
					{ role: "quit" },
				],
			},
			{
				label: "View",
				submenu: [
					{ role: "reload" },
					...(isDev ? [{ role: "toggleDevTools" }] : []),
					{ type: "separator" },
					{ role: "resetZoom" },
					{ role: "zoomIn" },
					{ role: "zoomOut" },
				],
			},
			{ label: "Help", submenu: [{ label: "ezCHAIN docs", click: () => shell.openExternal("https://github.com/0xJ1nn/ezchain") }] },
		]),
	);
}

async function boot() {
	// Installed before any window loads a page, so no response escapes the headers.
	hardenSession();
	createSplash();
	createTray();
	createMenu();
	try {
		await startServices();
		createWindow();
	} catch (error) {
		log(`startup failed: ${error.message}`);
		if (splash) splash.close();
		dialog.showErrorBox(
			`${APP_NAME} could not start`,
			`${error.message}\n\nLogs: ${logFile}`,
		);
		// Keep the supervisor alive so the tray can retry.
		if (tray) tray.displayBalloon?.({ title: APP_NAME, content: "Services failed to start. Use Restart services." });
	}
	setInterval(rotateLog, 60000);
	watchdogTimer = setInterval(async () => {
		if (quitting || starting || restarting || !apiPort || !webPort) return;
		const probedApi = apiChild;
		const probedWeb = webChild;
		try {
			await waitFor(`http://127.0.0.1:${apiPort}/healthz`, 3000);
		} catch {
			if (probedApi && apiChild === probedApi) {
				log("watchdog: api unhealthy; restarting");
				terminateForRestart(probedApi);
			} else if (!apiChild) {
				scheduleRestart("api");
			}
		}
		try {
			await waitFor(`http://127.0.0.1:${webPort}/desktop-health`, 3000);
		} catch {
			if (probedWeb && webChild === probedWeb) {
				log("watchdog: web unhealthy; restarting");
				terminateForRestart(probedWeb);
			} else if (!webChild) {
				scheduleRestart("web");
			}
		}
	}, 15000);
}

if (!app.requestSingleInstanceLock()) {
	app.quit();
} else {
	app.setAppUserModelId("io.ezchain.workstation");
	app.on("second-instance", () => {
		if (mainWindow) {
			mainWindow.show();
			mainWindow.focus();
		}
	});
	app.on("before-quit", async (event) => {
		if (quitting) return;
		event.preventDefault();
		quitting = true;
		if (watchdogTimer) clearInterval(watchdogTimer);
		for (const which of ["api", "web"]) {
			if (restartTimers[which]) {
				clearTimeout(restartTimers[which]);
				restartTimers[which] = null;
			}
		}
		await stopChild(webChild, "web");
		await stopChild(apiChild, "api");
		app.exit(0);
	});
	app.on("window-all-closed", () => {
		// Stay resident in the tray: the API, scheduler and market monitor keep running.
	});
	app.on("activate", () => {
		if (mainWindow) mainWindow.show();
	});
	app.whenReady().then(boot);
}

handleFromApp("desktop:restart", () => restartServices());
handleFromApp("desktop:open-logs", () => shell.openPath(logFile));
handleFromApp("desktop:open-data", () => shell.openPath(dataDir));

process.on("uncaughtException", (error) => log(`uncaught: ${error.stack || error.message}`));
process.on("unhandledRejection", (error) => log(`unhandled: ${String(error)}`));
