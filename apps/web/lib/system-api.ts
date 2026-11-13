// Typed reads for the operational endpoints that live at the API root, not under `/api`.
//
// These run on the Next server only. `/healthz` and `/readyz` are public probes;
// `/health` and `/metrics` carry the server-side operator token because the API
// serves them only to loopback or to an authenticated caller. Fixture mode reports
// "not probed" rather than inventing a healthy process.

import { serverAuthorizationHeader } from "./api-auth";

export type SystemMode = "fixture" | "live";

export type SystemResult<T> =
	| { ok: true; mode: SystemMode; data: T }
	| { ok: false; mode: SystemMode; error: { code: string; message: string } };

export interface ServiceHealth {
	status: string;
	db: string;
	mode: string;
	engine_inline: boolean;
	market_scan_inline: boolean;
	market_monitor: boolean;
	version: string;
}

export interface Liveness {
	status: string;
	/** The mode the API derived (ADR-0035). Absent on an older or partial answer. */
	mode?: { execution?: string; live_available?: boolean; live_blocked_reason?: string | null };
}

export interface Readiness {
	status: string;
	db: string;
	migration: string;
	revision: string | null;
	head: string | null;
}

export interface MetricsText {
	text: string;
}

function mode(): SystemMode {
	return process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
}

function origin(): string {
	return (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function readJson<T>(path: string, acceptStatuses: number[]): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${origin()}${path}`, {
			headers: { accept: "application/json", ...serverAuthorizationHeader() },
			cache: "no-store",
		});
	} catch (error) {
		throw new Error(
			`API request failed: ${error instanceof Error ? error.message : String(error)}`,
		);
	}
	const text = await response.text();
	let payload: unknown = null;
	if (text) {
		try {
			payload = JSON.parse(text);
		} catch {
			payload = text;
		}
	}
	if (!response.ok && !acceptStatuses.includes(response.status)) {
		const body = (payload ?? {}) as Record<string, unknown>;
		const detail = body.detail;
		const envelope =
			detail && typeof detail === "object" && !Array.isArray(detail)
				? (detail as Record<string, unknown>)
				: null;
		const code =
			(typeof envelope?.code === "string" ? envelope.code : undefined) ??
			(typeof body.code === "string" ? body.code : undefined) ??
			`HTTP_${response.status}`;
		const message =
			(typeof envelope?.message === "string" ? envelope.message : undefined) ??
			(typeof detail === "string" ? detail : undefined) ??
			(typeof body.message === "string" ? body.message : undefined) ??
			`API returned ${response.status}`;
		throw new Error(`${code}: ${message}`);
	}
	return payload as T;
}

async function capture<T>(work: () => Promise<T>): Promise<SystemResult<T>> {
	const current = mode();
	try {
		return { ok: true, mode: current, data: await work() };
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		const match = /^([A-Z][A-Z0-9_]+): ([\s\S]*)$/.exec(message);
		const code = match
			? match[1]!
			: message.startsWith("API request failed")
				? "UPSTREAM_UNREACHABLE"
				: "API_ERROR";
		return {
			ok: false,
			mode: current,
			error: { code, message },
		};
	}
}

const FIXTURE_MESSAGE =
	"This probe is not run in fixture mode. Set EZCHAIN_WEB_MODE=live against a running API to see the real process state.";

export async function getLiveness(): Promise<SystemResult<Liveness>> {
	const current = mode();
	if (current === "fixture") {
		return {
			ok: false,
			mode: current,
			error: { code: "FIXTURE_NOT_PROBED", message: FIXTURE_MESSAGE },
		};
	}
	return capture(() => readJson<Liveness>("/healthz", []));
}

// The execution mode is derived by the API, never by this client (ADR-0035). `/healthz`
// is the public read that carries it, so the shell reads the same value a probe would.
// A refusal or a payload without a recognised mode is an error, not a default: the chip
// must be able to say it does not know.
export type ExecutionMode = "paper" | "live";

export async function getExecutionMode(): Promise<SystemResult<ExecutionMode>> {
	const current = mode();
	if (current === "fixture") {
		return {
			ok: false,
			mode: current,
			error: { code: "FIXTURE_NOT_PROBED", message: FIXTURE_MESSAGE },
		};
	}
	return capture(async () => {
		const liveness = await readJson<Liveness>("/healthz", []);
		const execution = liveness.mode?.execution;
		if (execution !== "paper" && execution !== "live") {
			throw new Error(
				`MODE_NOT_REPORTED: /healthz did not report a recognised execution mode (got ${String(
					execution ?? "nothing",
				)})`,
			);
		}
		return execution;
	});
}

export async function getServiceHealth(): Promise<SystemResult<ServiceHealth>> {
	const current = mode();
	if (current === "fixture") {
		return {
			ok: false,
			mode: current,
			error: { code: "FIXTURE_NOT_PROBED", message: FIXTURE_MESSAGE },
		};
	}
	return capture(() => readJson<ServiceHealth>("/health", [503]));
}

export async function getReadiness(): Promise<SystemResult<Readiness>> {
	const current = mode();
	if (current === "fixture") {
		return {
			ok: false,
			mode: current,
			error: { code: "FIXTURE_NOT_PROBED", message: FIXTURE_MESSAGE },
		};
	}
	return capture(() => readJson<Readiness>("/readyz", [503]));
}

export async function getMetrics(): Promise<SystemResult<MetricsText>> {
	const current = mode();
	if (current === "fixture") {
		return {
			ok: false,
			mode: current,
			error: { code: "FIXTURE_NOT_PROBED", message: FIXTURE_MESSAGE },
		};
	}
	return capture(async () => {
		let response: Response;
		try {
			response = await fetch(`${origin()}/metrics`, {
				headers: { accept: "text/plain", ...serverAuthorizationHeader() },
				cache: "no-store",
			});
		} catch (error) {
			throw new Error(
				`API request failed: ${error instanceof Error ? error.message : String(error)}`,
			);
		}
		if (!response.ok) {
			throw new Error(`HTTP_${response.status}: metrics returned ${response.status}`);
		}
		return { text: await response.text() };
	});
}
