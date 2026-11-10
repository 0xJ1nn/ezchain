// Typed client for the signals read model.
//
// The ranking is a server read model: this module asks for movers for one window and
// one direction and renders what comes back. It never sorts, filters or picks a row.
// In fixture mode the answer comes from the local sample; the page labels it FIXTURE.

import { ApiError, webMode, type Source } from "./api";

export type SignalWindow = "h1" | "h24";
export type SignalDirection = "up" | "down";

/** Fixture-only states, used to exercise the empty and error surfaces offline. */
export type SignalFixtureState = "empty" | "error";

export interface SignalMover {
	rank: number;
	chain: string;
	token_address: string;
	pair_address: string;
	dex: string;
	symbol: string;
	name: string;
	price_usd: string;
	liquidity_usd: string;
	volume_24h_usd: string | null;
	change_pct: string;
	source: Source;
	observed_at: string;
	age_seconds: number;
}

/** Why a row of the market read did not reach the table. */
export interface SignalExclusions {
	unknown_token: number;
	duplicate_token: number;
	stale: number;
	unknown_change: number;
	unknown_liquidity: number;
	below_liquidity_floor: number;
	wrong_direction: number;
}

export interface SignalAppliedFilters {
	chain: string;
	window: SignalWindow;
	direction: SignalDirection;
	min_liquidity_usd: string;
	max_age_seconds: number;
	candidate_rows: number;
	candidate_sort: string;
	limit: number;
}

export interface SignalMovers {
	window: SignalWindow;
	direction: SignalDirection;
	as_of: string;
	rows: SignalMover[];
	total: number;
	limit: number;
	truncated: boolean;
	/** Present when the list is empty. No mover is invented to fill it. */
	reason: string | null;
	rows_read: number;
	exclusions: SignalExclusions;
	applied: SignalAppliedFilters;
	source: Source;
	stale: boolean;
}

export interface SignalQuery {
	window?: SignalWindow;
	direction?: SignalDirection;
	chain?: string;
	minLiquidityUsd?: string | number;
	maxAgeSeconds?: number;
	limit?: number;
	fixtureState?: SignalFixtureState;
}

function apiBase(): string {
	const url = (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
	return `${url}/api`;
}

async function request<T>(path: string, params: Record<string, string | number | undefined>) {
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value === undefined || value === "") continue;
		query.set(key, String(value));
	}
	const suffix = query.size > 0 ? `?${query.toString()}` : "";
	const response = await fetch(`${apiBase()}${path}${suffix}`, {
		method: "GET",
		headers: { accept: "application/json" },
		cache: "no-store",
	});
	const text = await response.text();
	let payload: unknown = null;
	if (text) {
		try {
			payload = JSON.parse(text);
		} catch {
			payload = null;
		}
	}
	if (!response.ok) {
		const body = (payload ?? {}) as Record<string, unknown>;
		const detail = body.detail as Record<string, unknown> | undefined;
		const code =
			typeof detail?.code === "string"
				? detail.code
				: typeof body.code === "string"
					? body.code
					: `HTTP_${response.status}`;
		const message =
			typeof detail?.message === "string"
				? detail.message
				: typeof body.message === "string"
					? body.message
					: `API returned ${response.status}`;
		throw new ApiError({
			code,
			message,
			status: response.status,
			retryable: response.status === 429 || response.status >= 500,
		});
	}
	return payload as T;
}

export async function getSignalMovers(query: SignalQuery = {}): Promise<SignalMovers> {
	if (webMode() === "fixture") {
		const fixtures = await import("./signals-fixtures");
		return fixtures.fixtureSignalMovers(query);
	}
	return request<SignalMovers>("/v1/signals/movers", {
		window: query.window ?? "h1",
		direction: query.direction ?? "up",
		chain: query.chain ?? "all",
		min_liquidity_usd: query.minLiquidityUsd,
		max_age_seconds: query.maxAgeSeconds,
		limit: query.limit,
	});
}
