// Build-time reader for the one product version.
//
// next.config.ts imports this so the web bundle gets the value the API reads, not a
// second copy. A missing or malformed file is an error: a build that cannot say which
// product it is shipping must not guess. No dependencies, no network, no Next imports —
// the same function is callable directly by the agreement test.

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const FILENAME = "VERSION";
const SEMVER = /^\d+\.\d+\.\d+$/;
const VERSION_FILE_ENV = "EZCHAIN_VERSION_FILE";

function readVersionFile(file) {
	let raw;
	try {
		raw = readFileSync(file, "utf8").trim();
	} catch (error) {
		throw new Error(`ezCHAIN product version is unreadable at ${file}: ${error.message}`);
	}
	if (!SEMVER.test(raw)) {
		throw new Error(
			`ezCHAIN product version at ${file} is not a semantic version: ${JSON.stringify(raw)}`,
		);
	}
	return raw;
}

export function readProductVersion(startDir = process.cwd()) {
	// An explicit path is authoritative, never a hint.
	const override = process.env[VERSION_FILE_ENV];
	if (override) {
		return readVersionFile(path.resolve(override));
	}

	let dir = path.resolve(startDir);
	const tried = [];
	while (true) {
		const candidate = path.join(dir, FILENAME);
		tried.push(candidate);
		if (existsSync(candidate)) {
			return readVersionFile(candidate);
		}
		const parent = path.dirname(dir);
		if (parent === dir) break;
		dir = parent;
	}
	throw new Error(
		"ezCHAIN product version is unavailable: no readable VERSION file. " +
			`Tried: ${tried.join(", ")}. Set ${VERSION_FILE_ENV} to the file's path.`,
	);
}
