// Typed client for the P0 paper account, positions and strategy endpoints.
//
// EZCHAIN_WEB_MODE=fixture swaps the HTTP calls for the fixture view models so Playwright
// and offline UI work never depend on a running API. Fixture mode is visibly labelled by
// the pages; it is never presented as live data. Unknown money values stay null and the
// pages print an em dash — they never become zero.

import { serverAuthorizationHeader } from "./api-auth";
import {
	fixtureStrategies,
	fixtureStrategyRuns,
	type Strategy,
	type StrategyKind,
	type StrategyParams,
	type StrategyRun,
	type StrategyStatus,
} from "./strategy-fixtures";

export type PaperApiMode = "fixture" | "live";

export interface Source {
	name: string;
	url?: string | null;
	observed_at: string;
}

export interface PaperPosition {
	chain: string;
	token_address: string;
	symbol: string;
	quantity: string;
	avg_cost_usd: string;
	mark_usd: string | null;
	market_value_usd: string | null;
	unrealized_usd: string | null;
	realized_usd: string;
	source: Source | null;
	mode: "paper";
}

export interface PaperAccount {
	cash_usd: string;
	equity_usd: string;
	realized_usd: string;
	unrealized_usd: string;
	positions: PaperPosition[];
	mode: "paper";
}

export type PaperResult<T> =
	| { ok: true; mode: PaperApiMode; data: T }
	| { ok: false; mode: PaperApiMode; error: { code: string; message: string } };

/** One order attempt as the API returns it. A rejected row carries its reason codes. */
export interface PaperOrderRecord {
	id: string;
	chain: string;
	token_address: string;
	side: string;
	amount_usd: string;
	status: string;
	reason: string | null;
	quote_kind: string | null;
	risk_profile?: string | null;
	created_at: string;
	filled_at: string | null;
}

export interface PaperFillRecord {
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

export interface ReconciliationCheck {
	id: string;
	ok: boolean;
	detail: string;
}

export interface ReconciliationTotals {
	genesis_usd: string;
	cash_usd: string;
	fills_cash_usd: string;
	open_positions: number;
	fills: number;
	filled_orders: number;
	orphan_fills: number;
	filled_orders_without_single_fill: number;
	negative_cash: number;
	negative_positions: number;
	quote_kinds: Record<string, number>;
}

export interface Reconciliation {
	ok: boolean;
	checks: ReconciliationCheck[];
	as_of: string;
	totals: ReconciliationTotals;
}

export interface KillSwitchState {
	active: boolean;
	reason: string | null;
	updated_at: string | null;
	scope: "strategy";
}

export interface StrategyInput {
	name: string;
	chain: string;
	token_address: string;
	token_symbol: string;
	kind: StrategyKind;
	params: StrategyParams;
	status?: StrategyStatus;
}

export interface StrategyPatch {
	name?: string;
	params?: StrategyParams;
	status?: StrategyStatus;
}

export class PaperApiError extends Error {
	readonly code: string;
	readonly status: number;

