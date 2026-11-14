// Typed client for the trade-policy surface.
//
// One store, reached only through `GET /api/v1/trade-policy` and the per-strategy `PUT`.
// The page renders the profiles and fields the API returns (value, range, description and
// source) and never invents a limit. The preview is the server's own sizing verdict, not a
// browser calculation: in fixture mode it is refused rather than faked.
//
// `TRADE_POLICY_FIELD_KEYS` and `TRADE_POLICY_PROFILE_ORDER` live in `trade-policy-keys.ts`
// so a Python test can compare this surface with the API registry in both directions.

import { serverAuthorizationHeader } from "./api-auth";
import { webMode } from "./paper-api";
import { fixtureStrategies } from "./strategy-fixtures";
import { TRADE_POLICY_FIELD_KEYS, TRADE_POLICY_PROFILE_ORDER } from "./trade-policy-keys";

export type PolicyKind = "decimal" | "integer" | "ratio";

export interface PolicyField {
	key: string;
	label: string;
	unit: string;
	kind: PolicyKind;
	description: string;
	value: string | number;
	minimum: string;
	maximum: string;
}

export interface PolicyProfile {
	name: string;
	label: string;
	description: string;
	fields: PolicyField[];
}

export interface PolicyWarning {
	code: string;
	message: string;
	detail?: Record<string, unknown> | null;
}

export interface StrategyPolicy {
	id: string;
	name: string;
	token_symbol: string;
	risk_profile: string | null;
	effective_profile: string | null;
	source: "strategy" | "default";
	order_usd: string | null;
	max_position_usd: string | null;
	warnings: PolicyWarning[];
}

export interface TradePolicyDocument {
	mode: "paper";
	active_profile: string | null;
	profiles: PolicyProfile[];
	fields: PolicyField[];
	strategies: StrategyPolicy[];
}

export interface PreviewLimits {
	min_order_usd: string;
	max_order_usd: string;
	max_order_pct_of_equity: string;
	per_token_cap_usd: string;
	max_slippage_bps: number;
}

export interface TradePolicyPreview {
	allowed: boolean;
	profile: string;
	amount_usd: string;
	equity_usd: string | null;
	limits: PreviewLimits;
	percent_cap_usd: string | null;
	effective_max_usd: string;
	reasons: { code: string; message: string; detail: Record<string, unknown> | null }[];
}

export type TradePolicyResult<T> =
	| { ok: true; mode: "fixture" | "live"; data: T }
	| { ok: false; mode: "fixture" | "live"; error: { code: string; message: string } };

export class TradePolicyApiError extends Error {
	constructor(
		readonly code: string,
		message: string,
		readonly status?: number,
	) {
		super(message);
		this.name = "TradePolicyApiError";
	}
}

const API_URL = process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000";

function apiBase(): string {
	return typeof window === "undefined" ? API_URL : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(`${apiBase()}/api/v1${path}`, {
		...init,
		cache: "no-store",
		headers: {
			"content-type": "application/json",
			accept: "application/json",
			...(init?.headers ?? {}),
			...serverAuthorizationHeader(),
		},
	});
	if (!response.ok) {
		let payload: unknown = null;
		try {
			payload = await response.json();
		} catch {
			payload = null;
		}
		const detail = (payload as { detail?: unknown } | null)?.detail;
		const code =
			typeof detail === "object" && detail && "code" in detail
				? String((detail as { code: unknown }).code)
				: `HTTP_${response.status}`;
		const message =
			typeof detail === "object" && detail && "message" in detail
				? String((detail as { message: unknown }).message)
				: `trade-policy request failed with status ${response.status}`;
		throw new TradePolicyApiError(code, message, response.status);
	}
	return (await response.json()) as T;
}

const PROFILE_META: Record<string, { label: string; description: string; values: string[] }> = {
	low: {
		label: "Low risk",
		description:
			"Smaller sizes, a tight slippage ceiling and a low per-token cap. Choosing it can only tighten the deployment default.",
		values: ["5", "50", "0.02", "100", "100"],
	},
	mid: {
		label: "Mid risk",
		description:
			"The balanced profile: a $5 minimum, a $100 absolute maximum and at most 5% of equity in one buy.",
		values: ["5", "100", "0.05", "250", "200"],
	},
	high: {
		label: "High risk",
		description:
			"Larger sizes and a wider slippage ceiling for a deliberately aggressive account. Choosing it loosens a fund-affecting limit and needs acknowledgement.",
		values: ["1", "250", "0.10", "500", "500"],
	},
};

