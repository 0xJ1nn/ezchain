// Typed client for the ezCHAIN API (v1).
//
// Two data modes, chosen by the explicit `EZCHAIN_WEB_MODE` switch:
//   fixture — deterministic local data for tests and offline UI work. The pages say
//             FIXTURE on screen; nothing pretends to be a live feed.
//   live    — fetch the API and throw `ApiError`. There is no silent fallback: a failed
//             live request renders a reason code, never fixture rows.
// An unset switch means `fixture` (the safe default). Any other value is an error.
//
// Reads are mode-switched. Writes (`postPaperOrder`) always reach the API so a fixture
// build can never fabricate a fill.
//
// The UI formats only. Numbers arrive as decimal strings from the API; this module never
// does PnL or quote arithmetic (CONTRACT I3/I10/I12).

export type WebMode = "fixture" | "live";
export type MarketSort = "trending" | "liquidity" | "new";
export type Side = "buy" | "sell";

export interface Source {
	name: string;
	url: string | null;
	observed_at: string;
}

export interface MarketRow {
	chain: string;
	pair_address: string;
	dex: string;
	base_token: string;
	quote_token: string;
	symbol: string;
	name: string;
	price_usd: string;
	liquidity_usd: string;
	volume_24h_usd: string;
	price_change_m5: string | null;
	price_change_h1: string | null;
	price_change_h6: string | null;
	price_change_h24: string | null;
	pair_created_at: string | null;
	url: string | null;
	source: Source;
}

export interface MarketList {
	rows: MarketRow[];
	source: Source;
	stale: boolean;
}

export interface ChainCapabilities {
	browse: boolean;
	paper_execute: boolean;
	live_execute: boolean;
	quote_kind: string;
}

export interface Chain {
	id: string;
	name: string;
	family: string;
	native_asset: string;
	tier: string;
	capabilities: ChainCapabilities;
	venues: string[];
	notes: string;
}

export interface PairInfo {
	pair_address: string;
	dex: string;
	quote_token: string;
	price_usd: string;
	liquidity_usd: string;
	volume_24h_usd: string;
	url: string | null;
}

export interface TokenSnapshot {
	chain: string;
	address: string;
	symbol: string;
	name: string;
	price_usd: string | null;
	liquidity_usd: string | null;
	volume_24h_usd: string | null;
	price_change_m5: string | null;
	price_change_h1: string | null;
	price_change_h6: string | null;
	price_change_h24: string | null;
	pairs: PairInfo[];
	safety: Record<string, unknown>;
	source: Source | null;
	stale: boolean;
}

export interface Quote {
	chain: string;
	token_in: string;
	token_out: string;
	side: string;
	amount_in: string;
	amount_out: string;
	price: string;
	price_impact_pct: string | null;
	fee_usd: string;
	kind: string;
	route: string | null;
	source: Source;
}

export interface PaperOrderInput {
	chain: string;
	token_address: string;
	side: Side;
	/** Decimal string in USD, quantised by the API. */
	amount_usd: string;
	idempotency_key: string;
}

export interface PaperOrder {
	id: string;
	chain: string;
	token_address: string;
	side: string;
	amount_usd: string;
	status: string;
	reason: string | null;
	quote_kind: string | null;
	created_at: string;
	filled_at: string | null;
}

/** Plain, serialisable failure that can cross the server/client boundary. */
export interface Reason {
	code: string;
	message: string;
	retryable: boolean;
}

export class ApiError extends Error {
	readonly code: string;
	readonly status: number;
	readonly retryable: boolean;
	readonly detail: string | null;

	constructor(init: {
		code: string;
		message: string;
		status?: number;
		retryable?: boolean;
		detail?: string | null;
	}) {
		super(init.message);
		this.name = "ApiError";
		this.code = init.code;
		this.status = init.status ?? 0;
		this.retryable = init.retryable ?? false;
		this.detail = init.detail ?? null;
	}

	toReason(): Reason {
		return { code: this.code, message: this.message, retryable: this.retryable };
	}
}

export function asApiError(error: unknown): ApiError {
	if (error instanceof ApiError) return error;
	const message = error instanceof Error ? error.message : String(error);
	return new ApiError({ code: "WEB_ERROR", message, status: 0, retryable: false });
}

export function webMode(): WebMode {
	const value = process.env.EZCHAIN_WEB_MODE;
	if (value === "live") return "live";
	if (value === undefined || value === "" || value === "fixture") return "fixture";
	throw new ApiError({
		code: "WEB_MODE_INVALID",
		message: `EZCHAIN_WEB_MODE must be "fixture" or "live", got "${value}"`,
	});
}

