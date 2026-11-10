// Typed client for the position-protection surface.
//
// EZCHAIN_WEB_MODE=fixture keeps a browser-session copy of the arms so the
// positions form and the automations list agree without an API. Live mode
// always reaches /api/v1/protection. Unknown money values stay null and the
// pages print an em dash — they never become zero.

import { serverAuthorizationHeader } from "./api-auth";

export type PaperApiMode = "fixture" | "live";
export type ProtectionKind = "stop_loss" | "trailing_stop" | "take_profit";
export type ProtectionStatus = "armed" | "triggered" | "cancelled" | "expired";
export type ProfileKind = "manual" | "smart";

export interface ProtectionArm {
	id: string;
	chain: string;
	token_address: string;
	token_symbol: string;
	kind: ProtectionKind;
	status: ProtectionStatus;
	params: Record<string, string | number | unknown>;
	peak_usd: string | null;
	triggered_levels: number[];
	triggered_at: string | null;
	order_id: string | null;
	expires_at: string | null;
	profile_id: string | null;
	version: number;
	created_at: string;
	updated_at: string;
	mode: "paper";
}

export interface ProtectionProfile {
	id: string;
	name: string;
	chain: string | null;
	kind: ProfileKind;
	stop_loss_pct: string | null;
	trailing_pct: string | null;
	take_profit_pct: string | null;
	rules: string[];
	created_at: string;
	updated_at: string;
	mode: "paper";
}

export type ProtectionResult<T> =
	| { ok: true; mode: PaperApiMode; data: T }
	| { ok: false; mode: PaperApiMode; error: { code: string; message: string } };

export interface ProtectionArmInput {
	chain: string;
	token_address: string;
	token_symbol: string;
	kind: ProtectionKind;
	params: Record<string, unknown>;
	profile_id?: string | null;
	expires_at?: string | null;
}

export interface ProtectionArmPatch {
	params: Record<string, unknown>;
	expected_version?: number;
}

export class ProtectionApiError extends Error {
	readonly code: string;
	readonly status: number;
	constructor(code: string, message: string, status = 0) {
		super(message);
		this.name = "ProtectionApiError";
		this.code = code;
		this.status = status;
	}
}

export function webMode(): PaperApiMode {
	return process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
}

function apiBase() {
	if (typeof window !== "undefined") return "";
	return (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

interface ErrorEnvelope {
	code?: string;
	message?: string;
	detail?: { code?: string; message?: string } | string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(`${apiBase()}/api/v1${path}`, {
		...init,
		cache: "no-store",
		headers: {
			"content-type": "application/json",
			...(init?.headers ?? {}),
			...serverAuthorizationHeader(),
		},
	});
	if (!response.ok) {
		let body: ErrorEnvelope = {};
		try {
			body = (await response.json()) as ErrorEnvelope;
		} catch {
			// Non-JSON bodies still fail closed with the HTTP status.
		}
		const detail = body.detail;
		const code =
			(typeof detail === "object" && detail?.code) ||
			body.code ||
			`HTTP_${response.status}`;
		const message =
			(typeof detail === "object" && detail?.message) ||
			(typeof detail === "string" ? detail : undefined) ||
			body.message ||
			`request failed with status ${response.status}`;
		throw new ProtectionApiError(code, message, response.status);
	}
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

async function capture<T>(work: () => Promise<T>): Promise<ProtectionResult<T>> {
	const mode = webMode();
	try {
		return { ok: true, mode, data: await work() };
	} catch (error) {
		const apiError = error instanceof ProtectionApiError ? error : null;
		return {
			ok: false,
			mode,
			error: {
				code: apiError?.code ?? "API_UNREACHABLE",
				message:
					apiError?.message ??
					(error instanceof Error ? error.message : "the API could not be reached"),
			},
		};
	}
}

const STORE_KEY = "ezchain-fixture-protection-arms";

function now() {
	return new Date().toISOString();
}

export function readFixtureArms(): ProtectionArm[] {
	if (typeof window === "undefined") return [];
	try {
		const raw = window.sessionStorage.getItem(STORE_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw) as ProtectionArm[];
		return Array.isArray(parsed) ? parsed : [];
	} catch {
		return [];
	}
}

export function writeFixtureArms(arms: ProtectionArm[]) {
	if (typeof window === "undefined") return;
	try {
		window.sessionStorage.setItem(STORE_KEY, JSON.stringify(arms));
	} catch {
		// Storage is best-effort; the client state still shows the arm.
	}
}

export const fixtureProtectionArms: ProtectionArm[] = [];

export const fixtureProtectionProfiles: ProtectionProfile[] = [
	{
		id: "profile-global-guarded",
		name: "Guarded",
		chain: null,
		kind: "smart",
		stop_loss_pct: "8",
		trailing_pct: "5",
		take_profit_pct: "20",
		rules: ["Stop out at 8% below entry.", "Trail 5% below the highest price."],
		created_at: now(),
		updated_at: now(),
		mode: "paper",
	},
	{
		id: "profile-solana-tight",
		name: "Tight",
		chain: "solana",
		kind: "manual",
		stop_loss_pct: "5",
		trailing_pct: null,
		take_profit_pct: null,
		rules: [],
		created_at: now(),
		updated_at: now(),
		mode: "paper",
	},
];

export async function getProtectionArms(): Promise<ProtectionResult<ProtectionArm[]>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return [...readFixtureArms()];
		}
		const rows = await request<ProtectionArm[]>("/protection/arms");
		return rows.map(normalizeArm);
	});
}

