"use strict";

// Platform differences the desktop supervisor has to know about, kept free of Electron so
// `node --test` can exercise them on any host.
//
// Three of these were real bugs for a Windows build and would have failed at runtime, not
// at build time:
//
//   1. The frozen sidecar is `ezchain-api.exe` on Windows, not `ezchain-api`. A literal
//      path makes `spawnBackend` throw "backend sidecar missing" on every launch.
//   2. `app.getPath("userData")` returns `C:\Users\me\AppData\Roaming\ezCHAIN` on Windows.
//      Interpolating that into `sqlite+pysqlite:///${path}` yields a URL with backslashes;
//      SQLAlchemy parses it, but the driver then hands the backslashes to sqlite, which
//      treats them as escapes. The URL must use forward slashes on every platform.
//   3. Extracting the web runtime needs a tar binary. Windows 10 1803+ ships bsdtar at
//      `%SystemRoot%\System32\tar.exe`, which is not always on a packaged app's PATH.
//
// None of these touch money, keys or the ledger: they decide which file to start and how
// to spell a local path.

const path = require("node:path");

/** The frozen API sidecar's file name for a platform (`ezchain-api.exe` on win32). */
function sidecarName(platform = process.platform) {
	return platform === "win32" ? "ezchain-api.exe" : "ezchain-api";
}

/** The tar binary to extract the web runtime with. Windows has one, but not on PATH. */
function tarBinary({ platform = process.platform, systemRoot = process.env.SystemRoot, exists = () => false } = {}) {
	if (platform !== "win32") return "tar";
	const root = systemRoot || "C:\\Windows";
	const candidate = path.win32.join(root, "System32", "tar.exe");
	return exists(candidate) ? candidate : "tar";
}

/**
 * The XDG autostart entry for Linux, where Electron's `setLoginItemSettings` does nothing.
 *
 * `docs/DESKTOP.md` promises the app keeps running after the window closes; starting it
 * again after a reboot is the other half of that, and on Linux it is one file in
 * `~/.config/autostart`. The Exec line is quoted per the Desktop Entry spec, and
 * `X-GNOME-Autostart-enabled=true` is what GNOME reads.
 */
function autostartEntry({ execPath, name = "ezCHAIN" }) {
	const quoted = `"${execPath.replace(/(["\\])/g, "\\$1")}"`;
	return [
		"[Desktop Entry]",
		"Type=Application",
		`Name=${name}`,
		"Comment=ezCHAIN trading workstation (paper)",
		`Exec=${quoted} --hidden`,
		"Terminal=false",
		"X-GNOME-Autostart-enabled=true",
		"StartupNotify=false",
		"",
	].join("\n");
}

/** Where the XDG autostart entry lives for a given config home. */
function autostartPath(configHome) {
	return path.posix.join(configHome, "autostart", "ezchain.desktop");
}

/**
 * The executable an autostart entry should launch.
 *
 * `process.execPath` is wrong for an AppImage: inside one it points at the mounted
 * squashfs copy (`/tmp/.mount_ezCHAI.../ezchain`), which is gone after the mount is
 * removed, so the entry would fail on the next login. AppImage sets `APPIMAGE` to the real
 * file the user launched, and that is the path to record. A `.deb` install has no `APPIMAGE`
 * and `execPath` is already durable.
 */
function autostartExecPath({ execPath, appImage } = {}) {
	const fromAppImage = (appImage ?? "").trim();
	if (fromAppImage) return fromAppImage;
	return execPath;
}

/**
 * A SQLAlchemy URL for the embedded SQLite database.
 *
 * `sqlite+pysqlite:///` plus an absolute path is the documented form. SQLite accepts
 * forward slashes on Windows, so one spelling covers all three platforms:
 * POSIX `/home/user/x.db` -> `sqlite+pysqlite:////home/user/x.db`,
 * Windows `C:\Users\user\x.db` -> `sqlite+pysqlite:///C:/Users/user/x.db`.
 */
function sqliteUrl(dbFile, platform = process.platform) {
	const normalized = platform === "win32" ? dbFile.replace(/\\/g, "/") : dbFile;
	return `sqlite+pysqlite:///${normalized}`;
}

/**
 * Whether the extracted web runtime is complete for this version. The marker records the
 * version that produced the directory, so an app upgrade re-extracts instead of running
 * the previous release's server.
 */
function webRuntimeReady({ serverPath, markerPath, version, exists, read }) {
	if (!exists(serverPath) || !exists(markerPath)) return false;
	try {
		return read(markerPath).trim() === String(version);
	} catch {
		return false;
	}
}

const SLEEP_SUSPEND = "suspend";
const SLEEP_RESUME = "resume";
const SLEEP_UNLOCK = "unlock";

/**
 * What the supervisor should do with its sidecars when the machine's power state changes.
 *
 * A desktop cannot promise uptime: a laptop sleeps, and a suspended machine runs no
 * strategy. Passing the state is not upside for the user, so the shell admits it instead
 * of pretending the app is a server. On resume the sidecars are usually alive and
 * serving; if the OS froze them mid-write the watchdog restarts them. The decision is:
 *
 *   * `suspend` - stop probing and stop queueing restarts. A restart attempted while the
 *     machine is asleep fails and the backoff climbs, so nothing is gained by trying.
 *   * `resume`  - probe once immediately rather than waiting for the 15 s watchdog, so a
 *     sidecar the OS killed during sleep is recovered in seconds.
 *   * `unlock`  - same as resume; a lock screen does not stop a process but it does
 *     mean a user is back.
 */
function onPowerState({ state, restartNow, probeNow }) {
	if (state === SLEEP_SUSPEND) {
		return { probing: false, message: "suspended; sidecars left as the OS left them" };
	}
	if (state === SLEEP_RESUME || state === SLEEP_UNLOCK) {
		probeNow();
		restartNow();
		return { probing: true, message: "resumed; probing both sidecars now" };
	}
	return { probing: true, message: "unchanged" };
}

module.exports = {
	autostartEntry,
	autostartExecPath,
	autostartPath,
	onPowerState,
	sidecarName,
	sqliteUrl,
	tarBinary,
	webRuntimeReady,
};