export function isLiveMode(): boolean {
	return webMode() === "live";
}

function apiBase(): string {
	if (typeof window !== "undefined") return "/api";
	const url = (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
	return `${url}/api`;
}

type QueryValue = string | number | boolean | null | undefined;

interface RequestInitLite {
	method?: "GET" | "POST";
	params?: Record<string, QueryValue>;
	body?: unknown;
}

async function request<T>(path: string, init: RequestInitLite = {}): Promise<T> {
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(init.params ?? {})) {
		if (value === null || value === undefined || value === "") continue;
		query.set(key, String(value));
	}
	const suffix = query.size > 0 ? `?${query.toString()}` : "";
	const target = `${apiBase()}${path}${suffix}`;
	const headers: Record<string, string> = { accept: "application/json" };
	let body: string | undefined;
	if (init.body !== undefined) {
		headers["content-type"] = "application/json";
		body = JSON.stringify(init.body);
	}

	let response: Response;
	try {
		response = await fetch(target, {
			method: init.method ?? "GET",
			headers,
			body,
			cache: "no-store",
		});
	} catch (error) {
		throw new ApiError({
			code: "UPSTREAM_UNREACHABLE",
			message: `API request failed: ${error instanceof Error ? error.message : String(error)}`,
			status: 0,
			retryable: true,
		});
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

	if (!response.ok) {
		throw errorFromPayload(payload, response.status);
	}
	return payload as T;
}

function errorFromPayload(payload: unknown, status: number): ApiError {
	const body = (payload ?? {}) as Record<string, unknown>;
	const detail = body.detail;
	if (detail && typeof detail === "object" && !Array.isArray(detail)) {
		const env = detail as Record<string, unknown>;
		return new ApiError({
			code: typeof env.code === "string" ? env.code : `HTTP_${status}`,
			message:
				typeof env.message === "string" ? env.message : `API returned ${status}`,
			status,
			retryable: env.retryable === true,
			detail: typeof env.detail === "string" ? env.detail : null,
		});
	}
	if (typeof body.code === "string") {
		return new ApiError({
			code: body.code,
			message: typeof body.message === "string" ? body.message : `API returned ${status}`,
			status,
			retryable: body.retryable === true,
		});
	}
	const message =
		typeof detail === "string"
			? detail
			: typeof body.message === "string"
				? body.message
				: `API returned ${status}`;
	return new ApiError({
		code: status === 429 ? "RATE_LIMITED" : `HTTP_${status}`,
		message,
		status,
		retryable: status === 429 || status >= 500,
	});
}

export interface MarketQuery {
	chain?: string;
	q?: string;
	sort?: MarketSort;
	limit?: number;
}

export async function getMarkets(query: MarketQuery = {}): Promise<MarketList> {
	if (webMode() === "fixture") {
		const fixtures = await import("./live-fixtures");
		return fixtures.fixtureMarketList(query);
	}
	return request<MarketList>("/v1/markets", {
		params: {
			chain: query.chain ?? "all",
			q: query.q,
			sort: query.sort ?? "trending",
			limit: query.limit ?? 50,
		},
	});
}

export async function getChains(): Promise<Chain[]> {
	if (webMode() === "fixture") {
		const fixtures = await import("./live-fixtures");
		return fixtures.fixtureChains();
	}
	return request<Chain[]>("/v1/chains");
}

export async function getToken(chain: string, address: string): Promise<TokenSnapshot> {
	if (webMode() === "fixture") {
		const fixtures = await import("./live-fixtures");
		return fixtures.fixtureToken(chain, address);
	}
	return request<TokenSnapshot>(
		`/v1/tokens/${encodeURIComponent(chain)}/${encodeURIComponent(address)}`,
	);
}

export interface QuoteQuery {
	chain: string;
	token: string;
	side: Side;
	amountUsd: string;
}

export async function getQuote(query: QuoteQuery): Promise<Quote> {
	if (webMode() === "fixture") {
		const fixtures = await import("./live-fixtures");
		return fixtures.fixtureQuote(query);
	}
	return request<Quote>("/v1/quotes", {
		params: {
			chain: query.chain,
			token: query.token,
			side: query.side,
			amount_usd: query.amountUsd,
		},
	});
}

/**
 * Submit a paper order. This is intentionally not mode-switched: a write must reach the
 * API, otherwise a fixture build would invent a fill. Errors surface with the API reason.
 */
export async function postPaperOrder(input: PaperOrderInput): Promise<PaperOrder> {
	return request<PaperOrder>("/v1/paper/orders", { method: "POST", body: input });
}
