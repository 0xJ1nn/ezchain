"use strict";

// First-run setup. One home for every value, the paths shown are the paths the API is
// given, a backup is a consistent copy that restores, and no secret reaches a page or a
// log. These are the four acceptance criteria for the setup screen.

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { DatabaseSync } = require("node:sqlite");
const sender = require("../../sender.js");

const TOKEN = "TEST_TOKEN_DO_NOT_USE";

function tempDir(t) {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ezsetup-"));
	t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
	return dir;
}

function backupDirOf(dir) {
	const backupDir = path.join(dir, "backups");
	fs.mkdirSync(backupDir, { recursive: true });
	return backupDir;
}

test("a clean setup has one home for every value and offers paper only", (t) => {
	const configDir = tempDir(t);
	const setup = sender.initialSetup(configDir);
	const paths = sender.setupPaths(setup, configDir);
	const summary = sender.setupSummary(setup, paths);

	assert.equal(setup.mode, "paper");
	assert.equal(sender.SETUP_MODE, "paper");
	assert.equal(summary.mode, "paper");
	assert.equal(summary.dataDir, configDir);
	assert.equal(summary.dataDir, paths.dataDir);
	assert.equal(summary.tokenPath, paths.tokenFile);
	assert.equal(summary.backupDir, paths.backupDir);
	assert.equal(paths.configFile, path.join(configDir, "host.json"));
	assert.equal(paths.logFile, path.join(configDir, "logs", "desktop.log"));
	assert.equal(summary.role, "local");
	assert.equal(summary.url, null);
	// The summary is what the screen displays; a secret can never be part of it.
	assert.equal(JSON.stringify(summary).includes(TOKEN), false);
	assert.equal(Object.keys(summary).some((k) => /token\s*value|secret|apikey/i.test(k)), false);
});

test("the paths shown are exactly the paths the API is given", (t) => {
	const configDir = tempDir(t);
	const dataDir = path.join(configDir, "custom-data");
	const backupDir = path.join(configDir, "custom-backups");
	fs.mkdirSync(backupDir, { recursive: true });
	const setup = sender.normalizeSetup(
		{ mode: "live", dataDir, backupDir, role: "local" },
		configDir,
	);
	// v1 ignores any saved live request; the screen offers paper and the API is pinned to it.
	assert.equal(setup.mode, "paper");

	const paths = sender.setupPaths(setup, configDir);
	const shown = sender.setupSummary(setup, paths);
	const env = sender.backendEnv(setup, paths, { api: 8123, web: 8234 }, TOKEN);

	assert.equal(env.EZCHAIN_DATABASE_URL, `sqlite+pysqlite:///${shown.dataDir}/ezchain.db`);
	assert.equal(env.EZCHAIN_DATABASE_URL, `sqlite+pysqlite:///${paths.dbFile}`);
	assert.equal(env.EZCHAIN_BACKUP_DIR, shown.backupDir);
	assert.equal(env.EZCHAIN_BACKUP_DIR, paths.backupDir);
	assert.equal(env.EZCHAIN_EXECUTION_MODE, "paper");
	assert.equal(env.EZCHAIN_API_TOKEN, TOKEN);
	assert.equal(env.EZCHAIN_DATA_MODE, "live"); // market data, not trading mode
	assert.match(env.EZCHAIN_PUBLIC_URL, /8234$/);
});

test("a saved host.json from the role chooser still resolves without a second file", (t) => {
	const configDir = tempDir(t);
	const setup = sender.normalizeSetup({ role: "remote", url: "https://host.example/" }, configDir);
	const paths = sender.setupPaths(setup, configDir);
	assert.equal(paths.configFile, path.join(configDir, "host.json"));
	assert.equal(setup.dataDir, configDir);
	assert.equal(setup.backupDir, path.join(configDir, "backups"));
	assert.equal(setup.role, "remote");
	assert.equal(setup.url, "https://host.example");
});

