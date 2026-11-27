// Typed client for the build-source flag. The API performs the signature check; this
// module renders the verdict and never decides for itself what counts as official. In
// sample-data mode it refuses to show a build claim rather than inventing one.

import { serverAuthorizationHeader } from "@/lib/api-auth";
import { webMode } from "@/lib/runtime";

export interface BuildSource {
	official: boolean;
	status: string;
	message: string;
	tag: string | null;
}

export type BuildSourceResult =
	| { ok: true; data: BuildSource }
	| { ok: false; error: { code: string; message: string } };

function apiBase(): string {
	const raw = (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").trim();
	try {
		const url = new URL(raw);
		if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("bad protocol");
		return `${url.protocol}//${url.host}`;
	} catch {
		return "http://127.0.0.1:8000";
	}
}

export async function getBuildSource(): Promise<BuildSourceResult> {
	if (webMode() === "fixture") {
		return {
			ok: true,
			data: {
				official: false,
				status: "fixture",
				message:
					"Sample data mode cannot check a build signature. Open a release build to see the result.",
				tag: null,
			},
		};
	}
	try {
		const response = await fetch(`${apiBase()}/api/v1/system/build-source`, {
			cache: "no-store",
			headers: {
				"content-type": "application/json",
				...serverAuthorizationHeader(),
			},
		});
		if (!response.ok) throw new Error(`the API returned HTTP ${response.status}`);
		return { ok: true, data: (await response.json()) as BuildSource };
	} catch (error) {
		return {
			ok: false,
			error: {
				code: "API_UNREACHABLE",
				message:
					error instanceof Error ? error.message : "the build-source endpoint could not be reached",
			},
		};
	}
}
