// Typed client for position protection arms and profiles.
//
// The backend surface lives on this branch: POST/GET/PATCH/DELETE /api/v1/protections and
// /api/v1/protection-profiles. Reads swap for the fixture armory when
// EZCHAIN_WEB_MODE=fixture; unknown money and unknown states stay null/— and are never
// rendered as zero by the pages. Mutations are browser calls in live mode; in fixture
// mode the pages keep changes in the browser session (lib/protection-session.ts),
// exactly like the strategy fixture session — a fixture build never invents API results.

import type { Tone } from "./dashboard-fixtures";
import { MINUS, usd } from "./format";
import { apiCapture, apiRequest, webMode, type PaperResult } from "./paper-api";

export type ProtectionKind = "stop_loss" | "trailing_stop" | "take_profit";

/** One armed protection rule as the API stores it. Money fields are decimal strings. */
export interface Protection {
	id: string;
	chain: string;
	token_address: string;
	kind: string;
	params: Record<string, unknown>;
	source: string;
	status: string;
	version: number;
	profile_id: string | null;
	trailing_activated: boolean;
	trailing_peak_usd: string | null;
	trailing_rung_index: number;
	created_at: string;
	updated_at: string | null;
}

/** A named defaults profile. `chain: null` is the global fallback. */
export interface ProtectionProfile {
	id: string;
	name: string;
	profile_type: string;
	chain: string | null;
	defaults: Record<string, unknown>;
	description: string | null;
	created_at: string;
}

export interface ProtectionArmInput {
	chain: string;
	token_address: string;
	kind: ProtectionKind;
	params: Record<string, unknown>;
	idempotency_key: string;
	profile_id?: string;
}

export interface ProtectionPatchInput {
	params?: Record<string, unknown>;
	expected_version?: number;
	profile_id?: string;
}

const KIND_NAMES: Record<string, string> = {
	stop_loss: "Stop loss",
	trailing_stop: "Trailing stop",
	take_profit: "Take profit",
};

export function kindLabel(kind: string): string {
	return KIND_NAMES[kind] ?? kind;
}

/** The persisted status is shown as persisted; an unknown status prints —, never a guess. */
export function protectionStatusMeta(status: string): { label: string; tone: Tone } {
	switch (status) {
		case "armed":
			return { label: "armed", tone: "ok" };
		case "triggered":
			return { label: "triggered", tone: "info" };
		case "cancelled":
			return { label: "cancelled", tone: "ghost" };
		case "expired":
			return { label: "EXPIRED", tone: "warn" };
		default:
			return { label: "—", tone: "ghost" };
	}
}

function numeric(params: Record<string, unknown>, key: string): string | null {
	const value = params[key];
	if (value === undefined || value === null) return null;
	const amount = Number(value);
	return Number.isFinite(amount) ? String(value) : null;
}

function fractionLabel(params: Record<string, unknown>): string {
	const raw = numeric(params, "sell_fraction");
	if (raw === null) return "";
	const amount = Number(raw);
	if (!Number.isFinite(amount) || amount >= 1) return "";
	const percent = Math.round(amount * 100);
	return ` · sell ${percent}%`;
}

/** Human summary of the stored params. Anything the rule does not define prints —. */
export function paramsSummary(kind: string, params: Record<string, unknown>): string {
	const sell = fractionLabel(params);
	if (kind === "stop_loss") {
		const lossPct = numeric(params, "loss_pct");
		const stopPrice = numeric(params, "stop_price_usd");
		const parts: string[] = [];
		if (lossPct !== null) parts.push(`${MINUS}${lossPct}% from cost`);
		if (stopPrice !== null) parts.push(`stop $${stopPrice}`);
		return parts.length > 0 ? `${parts.join(" or ")}${sell}` : "—";
	}
	if (kind === "trailing_stop") {
		const activation = numeric(params, "activation_pct");
		const givebackPct = numeric(params, "giveback_pct");
		const givebackUsd = numeric(params, "giveback_price_usd");
		if (activation === null) return "—";
		let summary = `arm +${activation}%`;
		if (givebackPct !== null) summary += ` · give back ${givebackPct}%`;
		if (givebackUsd !== null) summary += ` · give back $${givebackUsd}`;
		return `${summary}${sell}`;
	}
	if (kind === "take_profit") {
		const ladder = params.ladder;
		if (Array.isArray(ladder) && ladder.length > 0) {
			const rungs = ladder
				.map((rung) => {
					if (typeof rung !== "object" || rung === null) return null;
					const profit = numeric(rung as Record<string, unknown>, "profit_pct");
					const fraction = numeric(rung as Record<string, unknown>, "fraction");
					if (profit === null || fraction === null) return null;
					return `+${profit}%→${Math.round(Number(fraction) * 100)}%`;
				})
				.filter((rung): rung is string => rung !== null);
			if (rungs.length === 0) return "—";
			return `ladder ${rungs.length} rung${rungs.length === 1 ? "" : "s"} · ${rungs.join(" · ")}`;
		}
		const profit = numeric(params, "profit_pct");
		if (profit === null) return "—";
		return `+${profit}%${sell}`;
	}
	return "—";
}

/** Trailing state comes from the API record, not from a client-side recomputation. */
export function trailingDetail(arm: Protection): string {
	if (arm.kind !== "trailing_stop") return "";
	if (!arm.trailing_activated) {
		const activation = numeric(arm.params, "activation_pct");
		return `waiting for +${activation ?? "—"}%`;
	}
	const peak = arm.trailing_peak_usd === null ? null : Number(arm.trailing_peak_usd);
	return `activated · peak ${peak === null || !Number.isFinite(peak) ? "—" : usd(peak)}`;
}

