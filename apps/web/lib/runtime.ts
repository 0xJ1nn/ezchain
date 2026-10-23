// Safe runtime facts for the system pages.
//
// Never read or print `EZCHAIN_API_TOKEN` here. The only token fact a page may show is
// whether a deployment token is configured, and the token itself stays server-side.

export type WebMode = "fixture" | "live";

export function webMode(): WebMode {
	return process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
}

/**
 * Preview requests normally follow the data mode: a fixture page uses the labelled
 * fixture sample and never pretends to have resolved a size. An operator can point a
 * fixture UI at a real API for previews only (the Playwright gate does this) so the
 * authoritative preview contract is still exercised end to end.
 */
export function previewMode(mode: WebMode): WebMode {
	if (mode === "live") return "live";
	return process.env.EZCHAIN_WEB_PREVIEW_MODE === "live" ? "live" : "fixture";
}

function origin(): URL {
	const raw = (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").trim();
	try {
		const url = new URL(raw);
		if (url.protocol !== "http:" && url.protocol !== "https:") {
			return new URL("http://127.0.0.1:8000");
		}
		return url;
	} catch {
		return new URL("http://127.0.0.1:8000");
	}
}

/** Host and port only; path, query and credentials are dropped. */
export function apiHostOnly(): string {
	return origin().host;
}

export function apiDocsHref(): string {
	const url = origin();
	return `${url.protocol}//${url.host}/docs`;
}

export function apiTokenConfigured(): boolean {
	return Boolean(process.env.EZCHAIN_API_TOKEN);
}

export function publicUrlConfigured(): boolean {
	return Boolean(process.env.EZCHAIN_PUBLIC_URL);
}
