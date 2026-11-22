// Behaviour tests for the desktop shell's power and autostart logic, run with
// `node --test "test/*.mjs"`.
//
// The point of these is the admission, not a feature: a desktop machine sleeps, and a
// suspended machine runs no strategy. The shell must therefore do the useful thing on
// resume (probe immediately, restart what died) and must not pretend a suspend is uptime.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { autostartEntry, autostartExecPath, autostartPath, onPowerState } from "../platform.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");

test("suspend stops probing and does not queue a restart", () => {
	let probed = 0;
	let restarted = 0;
	const verdict = onPowerState({
		state: "suspend",
		probeNow: () => probed++,
		restartNow: () => restarted++,
	});
	assert.equal(probed, 0, "a suspend must not probe");
	assert.equal(restarted, 0, "a suspend must not queue a restart");
	assert.equal(verdict.probing, false);
	assert.match(verdict.message, /suspended/);
});

test("resume probes and recovers immediately instead of waiting for the watchdog", () => {
	for (const state of ["resume", "unlock"]) {
		let probed = 0;
		let restarted = 0;
		const verdict = onPowerState({
			state,
			probeNow: () => probed++,
			restartNow: () => restarted++,
		});
		assert.equal(probed, 1, `${state} probes once`);
		assert.equal(restarted, 1, `${state} runs the recovery pass`);
		assert.equal(verdict.probing, true);
	}
});

test("an unknown power state changes nothing", () => {
	let calls = 0;
	const verdict = onPowerState({
		state: "battery-low",
		probeNow: () => calls++,
		restartNow: () => calls++,
	});
	assert.equal(calls, 0);
	assert.equal(verdict.message, "unchanged");
});

test("the autostart entry is a valid desktop file that launches hidden", () => {
	const entry = autostartEntry({ execPath: "/opt/ezCHAIN/ezchain" });
	const lines = entry.split("\n");
	assert.equal(lines[0], "[Desktop Entry]");
	assert.ok(lines.includes("Type=Application"));
	assert.ok(lines.includes("Terminal=false"));
	assert.ok(lines.includes("X-GNOME-Autostart-enabled=true"));
	assert.match(entry, /^Exec="\/opt\/ezCHAIN\/ezchain" --hidden$/m);
	assert.ok(entry.endsWith("\n"), "a desktop file ends with a newline");

	// A path with a quote or a backslash must not break out of the Exec value.
	const tricky = autostartEntry({ execPath: '/opt/we"ird\\path/ezchain' });
	assert.match(tricky, /^Exec="\/opt\/we\\"ird\\\\path\/ezchain" --hidden$/m);
});

test("the autostart file lives in the XDG config home", () => {
	assert.equal(
		autostartPath("/home/user/.config"),
		"/home/user/.config/autostart/ezchain.desktop",
	);
});

test("an AppImage records the real file, not the ephemeral mount", () => {
	// Getting this wrong writes an Exec line pointing into a mount that no longer exists
	// after the session ends, so the app silently stops starting at login.
	const execPath = "/tmp/.mount_ezCHAIab12cd/ezchain";
	const real = "/home/user/Applications/ezCHAIN-0.1.0-x86_64.AppImage";
	assert.equal(autostartExecPath({ execPath, appImage: real }), real);
	assert.equal(autostartExecPath({ execPath, appImage: "" }), execPath);
	assert.equal(autostartExecPath({ execPath, appImage: undefined }), execPath);
	// A .deb install has no APPIMAGE and its execPath is durable.
	assert.equal(autostartExecPath({ execPath: "/opt/ezCHAIN/ezchain" }), "/opt/ezCHAIN/ezchain");
});

test("the supervisor actually gates its watchdog and restarts on power state", () => {
	const mainSource = readFileSync(path.join(desktop, "main.cjs"), "utf8");
	// The decision in onPowerState is worthless if nothing calls it, and the gate is
	// worthless if the watchdog and the restart timer do not read it.
	assert.match(mainSource, /powerMonitor\.on\("suspend"/);
	assert.match(mainSource, /powerMonitor\.on\("resume"/);
	assert.match(mainSource, /let suspended = false/);
	assert.match(
		mainSource,
		/suspended = state === "suspend"/,
		"the gate must close on suspend",
	);
	const watchdog = mainSource.slice(mainSource.indexOf("watchdogTimer = setInterval"));
	assert.match(
		watchdog.slice(0, 200),
		/suspended/,
		"the watchdog must skip while suspended",
	);
	const restart = mainSource.slice(mainSource.indexOf("restartTimers[which] = setTimeout"));
	assert.match(
		mainSource,
		/if \(starting \|\| restarting \|\| quitting \|\| suspended\) return;/,
		"a queued restart must not fire while suspended",
	);
	assert.ok(restart.includes("restartTimers[which] = null"), "sanity: found the restart timer");
});

test("an autostart launch stays in the tray", () => {
	const mainSource = readFileSync(path.join(desktop, "main.cjs"), "utf8");
	assert.match(mainSource, /process\.argv\.includes\("--hidden"\)/);
	assert.match(mainSource, /if \(!hiddenLaunch\(\)\) \{/);
	assert.match(mainSource, /if \(!hiddenLaunch\(\)\) createSplash\(\)/);
});
