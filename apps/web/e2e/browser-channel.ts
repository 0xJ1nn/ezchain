import { existsSync } from "node:fs";

/**
 * Resolve the Playwright browser channel once for both the fast and live configs.
 * Installed Chrome wins when present; bundled Chromium is the portable fallback. An
 * explicit EZCHAIN_PW_CHANNEL always wins. Keeping this in one file prevents the two
 * gates from silently testing different browsers.
 */
export function browserChannel(): string {
	const explicit = process.env.EZCHAIN_PW_CHANNEL;
	if (explicit) return explicit;
	const candidates = [
		"/opt/google/chrome/chrome",
		"/usr/bin/google-chrome",
		"/usr/bin/google-chrome-stable",
		"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
	];
	return candidates.some((path) => existsSync(path)) ? "chrome" : "chromium";
}
