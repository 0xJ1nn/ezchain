// Typed read-only client for the paper account read models.
//
// These endpoints are server projections: the UI formats them and never recomputes PnL,
// cost basis or equity. In fixture mode there is no fake substitute for a live read; the
// caller sees `FIXTURE_UNAVAILABLE` and renders an honest empty state.

import { serverAuthorizationHeader } from "./api-auth";
import { webMode } from "./paper-api";

export interface AccountSource {
	name: string;
	url?: string | null;
	observed_at: string;
}

export interface PaperLimits {
	mode: "paper";
	max_order_usd: string;
	max_position_usd: string;
	max_open_positions: number;
	max_daily_loss_usd: string;
	min_pair_age_minutes: number;
	min_liquidity_usd: string;
	paper_mark_max_order_usd: string;
	paper_fee_bps: number;
	max_quote_age_seconds: number;
	kill_switch: { active: boolean; reason?: string | null; updated_at?: string | null };
}

export interface PaperAlert {
	id: string;
	code: string;
	severity: "critical" | "warning" | "info";
	title: string;
	detail: string;
	created_at: string | null;
	resource: string | null;
}

export interface PaperAlerts {
	as_of: string;
	alerts: PaperAlert[];
}

export interface PerformanceToken {
	chain: string;
	token_address: string;
	symbol: string;
	realized_usd: string;
	unrealized_usd: string | null;
	fees_usd: string;
	trades: number;
	quantity: string;
	avg_cost_usd: string | null;
	mark_usd: string | null;
	market_value_usd: string | null;
}

export interface AttributionRow extends PerformanceToken {
	share_pct: string | null;
}

export interface PaperPerformance {
	mode: "paper";
	as_of: string;
	cash_usd: string;
	equity_usd: string | null;
	realized_usd: string;
	unrealized_usd: string | null;
	fees_usd: string;
	trades: number;
	wins: number;
	losses: number;
	win_rate_pct: string | null;
	open_positions: number;
	by_token: PerformanceToken[];
	daily_realized: { date: string; realized_usd: string; fees_usd: string }[];
}

export interface LedgerEntry {
	id: string;
	group_id: string;
	account: string;
	asset: string;
	amount: string;
	usd_value: string;
	kind: string;
	created_at: string;
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

export interface PaperFill {
	id: string;
	order_id: string;
	chain: string;
	token_address: string;
	side: string;
	quantity: string;
	price_usd: string;
	notional_usd: string;
	fee_usd: string;
	quote_kind: string;
	created_at: string;
}

export interface MarketOverview {
	as_of: string;
	chains: {
		id: string;
		name: string;
		status: "ok" | "stale" | "no_data" | "error" | "scanning";
		rows: number;
		age_seconds: number | null;
		last_error: string | null;
	}[];
	totals: {
		chains: number;
		covered: number;
		stale: number;
		no_data: number;
		error: number;
		snapshots: number;
	};
}

export type ReadResult<T> =
	| { ok: true; data: T }
	| { ok: false; code: string; message: string };

const API_URL = process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000";

function apiBase(): string {
	return typeof window === "undefined" ? API_URL : "";
}

async function read<T>(path: string): Promise<ReadResult<T>> {
	if (webMode() === "fixture") {
		return {
			ok: false,
			code: "FIXTURE_UNAVAILABLE",
			message: "This read is not part of the labelled fixture set.",
		};
	}
	try {
		const response = await fetch(`${apiBase()}/api/v1${path}`, {
			cache: "no-store",
			headers: { accept: "application/json", ...serverAuthorizationHeader() },
		});
		if (!response.ok) {
			const payload = (await response.json().catch(() => null)) as
				| { detail?: { code?: string; message?: string } | string }
				| null;
			const detail = payload?.detail;
			return {
				ok: false,
				code:
					(typeof detail === "object" && detail?.code) ||
					`HTTP_${response.status}`,
				message:
					(typeof detail === "object" && detail?.message) ||
					(typeof detail === "string" ? detail : `API returned ${response.status}`),
			};
		}
		return { ok: true, data: (await response.json()) as T };
	} catch (error) {
		return {
			ok: false,
			code: "UPSTREAM_UNREACHABLE",
			message: error instanceof Error ? error.message : String(error),
		};
	}
}

export function getPaperLimits(): Promise<ReadResult<PaperLimits>> {
	return read("/paper/limits");
}

export function getPaperAlerts(): Promise<ReadResult<PaperAlerts>> {
	return read("/paper/alerts");
}

export function getPaperPerformance(): Promise<ReadResult<PaperPerformance>> {
	return read("/paper/performance");
}

export function getPaperAttribution(): Promise<ReadResult<AttributionRow[]>> {
	return read("/paper/attribution");
}

export function getPaperLedger(limit = 200): Promise<ReadResult<LedgerEntry[]>> {
	return read(`/paper/ledger?limit=${limit}`);
}

export function getPaperOrders(limit = 10): Promise<ReadResult<PaperOrder[]>> {
	return read(`/paper/orders?limit=${limit}`);
}

export function getPaperFills(limit = 10): Promise<ReadResult<PaperFill[]>> {
	return read(`/paper/fills?limit=${limit}`);
}

export function getMarketOverview(): Promise<ReadResult<MarketOverview>> {
	return read("/markets/overview");
}