	constructor(code: string, message: string, status = 0) {
		super(message);
		this.name = "PaperApiError";
		this.code = code;
		this.status = status;
	}
}

export function webMode(): PaperApiMode {
	return process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
}

function apiBase() {
	// On the server Next needs an absolute URL. In the browser the server-side proxy at
	// /api/[...path] forwards to the API and injects the bearer token, so a relative path
	// keeps working on any origin without exposing the token. Read the env per call so a
	// server runtime that swaps configuration (tests, sidecar reload) is observed.
	if (typeof window !== "undefined") return "";
	return (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

interface ErrorEnvelope {
	code?: string;
	message?: string;
	detail?: { code?: string; message?: string } | string;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
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
			// A non-JSON error body still fails closed with the HTTP status.
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
		throw new PaperApiError(code, message, response.status);
	}

	if (response.status === 204) {
		return undefined as T;
	}
	return (await response.json()) as T;
}

export async function capture<T>(work: () => Promise<T>): Promise<PaperResult<T>> {
	const mode = webMode();
	try {
		return { ok: true, mode, data: await work() };
	} catch (error) {
		const apiError = error instanceof PaperApiError ? error : null;
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

function observed(secondsAgo: number) {
	return new Date(Date.now() - secondsAgo * 1000).toISOString();
}

const BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const AERO = "0x940181a94A35A4569E4529A3CDfB74e38FD98631";
const MOCHI = "7atgF8KQo4wJrD5ATGX7t1V2zVvykPJbFfNeVf1icFv1";
const SCAM = "5z3EqYQo9HiCEs3R84RCDMu2n7anpDMxR3T7dRntZ0bV";

// Fixture snapshot of the server view model. Numbers are already computed here, exactly
// as the API returns them: cash + known market value = equity, and the four unrealized
// values sum to the account unrealized. MOCHI has no mark, so its mark and unrealized are
// null and the UI prints —.
export const fixturePaperPositions: PaperPosition[] = [
	{
		chain: "solana",
		token_address: BONK,
		symbol: "BONK",
		quantity: "2000000",
		avg_cost_usd: "0.00002181",
		mark_usd: "0.00003142",
		market_value_usd: "62.84",
		unrealized_usd: "19.22",
		realized_usd: "18.90",
		source: { name: "jupiter", observed_at: observed(2) },
		mode: "paper",
	},
	{
		chain: "base",
		token_address: AERO,
		symbol: "AERO",
		quantity: "52.88",
		avg_cost_usd: "1.4000",
		mark_usd: "1.2840",
		market_value_usd: "67.90",
		unrealized_usd: "-6.12",
		realized_usd: "0.00",
		source: { name: "dexscreener", observed_at: observed(3) },
		mode: "paper",
	},
	{
		chain: "solana",
		token_address: MOCHI,
		symbol: "MOCHI",
		quantity: "5927.00",
		avg_cost_usd: "0.004218",
		mark_usd: null,
		market_value_usd: null,
		unrealized_usd: null,
		realized_usd: "0.00",
		source: null,
		mode: "paper",
	},
	{
		chain: "solana",
		token_address: SCAM,
		symbol: "SCAM",
		quantity: "13703703",
		avg_cost_usd: "0.00001080",
		mark_usd: "0.00000041",
		market_value_usd: "5.70",
		unrealized_usd: "-142.30",
		realized_usd: "0.00",
		source: { name: "dexscreener", observed_at: observed(125) },
		mode: "paper",
	},
];

export const fixturePaperAccount: PaperAccount = {
	cash_usd: "9709.36",
	equity_usd: "9870.80",
	realized_usd: "12.10",
	unrealized_usd: "-129.20",
	positions: fixturePaperPositions,
	mode: "paper",
};

function clonePositions(positions: PaperPosition[]) {
	return positions.map((position) => ({
		...position,
		source: position.source ? { ...position.source } : null,
	}));
}

export async function getPaperAccount(): Promise<PaperResult<PaperAccount>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return {
				...fixturePaperAccount,
				positions: clonePositions(fixturePaperAccount.positions),
			};
		}
		return apiRequest<PaperAccount>("/paper/account");
	});
}

export async function getPaperPositions(): Promise<PaperResult<PaperPosition[]>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return clonePositions(fixturePaperPositions);
		}
		return apiRequest<PaperPosition[]>("/paper/positions");
	});
}

export interface PaperSnapshot {
	mode: PaperApiMode;
	account: PaperResult<PaperAccount>;
	positions: PaperResult<PaperPosition[]>;
}

export async function getPaperSnapshot(): Promise<PaperSnapshot> {
	const [account, positions] = await Promise.all([getPaperAccount(), getPaperPositions()]);
	return { mode: webMode(), account, positions };
}

function boundedLimit(limit: number): number {
	return Math.min(200, Math.max(1, Math.floor(limit)));
}

export async function getPaperOrders(limit = 100): Promise<PaperResult<PaperOrderRecord[]>> {
	const bounded = boundedLimit(limit);
	return capture(async () => {
		if (webMode() === "fixture") {
			const fixtures = await import("./ops-fixtures");
			return fixtures.fixtureOrders(bounded);
		}
		return apiRequest<PaperOrderRecord[]>(`/paper/orders?limit=${bounded}`);
	});
}

export async function getPaperFills(limit = 100): Promise<PaperResult<PaperFillRecord[]>> {
	const bounded = boundedLimit(limit);
	return capture(async () => {
		if (webMode() === "fixture") {
			const fixtures = await import("./ops-fixtures");
			return fixtures.fixtureFills(bounded);
		}
		return apiRequest<PaperFillRecord[]>(`/paper/fills?limit=${bounded}`);
	});
}