export async function getProtectionProfiles(): Promise<ProtectionResult<ProtectionProfile[]>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return fixtureProtectionProfiles.map((profile) => ({ ...profile, rules: [...profile.rules] }));
		}
		return request<ProtectionProfile[]>("/protection/profiles");
	});
}

export async function getEffectiveProfile(
	chain: string,
): Promise<ProtectionResult<ProtectionProfile | null>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			const perChain = fixtureProtectionProfiles.find((p) => p.chain === chain);
			if (perChain) return { ...perChain, rules: [...perChain.rules] };
			const global = fixtureProtectionProfiles.find((p) => p.chain === null);
			return global ? { ...global, rules: [...global.rules] } : null;
		}
		const body = await request<{ chain: string; profile: ProtectionProfile | null }>(
			`/protection/profiles/effective?chain=${encodeURIComponent(chain)}`,
		);
		return body.profile;
	});
}

export async function createProtectionArm(
	input: ProtectionArmInput,
	mode: PaperApiMode = webMode(),
): Promise<ProtectionArm> {
	if (mode === "fixture") {
		const arm: ProtectionArm = {
			id: `arm-${Date.now().toString(36)}`,
			chain: input.chain,
			token_address: input.token_address,
			token_symbol: input.token_symbol,
			kind: input.kind,
			status: "armed",
			params: { ...input.params },
			peak_usd: null,
			triggered_levels: [],
			triggered_at: null,
			order_id: null,
			expires_at: input.expires_at ?? null,
			profile_id: input.profile_id ?? null,
			version: 1,
			created_at: now(),
			updated_at: now(),
			mode: "paper",
		};
		const arms = [arm, ...readFixtureArms()];
		writeFixtureArms(arms);
		return arm;
	}
	return normalizeArm(await request<ProtectionArm>("/protection/arms", {
		method: "POST",
		body: JSON.stringify(input),
	}));
}

export async function patchProtectionArm(
	id: string,
	patch: ProtectionArmPatch,
	mode: PaperApiMode = webMode(),
): Promise<ProtectionArm> {
	if (mode === "fixture") {
		const arms = readFixtureArms();
		const found = arms.find((arm) => arm.id === id);
		if (!found) throw new ProtectionApiError("ARM_NOT_FOUND", "protection arm not found", 404);
		if (patch.expected_version !== undefined && patch.expected_version !== found.version) {
			throw new ProtectionApiError(
				"PROTECTION_CONFLICT",
				"protection arm version mismatch",
				409,
			);
		}
		const updated = {
			...found,
			params: { ...patch.params },
			version: found.version + 1,
			updated_at: now(),
		};
		writeFixtureArms(arms.map((arm) => (arm.id === id ? updated : arm)));
		return updated;
	}
	return normalizeArm(await request<ProtectionArm>(`/protection/arms/${id}`, {
		method: "PATCH",
		body: JSON.stringify(patch),
	}));
}

export async function cancelProtectionArm(id: string, mode: PaperApiMode = webMode()): Promise<ProtectionArm> {
	if (mode === "fixture") {
		const arms = readFixtureArms();
		const found = arms.find((arm) => arm.id === id);
		if (!found) throw new ProtectionApiError("ARM_NOT_FOUND", "protection arm not found", 404);
		const updated = {
			...found,
			status: "cancelled" as const,
			version: found.version + 1,
			updated_at: now(),
		};
		writeFixtureArms(arms.map((arm) => (arm.id === id ? updated : arm)));
		return updated;
	}
	return normalizeArm(await request<ProtectionArm>(`/protection/arms/${id}/cancel`, {
		method: "POST",
		body: JSON.stringify({}),
	}));
}

function normalizeArm(row: ProtectionArm): ProtectionArm {
	return {
		...row,
		params: row.params ?? {},
		triggered_levels: row.triggered_levels ?? [],
		version: typeof row.version === "number" ? row.version : 1,
	};
}

export function armLabel(kind: ProtectionKind): string {
	if (kind === "stop_loss") return "stop-loss";
	if (kind === "trailing_stop") return "trailing stop";
	return "take-profit";
}

export function armSummary(arm: ProtectionArm): string {
	const p = arm.params as Record<string, unknown>;
	const price = typeof p.price_usd === "string" ? p.price_usd : null;
	if (price) return price;
	const loss = p.loss_pct ?? p.trailing_pct ?? p.gain_pct;
	if (typeof loss === "string" || typeof loss === "number") return `${loss}%`;
	return "—";
}
