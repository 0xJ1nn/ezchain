"use strict";

// ezCHAIN desktop supervisor.
//
// One product, two roles. As a host the app owns two private sidecars: the FastAPI paper
// API on an embedded SQLite database and the traced Next.js server. Both bind to 127.0.0.1
// on ephemeral ports and are never exposed. As a shell the app owns no process and no key:
// it opens a read-only view of a host the operator chose, and only GETs on an allowlist of
// read paths leave the window. Closing the window hides it to the tray either way.

const { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const sender = require("./sender.js");

const APP_NAME = "ezCHAIN";
const isDev = !app.isPackaged;
app.setName(APP_NAME);
const dataDir = app.getPath("userData");
const logsDir = path.join(dataDir, "logs");
const logFile = path.join(logsDir, "desktop.log");
const dbFile = path.join(dataDir, "ezchain.db");
const tokenFile = path.join(dataDir, "api-token");
const configFile = path.join(dataDir, "host.json");
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

let mainWindow = null;
let splash = null;
let tray = null;
let firstRun = null;
let rebuildTray = () => {};
let role = null;
let hostUrl = null;
let viewerUrl = null;
let loadedViewerUrl = null;
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

// The role and the host address live in the user data directory, never in the repository.
// A malformed or missing file is a first run, not a reason to guess a host.
function loadConfig() {
	try {
		const saved = JSON.parse(fs.readFileSync(configFile, "utf8"));
		if (saved && saved.role === "remote" && typeof saved.url === "string") {
			return { role: "remote", url: sender.normalizeHostUrl(saved.url) };
		}
		if (saved && saved.role === "local") return { role: "local" };
	} catch {
		// No usable configuration yet; the first-run screen decides.
	}
	return null;
}

function saveConfig(config) {
	fs.mkdirSync(dataDir, { recursive: true });
	fs.writeFileSync(configFile, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
}

// A URL is tested before it is saved. The probe is a read-only GET over TLS, so a failed
// test leaves no configuration behind and the chooser stays open with a retry message.
async function testHost(rawUrl) {
	const probe = sender.hostProbeRequest(rawUrl);
	const response = await fetch(probe.url, { ...probe.init, signal: AbortSignal.timeout(5000) });
	if (!response.ok) throw new Error(`host returned HTTP ${response.status}`);
	await response.arrayBuffer();
	return sender.normalizeHostUrl(rawUrl);
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
	const child = spawn(process.execPath, [webServer], {
		cwd: path.dirname(webServer),
		env: childEnv({
			ELECTRON_RUN_AS_NODE: "1",
			NODE_ENV: "production",
			PORT: String(webPort),
			HOSTNAME: "127.0.0.1",
			NEXT_TELEMETRY_DISABLED: "1",
			EZCHAIN_WEB_MODE: "live",
			EZCHAIN_API_URL: `http://127.0.0.1:${apiPort}`,
			EZCHAIN_API_TOKEN: apiToken,
			EZCHAIN_PUBLIC_URL: `http://127.0.0.1:${webPort}`,
			EZCHAIN_DESKTOP: "1",
		}),
		stdio: ["ignore", "pipe", "pipe"],
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

function terminateForRestart(child) {
	child.kill("SIGTERM");
	setTimeout(() => {
		if (child.exitCode === null && child.signalCode === null) {
			log(`sidecar pid=${child.pid} ignored SIGTERM; sending SIGKILL`);
			child.kill("SIGKILL");
		}
	}, 5000).unref?.();
}

async function stopChild(child, name) {
	if (!child) return;
	intentionalStop[name] = true;
	log(`stopping ${name} pid=${child.pid}`);
	child.kill("SIGTERM");
	const exited = await Promise.race([
		new Promise((resolve) => child.once("exit", () => resolve(true))),
		new Promise((resolve) => setTimeout(() => resolve(false), 5000)),
	]);
	if (!exited) {
		log(`${name} did not exit; sending SIGKILL`);
		child.kill("SIGKILL");
	}
	intentionalStop[name] = false;
}

async function restartServices() {
	// A shell has no sidecars to restart; the tray's restart action reconnects instead.
	if (role === "remote") return reconnectHost();
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

function createFirstRun() {
	if (firstRun) {
		firstRun.show();
		firstRun.focus();
		return;
	}
	firstRun = new BrowserWindow({
		width: 560,
		height: 620,
		title: `${APP_NAME} — choose a host`,
		backgroundColor: "#161615",
		autoHideMenuBar: true,
		resizable: false,
		webPreferences: {
			preload: path.join(__dirname, "preload.cjs"),
			contextIsolation: true,
			nodeIntegration: false,
			sandbox: true,
		},
	});
	firstRun.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(sender.FIRST_RUN_HTML)}`);
	firstRun.on("closed", () => (firstRun = null));
}

function createWindow() {
	if (mainWindow) {
		// A role change swaps the viewer (local web app vs remote read-only page). Reload
		// when the target changed; an ordinary show must not interrupt in-page navigation.
		if (loadedViewerUrl !== viewerUrl) {
			loadedViewerUrl = viewerUrl;
			mainWindow.loadURL(viewerUrl);
		}
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
	if (!viewerUrl) viewerUrl = `http://127.0.0.1:${webPort}/`;
	loadedViewerUrl = viewerUrl;
	mainWindow.loadURL(viewerUrl);
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
		const allowed =
			role === "remote"
				? url.startsWith("https://")
				: url.startsWith("http://127.0.0.1:") || url.startsWith("https://");
		if (allowed) shell.openExternal(url);
		return { action: "deny" };
	});
	mainWindow.webContents.on("will-navigate", (event, url) => {
		let same = url === viewerUrl;
		if (!same && role !== "remote") {
			try {
				same = new URL(url).origin === `http://127.0.0.1:${webPort}`;
			} catch {
				same = false;
			}
		}
		if (!same) {
			// A remote view never loads the host's web app. Its only navigation target is
			// the local read-only page, so a link to the operator's origin leaves the app.
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
		rebuildTray = () => {
			const login = app.getLoginItemSettings().openAtLogin;
			const remote = role === "remote";
			tray.setContextMenu(
				Menu.buildFromTemplate([
					{ label: "Open ezCHAIN", click: () => (mainWindow || firstRun)?.show() },
					{
						label: remote ? "Reconnect to host" : "Restart services",
						click: () => void (remote ? reconnectHost() : restartServices()),
					},
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
		rebuildTray();
		tray.on("click", () => (mainWindow || firstRun)?.show());
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

function startWatchdog() {
	if (watchdogTimer) return;
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

// Reconnecting is a read-only probe and a reload of the local view. It never touches a
// process on the host, so a lost connection cannot end a strategy running there.
async function reconnectHost() {
	if (role !== "remote") return restartServices();
	try {
		hostUrl = await testHost(hostUrl);
		saveConfig({ role: "remote", url: hostUrl });
		if (mainWindow) mainWindow.loadURL(viewerUrl);
		return true;
	} catch (error) {
		log(`reconnect failed: ${error.message}`);
		dialog.showErrorBox(`${APP_NAME} could not reconnect`, `${error.message}\n\nLogs: ${logFile}`);
		return false;
	}
}

// Leaving the local role tears down everything it owned: watchdog, restart timers and
// both sidecars. A shell labelled remote must not still supervise a local host.
async function stopLocalHost() {
	if (watchdogTimer) {
		clearInterval(watchdogTimer);
		watchdogTimer = null;
	}
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
	apiToken = null;
	apiPort = null;
	webPort = null;
}

async function startRole(config) {
	const previousRole = role;
	role = config.role;
	hostUrl = config.url || null;
	const plan = sender.rolePlan(role);
	if (!plan.spawnSidecars && (previousRole === "local" || apiChild || webChild || watchdogTimer)) {
		await stopLocalHost();
	}
	if (plan.spawnSidecars) {
		try {
			await startServices();
		} catch (error) {
			log(`startup failed: ${error.message}`);
			if (splash) splash.close();
			dialog.showErrorBox(`${APP_NAME} could not start`, `${error.message}\n\nLogs: ${logFile}`);
			// Keep the supervisor alive so the tray can retry.
			if (tray) tray.displayBalloon?.({ title: APP_NAME, content: "Services failed to start. Use Restart services." });
			return false;
		}
		viewerUrl = `http://127.0.0.1:${webPort}/`;
	} else {
		viewerUrl = `data:text/html;charset=utf-8,${encodeURIComponent(sender.REMOTE_VIEWER_HTML)}`;
	}
	if (splash) splash.close();
	if (firstRun) {
		firstRun.close();
		firstRun = null;
	}
	createWindow();
	rebuildTray();
	if (plan.watchdog) startWatchdog();
	return true;
}

async function boot() {
	setInterval(rotateLog, 60000);
	const config = loadConfig();
	createTray();
	createMenu();
	if (!config) {
		createFirstRun();
		return;
	}
	createSplash();
	await startRole(config);
}

if (!app.requestSingleInstanceLock()) {
	app.quit();
} else {
	app.setAppUserModelId("io.ezchain.workstation");
	app.on("second-instance", () => {
		const windowToShow = mainWindow || firstRun;
		if (windowToShow) {
			windowToShow.show();
			windowToShow.focus();
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
		else if (firstRun) firstRun.show();
	});
	app.whenReady().then(boot);
}

ipcMain.handle("desktop:restart", () => restartServices());
ipcMain.handle("desktop:open-logs", () => shell.openPath(logFile));
ipcMain.handle("desktop:open-data", () => shell.openPath(dataDir));

// First-run choices. A URL is only stored after the probe succeeds, and choosing local
// leaves the existing sidecar path exactly as it was. The role is decided once: a script in
// the local web app cannot later re-point this window or make it stop owning its host.
function assertFirstRun() {
	if (!firstRun || role !== null) {
		throw new Error("the workstation role can only be chosen on first run");
	}
}

ipcMain.handle("desktop:remote-connect", async (_event, rawUrl) => {
	assertFirstRun();
	const url = await testHost(rawUrl);
	saveConfig({ role: "remote", url });
	await startRole({ role: "remote", url });
	return { role: "remote", url };
});
ipcMain.handle("desktop:choose-local", async () => {
	assertFirstRun();
	saveConfig({ role: "local" });
	await startRole({ role: "local" });
	return { role: "local" };
});
ipcMain.handle("desktop:remote-config", () => ({ role, url: hostUrl }));

// The only channel from the read-only view to a host. The sender refuses every write
// method and every path outside the read allowlist, and drops all credential headers.
ipcMain.handle("desktop:remote-read", async (_event, requestPath) => {
	if (role !== "remote" || !hostUrl) {
		throw new Error("remote reads are only available after attaching to a host");
	}
	const request = sender.buildRemoteRequest({ baseUrl: hostUrl, path: requestPath });
	const response = await fetch(request.url, request.init);
	const body = await response.text();
	if (!response.ok) throw new Error(`host returned HTTP ${response.status}`);
	return { status: response.status, body };
});

process.on("uncaughtException", (error) => log(`uncaught: ${error.stack || error.message}`));
process.on("unhandledRejection", (error) => log(`unhandled: ${String(error)}`));