test("a backup is a consistent copy that restores into a working app", (t) => {
	const dir = tempDir(t);
	const dbFile = path.join(dir, "ezchain.db");
	const backupDir = path.join(dir, "backups");
	fs.mkdirSync(backupDir, { recursive: true });

	const db = new DatabaseSync(dbFile);
	db.exec("CREATE TABLE ledger(id INTEGER PRIMARY KEY, note TEXT)");
	db.exec("INSERT INTO ledger(id, note) VALUES (1, 'paper-fill')");
	db.close();

	const result = sender.backupDatabase({
		dbFile,
		backupDir,
		now: new Date("2026-11-23T10:11:12Z"),
	});

	assert.equal(path.dirname(result.file), backupDir);
	assert.match(path.basename(result.file), /^ezchain-20261123T101112Z.*\.db$/);
	assert.equal(result.bytes, fs.statSync(result.file).size);
	assert.ok(result.bytes > 0);

	// Restoring the file into a fresh app means opening it and finding the same rows
	// with an intact page structure. The source is untouched by the copy.
	const restored = new DatabaseSync(result.file, { readOnly: true });
	assert.deepEqual({ ...restored.prepare("SELECT note FROM ledger WHERE id = 1").get() }, { note: "paper-fill" });
	assert.equal(restored.prepare("PRAGMA integrity_check").get().integrity_check, "ok");
	restored.close();
	const source = new DatabaseSync(dbFile, { readOnly: true });
	assert.equal(source.prepare("SELECT count(*) AS n FROM ledger").get().n, 1);
	source.close();
});

test("a missing or unwritable backup path is refused at setup with the reason", (t) => {
	const dir = tempDir(t);
	const missing = path.join(dir, "does-not-exist");
	const file = path.join(dir, "not-a-dir");
	fs.writeFileSync(file, "x");

	assert.equal(sender.validateBackupDir(missing).ok, false);
	assert.match(sender.validateBackupDir(missing).reason, /exist|ENOENT/i);
	assert.equal(sender.validateBackupDir(file).ok, false);
	assert.match(sender.validateBackupDir(file).reason, /directory/i);
	assert.equal(sender.validateBackupDir(backupDirOf(dir)).ok, true);

	assert.throws(
		() => sender.backupDatabase({ dbFile: path.join(dir, "none.db"), backupDir: file }),
		/directory/,
	);
	assert.throws(
		() => sender.backupDatabase({ dbFile: path.join(dir, "none.db"), backupDir: missing }),
		/exist|ENOENT/i,
	);
});