// The same public token addresses the paper fixtures use, so arm rows line up with the
// position rows a fixture build shows.
const BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const AERO = "0x940181a94A35A4569E4529A3CDfB74e38FD98631";
const SCAM = "5z3EqYQo9HiCEs3R84RCDMu2n7anpDMxR3T7dRntZ0bV";

export const fixtureProtectionProfiles: ProtectionProfile[] = [
	{
		id: "prof-house",
		name: "House default",
		profile_type: "manual",
		chain: null,
		defaults: { stop_loss: { loss_pct: 20 }, take_profit: { profit_pct: 30 } },
		description: "Global fallback for every chain without its own profile.",
		created_at: "2026-11-01T10:00:00Z",
	},
	{
		id: "prof-base-swing",
		name: "Base swing",
		profile_type: "smart",
		chain: "base",
		defaults: { trailing_stop: { activation_pct: 10, giveback_pct: 25 } },
		description: "Wider trailing room for Base positions.",
		created_at: "2026-11-02T10:00:00Z",
	},
	{
		id: "prof-solana-degen",
		name: "Solana degen",
		profile_type: "smart",
		chain: "solana",
		defaults: { stop_loss: { loss_pct: 25 } },
		description: "Tighter risk on Solana momentum entries.",
		created_at: "2026-11-03T10:00:00Z",
	},
];

export const fixtureProtections: Protection[] = [
	{
		id: "prot-fixture-bonk-stop",
		chain: "solana",
		token_address: BONK,
		kind: "stop_loss",
		params: { loss_pct: "15" },
		source: "manual",
		status: "armed",
		version: 1,
		profile_id: "prof-house",
		trailing_activated: false,
		trailing_peak_usd: null,
		trailing_rung_index: 0,
		created_at: "2026-11-07T09:12:00Z",
		updated_at: "2026-11-07T09:12:00Z",
	},
	{
		id: "prot-fixture-bonk-ladder",
		chain: "solana",
		token_address: BONK,
		kind: "take_profit",
		params: {
			ladder: [
				{ profit_pct: 25, fraction: 0.5 },
				{ profit_pct: 60, fraction: 0.5 },
			],
		},
		source: "manual",
		status: "expired",
		version: 2,
		profile_id: null,
		trailing_activated: false,
		trailing_peak_usd: null,
		trailing_rung_index: 2,
		created_at: "2026-11-06T18:40:00Z",
		updated_at: null,
	},
	{
		id: "prot-fixture-aero-trail",
		chain: "base",
		token_address: AERO,
		kind: "trailing_stop",
		params: { activation_pct: "10", giveback_pct: "25" },
		source: "smart",
		status: "armed",
		version: 3,
		profile_id: "prof-base-swing",
		trailing_activated: true,
		trailing_peak_usd: "1.4124",
		trailing_rung_index: 0,
		created_at: "2026-11-06T12:00:00Z",
		updated_at: "2026-11-07T09:40:00Z",
	},
	{
		id: "prot-fixture-aero-tp",
		chain: "base",
		token_address: AERO,
		kind: "take_profit",
		params: { profit_pct: "40", sell_fraction: "0.5" },
		source: "manual",
		status: "triggered",
		version: 1,
		profile_id: "prof-house",
		trailing_activated: false,
		trailing_peak_usd: null,
		trailing_rung_index: 0,
		created_at: "2026-11-06T15:30:00Z",
		updated_at: "2026-11-07T08:05:00Z",
	},
	{
		id: "prot-fixture-scam-stop",
		chain: "solana",
		token_address: SCAM,
		kind: "stop_loss",
		params: { stop_price_usd: "0.00000030" },
		source: "manual",
		status: "cancelled",
		version: 2,
		profile_id: "prof-house",
		trailing_activated: false,
		trailing_peak_usd: null,
		trailing_rung_index: 0,
		created_at: "2026-11-05T11:00:00Z",
		updated_at: "2026-11-07T08:55:00Z",
	},
];

function cloneArms(arms: Protection[]): Protection[] {
	return arms.map((arm) => ({ ...arm, params: { ...arm.params } }));
}

export async function getProtections(): Promise<PaperResult<Protection[]>> {
	return apiCapture(async () => {
		if (webMode() === "fixture") return cloneArms(fixtureProtections);
		return apiRequest<Protection[]>("/protections");
	});
}

export async function getProtectionProfiles(): Promise<PaperResult<ProtectionProfile[]>> {
	return apiCapture(async () => {
		if (webMode() === "fixture") {
			return fixtureProtectionProfiles.map((profile) => ({
				...profile,
				defaults: { ...profile.defaults },
			}));
		}
		return apiRequest<ProtectionProfile[]>("/protection-profiles");
	});
}

// Mutations always reach the API in live mode — the pages never present a local change
// as a persisted one. Fixture mode keeps the session store instead (see components).
export async function armProtection(input: ProtectionArmInput): Promise<Protection> {
	return apiRequest<Protection>("/protections", {
		method: "POST",
		body: JSON.stringify(input),
	});
}

export async function patchProtection(
	id: string,
	patch: ProtectionPatchInput,
): Promise<Protection> {
	return apiRequest<Protection>(`/protections/${id}`, {
		method: "PATCH",
		body: JSON.stringify(patch),
	});
}

/** Disarm returns the row with status "cancelled": a state change, never a deletion. */
export async function disarmProtection(id: string): Promise<Protection> {
	return apiRequest<Protection>(`/protections/${id}`, { method: "DELETE" });
}