const FIELDS: Omit<PolicyField, "value">[] = [
	{
		key: "min_order_usd",
		label: "Minimum order size",
		unit: "USD",
		kind: "decimal",
		description:
			"A buy below this size is refused with SIZE_BELOW_MIN. The percentage floor is a separate bound; both apply.",
		minimum: "0",
		maximum: "1000000",
	},
	{
		key: "max_order_usd",
		label: "Maximum order size",
		unit: "USD",
		kind: "decimal",
		description:
			"The absolute ceiling for one buy. A size above it is refused with SIZE_ABOVE_MAX.",
		minimum: "1",
		maximum: "1000000",
	},
	{
		key: "max_order_pct_of_equity",
		label: "Maximum share of equity",
		unit: "ratio",
		kind: "ratio",
		description:
			"A buy may use at most this share of account equity, as a ratio (0.05 = 5%). When equity is unknown the buy is refused with EQUITY_UNKNOWN.",
		minimum: "0",
		maximum: "1",
	},
	{
		key: "per_token_cap_usd",
		label: "Per-token exposure cap",
		unit: "USD",
		kind: "decimal",
		description:
			"The largest USD value one token may reach, including the proposed buy. Reduce-only sells are never blocked by it.",
		minimum: "0",
		maximum: "1000000",
	},
	{
		key: "max_slippage_bps",
		label: "Maximum slippage",
		unit: "bps",
		kind: "integer",
		description:
			"The worst execution cost a new buy may accept, in basis points. A sell is never blocked by it.",
		minimum: "0",
		maximum: "10000",
	},
];

function fixtureProfile(name: keyof typeof PROFILE_META): PolicyProfile {
	const meta = PROFILE_META[name];
	const values = Object.fromEntries(
		TRADE_POLICY_FIELD_KEYS.map((key, index) => [key, meta.values[index]]),
	);
	return {
		name,
		label: meta.label,
		description: meta.description,
		fields: FIELDS.map((field) => ({
			...field,
			value:
				field.kind === "integer" ? Number(values[field.key]) : String(values[field.key]),
		})),
	};
}

/** The documented defaults a fixture build shows. Nothing here is a saved override. */
export function fixtureTradePolicyDocument(): TradePolicyDocument {
	const profiles = TRADE_POLICY_PROFILE_ORDER.map((name) => fixtureProfile(name));
	return {
		mode: "paper",
		active_profile: "mid",
		profiles,
		fields: profiles.find((profile) => profile.name === "mid")?.fields ?? [],
		strategies: fixtureStrategies.map((strategy) => ({
			id: strategy.id,
			name: strategy.name,
			token_symbol: strategy.token_symbol,
			risk_profile: null,
			effective_profile: "mid",
			source: "default" as const,
			order_usd: String(strategy.params.order_usd ?? ""),
			max_position_usd: String(strategy.params.max_position_usd ?? ""),
			warnings: [],
		})),
	};
}

export async function getTradePolicy(): Promise<TradePolicyResult<TradePolicyDocument>> {
	const mode = webMode();
	if (mode === "fixture") {
		return { ok: true, mode, data: fixtureTradePolicyDocument() };
	}
	try {
		return { ok: true, mode, data: await request<TradePolicyDocument>("/trade-policy") };
	} catch (error) {
		return { ok: false, mode, error: describe(error) };
	}
}

/** Persist a strategy override. Never simulated: a fixture build refuses honestly. */
export async function updateStrategyPolicy(
	strategyId: string,
	riskProfile: string | null,
	acknowledgeRisk = false,
): Promise<StrategyPolicy> {
	if (webMode() === "fixture") {
		throw new TradePolicyApiError(
			"FIXTURE_UNAVAILABLE",
			"Changing a strategy profile is a real write; it is not simulated in fixture mode.",
		);
	}
	return request<StrategyPolicy>(`/trade-policy/strategies/${encodeURIComponent(strategyId)}`, {
		method: "PUT",
		body: JSON.stringify({
			risk_profile: riskProfile,
			acknowledge_risk: acknowledgeRisk,
		}),
	});
}

/** The server's sizing verdict. Fixture mode refuses rather than computing a fake one. */
export async function previewTradePolicy(input: {
	profile: string | null;
	amount_usd: string;
	equity_usd: string | null;
}): Promise<TradePolicyPreview> {
	if (webMode() === "fixture") {
		throw new TradePolicyApiError(
			"FIXTURE_UNAVAILABLE",
			"Sizing is checked by the API; a fixture build cannot answer for it.",
		);
	}
	const params = new URLSearchParams({ amount_usd: input.amount_usd });
	if (input.profile) params.set("profile", input.profile);
	if (input.equity_usd) params.set("equity_usd", input.equity_usd);
	return request<TradePolicyPreview>(`/trade-policy/preview?${params.toString()}`);
}

function describe(error: unknown): { code: string; message: string } {
	if (error instanceof TradePolicyApiError) {
		return { code: error.code, message: error.message };
	}
	return {
		code: "API_UNREACHABLE",
		message: error instanceof Error ? error.message : "the trade-policy API could not be reached",
	};
}
