// Display helpers shared by the positions list and the per-position route.
//
// The server sends the position view model. This module orders and formats it; it never
// recomputes PnL, cost basis or executable value. An unknown mark stays null and prints —.

import { MINUS, signedUsd, usd } from "./format";
import type { PaperPosition } from "./paper-api";

export const STALE_AFTER_SECONDS = 60;

export type PositionView = "list" | "grid" | "detailed";

export const POSITION_VIEWS: { value: PositionView; label: string }[] = [
	{ value: "list", label: "List" },
	{ value: "grid", label: "Grid" },
	{ value: "detailed", label: "Detailed" },
];

export function parsePositionView(value: string | string[] | undefined): PositionView {
	const raw = Array.isArray(value) ? value[0] : value;
	return raw === "grid" || raw === "detailed" ? raw : "list";
}

export type PositionSort = "server" | "value" | "unrealized" | "symbol" | "chain";

export const POSITION_SORTS: { value: PositionSort; label: string }[] = [
	{ value: "server", label: "Server order" },
	{ value: "value", label: "Value" },
	{ value: "unrealized", label: "Unrealized" },
	{ value: "symbol", label: "Symbol" },
	{ value: "chain", label: "Chain" },
];

export function parsePositionSort(value: string | string[] | undefined): PositionSort {
	const raw = Array.isArray(value) ? value[0] : value;
	return POSITION_SORTS.some((entry) => entry.value === raw)
		? (raw as PositionSort)
		: "server";
}

export interface PositionQuery {
	view?: PositionView;
	chain?: string;
	sort?: PositionSort;
	state?: string;
}

function one(value: string | string[] | undefined): string | undefined {
	const raw = Array.isArray(value) ? value[0] : value;
	return raw && raw.trim() ? raw.trim() : undefined;
}

function applyQuery(params: URLSearchParams, query: PositionQuery) {
	if (query.view) params.set("view", query.view);
	if (query.chain) params.set("chain", query.chain);
	if (query.sort && query.sort !== "server") params.set("sort", query.sort);
	if (query.state) params.set("state", query.state);
}

export function positionsHref(query: PositionQuery = {}): string {
	const params = new URLSearchParams();
	applyQuery(params, query);
	const suffix = params.toString();
	return suffix ? `/positions?${suffix}` : "/positions";
}

export interface PositionRouteQuery extends PositionQuery {
	timeframe?: string;
	/** Fixture-only candle demo state, carried so a timeframe switch preserves it. */
	candles?: string;
}

export function positionRouteHref(
	chain: string,
	token: string,
	query: PositionRouteQuery = {},
): string {
	const params = new URLSearchParams();
	applyQuery(params, query);
	if (query.timeframe && query.timeframe !== "1h") params.set("timeframe", query.timeframe);
	if (query.candles) params.set("candles", query.candles);
	const suffix = params.toString();
	const path = `/positions/${encodeURIComponent(chain)}/${encodeURIComponent(token)}`;
	return suffix ? `${path}?${suffix}` : path;
}

export function positionHref(
	position: PaperPosition,
	query: PositionRouteQuery = {},
): string {
	return positionRouteHref(position.chain, position.token_address, query);
}

export function positionKey(position: PaperPosition): string {
	return `${position.chain}:${position.token_address}`;
}

export function chainOptions(positions: PaperPosition[]): string[] {
	return [...new Set(positions.map((position) => position.chain))].sort();
}

export function parseChainFilter(
	value: string | string[] | undefined,
	positions: PaperPosition[],
): string | undefined {
	const chain = one(value);
	return chain && positions.some((position) => position.chain === chain) ? chain : undefined;
}

function numeric(value: string | null): number | null {
	if (value === null || value === "") return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

/** Display ordering only. Values stay exactly as the server computed them. */
export function visiblePositions(
	positions: PaperPosition[],
	options: { chain?: string; sort?: PositionSort } = {},
): PaperPosition[] {
	const filtered = options.chain
		? positions.filter((position) => position.chain === options.chain)
		: [...positions];
	const sort = options.sort ?? "server";
	if (sort === "server") return filtered;

	const withIndex = filtered.map((position, index) => ({ position, index }));
	withIndex.sort((a, b) => {
		let comparison = 0;
		if (sort === "symbol") {
			comparison = a.position.symbol.localeCompare(b.position.symbol);
		} else if (sort === "chain") {
			comparison = a.position.chain.localeCompare(b.position.chain);
		} else {
			const field = sort === "value" ? "market_value_usd" : "unrealized_usd";
			const left = numeric(a.position[field]);
			const right = numeric(b.position[field]);
			if (left === null && right === null) comparison = 0;
			else if (left === null) comparison = 1;
			else if (right === null) comparison = -1;
			else comparison = right - left;
		}
		return comparison !== 0 ? comparison : a.index - b.index;
	});
	return withIndex.map((entry) => entry.position);
}

export function money(value: string | null | undefined, signed = false): string {
	if (value === null || value === undefined || value === "") return "—";
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	if (amount !== 0 && Math.abs(amount) < 0.01) {
		const decimal = Math.abs(amount).toFixed(12).replace(/0+$/, "").replace(/\.$/, "");
		return `${amount < 0 ? MINUS : ""}$${decimal}`;
	}
	if (amount === 0) return usd(0);
	return signed ? signedUsd(amount) : usd(amount);
}

export function quantity(value: string): string {
	const raw = value?.trim();
	if (!raw) return "—";
	const decimal = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(raw);
	if (decimal && /\d/.test(raw)) {
		// Format the server's decimal string directly. Going through Number would round a
		// valid sub-micro quantity to zero, which reads as "no position".
		const [, sign, wholeRaw, fractionRaw = ""] = decimal;
		const whole = (wholeRaw || "0").replace(/^0+(?=\d)/, "");
		const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
		const fraction = fractionRaw.replace(/0+$/, "");
		return `${sign}${grouped}${fraction ? `.${fraction}` : ""}`;
	}
	const amount = Number(raw);
	if (!Number.isFinite(amount)) return "—";
	return amount.toLocaleString("en-US", { maximumFractionDigits: 18 });
}

export interface MarkAge {
	label: string;
	stale: boolean;
	unknown: boolean;
}

export function markAge(position: PaperPosition, now = Date.now()): MarkAge {
	if (!position.source?.observed_at) {
		return { label: "unknown mark", stale: false, unknown: true };
	}
	const observed = Date.parse(position.source.observed_at);
	if (Number.isNaN(observed)) {
		return { label: "unknown age", stale: false, unknown: true };
	}
	const seconds = Math.max(0, Math.round((now - observed) / 1000));
	if (seconds <= STALE_AFTER_SECONDS) {
		return { label: `${seconds}s`, stale: false, unknown: false };
	}
	const minutes = Math.round(seconds / 60);
	return { label: `${minutes}m`, stale: true, unknown: false };
}

export function markTone(position: PaperPosition): "warn" | "ghost" {
	return markAge(position).stale || position.mark_usd === null ? "warn" : "ghost";
}