test("the setup page never shows a secret or generates one in the renderer", () => {
	const html = sender.FIRST_RUN_HTML;
	assert.match(html, /PAPER/);
	assert.match(html, /stored at/);
	assert.match(html, /never displayed/);
	// The value is never rendered, so the copy must not imply a reveal that never happened.
	assert.equal(/shown again|shown once|reveal/i.test(html), false);
	assert.match(html, /Back up now/);
	assert.match(html, /Data directory/i);
	assert.match(html, /Backup path/i);
	assert.match(html, /setupPreview\(/);
	assert.match(html, /backupNow\(/);
	// The token is created in the main process only; no page script holds a value.
	assert.equal(/randomBytes|generateToken|api[_-]?token|api[_-]?key|authorization|bearer/i.test(html), false);
	assert.equal(/<form\b/i.test(html), false);
	assert.equal(/fetch\s*\(/.test(html), false);
});

test("the supervisor never writes the token to the log", () => {
	const main = fs.readFileSync(path.resolve(__dirname, "..", "..", "main.cjs"), "utf8");
	for (const line of main.split("\n")) {
		if (!/\blog\(/.test(line)) continue;
		assert.equal(/apiToken|EZCHAIN_API_TOKEN|tokenFile|\btoken\b/i.test(line), false, line);
	}
});

test("a local to local re-run tears down the previous host before starting the new one", () => {
	// The decision is a pure function of the previous role and what this host owns, so the
	// re-run case is pinned without launching Electron.
	assert.equal(sender.needsLocalTeardown("local", {}), true);
	assert.equal(sender.needsLocalTeardown("local", { apiChild: {}, webChild: {}, watchdog: true }), true);
	// A clean boot owns nothing; switching from a remote shell owns no local host either.
	assert.equal(sender.needsLocalTeardown(null, {}), false);
	assert.equal(sender.needsLocalTeardown("remote", {}), false);
	// A stale child or watchdog still forces a teardown even if the role record disagrees.
	assert.equal(sender.needsLocalTeardown("remote", { webChild: {}, watchdog: true }), true);

	// The supervisor applies that decision before it spawns replacements.
	const main = fs.readFileSync(path.resolve(__dirname, "..", "..", "main.cjs"), "utf8");
	const fn = main.slice(main.indexOf("async function startRole"), main.indexOf("async function boot"));
	const teardown = fn.indexOf("stopLocalHost()");
	const spawn = fn.indexOf("startServices()");
	assert.ok(teardown >= 0, "startRole tears down the existing local host");
	assert.ok(spawn >= 0, "startRole starts services");
	assert.ok(teardown < spawn, "teardown happens before the new host starts");
});

test("only the setup window can change setup, and its bridge alone has the controls", () => {
	// A dashboard script cannot forge its WebContents id, so identity is a stricter gate
	// than an origin check: both windows load local data documents.
	assert.equal(sender.isSetupSender(7, 7), true);
	assert.equal(sender.isSetupSender(8, 7), false);
	assert.equal(sender.isSetupSender(7, null), false);
	assert.equal(sender.isSetupSender(7, undefined), false);

	const preload = fs.readFileSync(path.resolve(__dirname, "..", "..", "preload.cjs"), "utf8");
	const guarded = preload.indexOf("if (isSetupWindow)");
	assert.ok(guarded >= 0, "the preload marks the setup window");
	assert.match(preload.slice(0, guarded), /--ezchain-setup-window/);
	assert.equal(/finishSetup|setupPreview|backupNow/.test(preload.slice(0, guarded)), false);
	for (const method of ["setupPreview", "finishSetup", "backupNow"]) {
		assert.match(preload.slice(guarded), new RegExp(method), method);
	}
	// No path from the dashboard reopens setup: reconfiguration is opened from the tray.
	assert.equal(/openSetup|desktop:open-setup/.test(preload), false);

	const main = fs.readFileSync(path.resolve(__dirname, "..", "..", "main.cjs"), "utf8");
	const guard = main.slice(main.indexOf("function assertSetupSender"), main.indexOf('ipcMain.handle("desktop:setup-preview"'));
	assert.match(guard, /firstRun\.webContents\.id/);
	const region = main.slice(
		main.indexOf('ipcMain.handle("desktop:setup-preview"'),
		main.indexOf("// The only channel from the read-only view"),
	);
	for (const channel of ["desktop:setup-preview", "desktop:backup-now", "desktop:finish-setup"]) {
		const start = region.indexOf(`ipcMain.handle("${channel}"`);
		assert.ok(start >= 0, `handler for ${channel}`);
		const next = region.indexOf("ipcMain.handle(", start + 1);
		assert.match(region.slice(start, next < 0 ? undefined : next), /assertSetupSender\(event\)/, channel);
	}
	assert.equal(/desktop:open-setup/.test(main), false);
});

test("the production backup checks the snapshot it just wrote", () => {
	const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "sender.js"), "utf8");
	const fn = src.slice(src.indexOf("function backupDatabase"), src.indexOf("// The setup screen."));
	assert.match(fn, /VACUUM INTO/);
	assert.match(fn, /PRAGMA integrity_check/);
	assert.match(fn, /rmSync\(file/);
});