/**
 * Read-only proof that the journal, fills and orders agree. Fixture mode does not invent
 * a reconciliation report: the endpoint runs database checks that a preview cannot prove.
 */
export async function getReconciliation(): Promise<PaperResult<Reconciliation>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			throw new PaperApiError(
				"FIXTURE_NOT_PROBED",
				"Reconciliation is computed from the paper database and is not probed in fixture mode.",
			);
		}
		return apiRequest<Reconciliation>("/paper/reconciliation");
	});
}

export async function getKillSwitch(): Promise<PaperResult<KillSwitchState>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return {
				active: false,
				reason: null,
				updated_at: null,
				scope: "strategy",
			};
		}
		return apiRequest<KillSwitchState>("/system/kill-switch");
	});
}

/**
 * Arm or release the persisted strategy switch. Like every mutation this always reaches
 * the API, including from a fixture build: a preview must never invent an operator action.
 */
export async function setKillSwitch(
	active: boolean,
	reason: string,
): Promise<KillSwitchState> {
	return apiRequest<KillSwitchState>("/system/kill-switch", {
		method: "POST",
		body: JSON.stringify({ active, reason }),
	});
}


export async function getStrategies(): Promise<PaperResult<Strategy[]>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return fixtureStrategies.map((strategy) => ({
				...strategy,
				params: { ...strategy.params },
			}));
		}
		const rows = await apiRequest<Strategy[]>("/strategies");
		return rows.map(normalizeStrategy);
	});
}

export async function getStrategy(id: string): Promise<PaperResult<Strategy>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			const found = fixtureStrategies.find((strategy) => strategy.id === id);
			if (!found) {
				throw new PaperApiError("STRATEGY_NOT_FOUND", "strategy not found", 404);
			}
			return { ...found, params: { ...found.params } };
		}
		return normalizeStrategy(await apiRequest<Strategy>(`/strategies/${id}`));
	});
}

export async function getStrategyRuns(id: string): Promise<PaperResult<StrategyRun[]>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return fixtureStrategyRuns
				.filter((run) => run.strategy_id === id)
				.sort((a, b) => b.created_at.localeCompare(a.created_at))
				.map((run) => ({ ...run, inputs: { ...run.inputs } }));
		}
		return apiRequest<StrategyRun[]>(`/strategies/${id}/runs`);
	});
}

export async function getAllStrategyRuns(): Promise<PaperResult<StrategyRun[]>> {
	return capture(async () => {
		if (webMode() === "fixture") {
			return [...fixtureStrategyRuns]
				.sort((a, b) => b.created_at.localeCompare(a.created_at))
				.map((run) => ({ ...run, inputs: { ...run.inputs } }));
		}
		const strategies = await apiRequest<Strategy[]>("/strategies");
		const runs = await Promise.all(
			strategies.map((strategy) => apiRequest<StrategyRun[]>(`/strategies/${strategy.id}/runs`)),
		);
		return runs.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
	});
}

// Mutations are browser calls in live mode. Fixture mode keeps a page-local session
// store in the strategy client; the API is never touched.
export async function createStrategy(input: StrategyInput): Promise<Strategy> {
	return normalizeStrategy(
		await apiRequest<Strategy>("/strategies", {
			method: "POST",
			body: JSON.stringify(input),
		}),
	);
}

export async function patchStrategy(id: string, patch: StrategyPatch): Promise<Strategy> {
	return normalizeStrategy(
		await apiRequest<Strategy>(`/strategies/${id}`, {
			method: "PATCH",
			body: JSON.stringify(patch),
		}),
	);
}

export async function deleteStrategy(id: string): Promise<void> {
	await apiRequest<void>(`/strategies/${id}`, { method: "DELETE" });
}

export async function runStrategy(id: string): Promise<StrategyRun> {
	return apiRequest<StrategyRun>(`/strategies/${id}/run`, { method: "POST" });
}

function normalizeStrategy(row: Strategy): Strategy {
	return {
		...row,
		kind: row.kind as StrategyKind,
		status: row.status as StrategyStatus,
		params: row.params ?? {},
	};
}
