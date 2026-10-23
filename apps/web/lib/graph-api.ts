// Client for the strategy-graph endpoints (docs/STRATEGY-GRAPH.md §9).
//
// Reads follow the same two-mode switch as `paper-api`: `fixture` returns deterministic
// local data labelled FIXTURE, `live` calls the API through the Next proxy at
// `/api/[...path]` (which injects the bearer token). There is no silent fallback — a
// failed live read returns an error result and the page says so.
//
// Writes (`createGraphStrategy`, `patchGraphStrategy`) are never mode-switched: a fixture
// build must not invent a saved strategy, so the POST/PATCH always reaches the API.

import { serverAuthorizationHeader } from "./api-auth";
import { parseGraphDocument } from "./graph-doc";
import { fixtureStrategies } from "./strategy-fixtures";
import {
	fixtureBacktest,
	fixtureBacktests,
	fixtureCatalogue,
	fixtureDryRun,
	fixtureGraphStrategy,
	fixtureGraphStrategyRuns,
	validateGraphDocument,
	type GraphStrategyRow,
	type GraphStrategyRun,
} from "./graph-fixtures";
import type {
	Backtest,
	BacktestRequest,
	BacktestSummary,
	CatalogueCategory,
	CatalogueNode,
	CatalogueParam,
	CataloguePinType,
	DryRunRequest,
	DryRunResult,
	GraphCatalogue,
	GraphResult,
	ParamKind,
	ValidationReport,
} from "./graph-types";

export type GraphMode = "fixture" | "live";

const API_URL = process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000";

function apiBase(): string {
	// On the server Next needs an absolute URL; in the browser the proxy keeps the token
	// out of the bundle, so a relative path is correct.
	return typeof window === "undefined" ? API_URL : "";
}

export class GraphApiError extends Error {
	readonly code: string;
	readonly status: number;
	readonly report?: ValidationReport;

	constructor(code: string, message: string, status = 0, report?: ValidationReport) {
		super(message);
		this.name = "GraphApiError";
		this.code = code;
		this.status = status;
		this.report = report;
	}
}

interface ErrorEnvelope {
	code?: string;
	message?: string;
	report?: ValidationReport;
	detail?:
		| { code?: string; message?: string; report?: ValidationReport }
		| string;
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
			// A non-JSON error body still fails closed with the HTTP status.
		}
		const detail = body.detail;
		const code =
			(typeof detail === "object" && detail?.code) || body.code || `HTTP_${response.status}`;
		const message =
			(typeof detail === "object" && detail?.message) ||
			(typeof detail === "string" ? detail : undefined) ||
			body.message ||
			`request failed with status ${response.status}`;
		const report =
			(typeof detail === "object" && detail?.report) || body.report || undefined;
		throw new GraphApiError(code, message, response.status, report);
	}

	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

async function capture<T>(
	mode: GraphMode,
	work: () => Promise<T>,
): Promise<GraphResult<T>> {
	try {
		return { ok: true, mode, data: await work() };
	} catch (error) {
		const apiError = error instanceof GraphApiError ? error : null;
		return {
			ok: false,
			mode,
			error: {
				code: apiError?.code ?? "API_UNREACHABLE",
				message:
					apiError?.message ??
					(error instanceof Error ? error.message : "the API could not be reached"),
				report: apiError?.report,
			},
		};
	}
}

function requireDocument(graph: unknown) {
	const parsed = parseGraphDocument(graph);
	if (!parsed.ok) {
		throw new GraphApiError("STRATEGY_GRAPH_INVALID", parsed.message, 422);
	}
	return parsed.document;
}


/**
 * The API serves the core catalogue (`node_types`, string `pin_types`, param kinds
 * `decimal|int|enum|str`). The editor uses the presentation shape (`nodes`, pin objects,
 * `number|select|text`). This is the only translation; node semantics stay in core.
 */
export function adaptCatalogue(raw: unknown): GraphCatalogue {
	const value = raw as Record<string, unknown>;
	if (Array.isArray(value.nodes)) return raw as GraphCatalogue;

	const pinTypes: CataloguePinType[] = Array.isArray(value.pin_types)
		? (value.pin_types as unknown[]).map((entry) =>
				typeof entry === "string"
					? { id: entry as CataloguePinType["id"], title: entry.charAt(0).toUpperCase() + entry.slice(1) }
					: (entry as CataloguePinType),
			)
		: [];
	const categories = (value.categories ?? []) as CatalogueCategory[];
	const params = (rawNode: Record<string, unknown>): CatalogueParam[] =>
		((rawNode.params ?? []) as Record<string, unknown>[]).map((param) => {
			const kind = String(param.kind ?? "text");
			const mapped: ParamKind = kind === "enum" ? "select" : kind === "str" ? "text" : "number";
			const choices = Array.isArray(param.choices) && param.choices.length > 0
				? (param.choices as string[])
				: null;
			return {
				name: String(param.name ?? ""),
				kind: mapped,
				title: String(param.title ?? param.name ?? ""),
				unit: param.unit === undefined ? null : (param.unit as string | null),
				min: (param.min ?? null) as number | string | null,
				max: (param.max ?? null) as number | string | null,
				choices,
				default: (param.default ?? null) as string | number | null,
				required: param.required === true,
			};
		});
	const nodes: CatalogueNode[] = ((value.node_types ?? []) as Record<string, unknown>[]).map(
		(node) => ({
			type: String(node.type ?? ""),
			title: String(node.title ?? node.type ?? ""),
			category: String(node.category ?? ""),
			description: String(node.description ?? ""),
			inputs: (node.inputs ?? []) as CatalogueNode["inputs"],
			outputs: (node.outputs ?? []) as CatalogueNode["outputs"],
			params: params(node),
		}),
	);
	return { schema: String(value.schema ?? "ezchain.graph/1"), pin_types: pinTypes, categories, nodes };
}

export async function getGraphCatalogue(
	mode: GraphMode,
): Promise<GraphResult<GraphCatalogue>> {
	return capture(mode, async () => {
		if (mode === "fixture") return adaptCatalogue(fixtureCatalogue);
		return adaptCatalogue(await request<unknown>("/graph/catalogue"));
	});
}

export async function validateGraph(
	mode: GraphMode,
	graph: unknown,
): Promise<GraphResult<ValidationReport>> {
	return capture(mode, async () => {
		if (mode === "fixture") return validateGraphDocument(requireDocument(graph));
		return request<ValidationReport>("/graph/validate", {
			method: "POST",
			body: JSON.stringify({ graph }),
		});
	});
}

export async function dryRunGraph(
	mode: GraphMode,
	body: DryRunRequest,
): Promise<GraphResult<DryRunResult>> {
	return capture(mode, async () => {
		if (mode === "fixture") return fixtureDryRun(requireDocument(body.graph));
		return request<DryRunResult>("/graph/dry-run", {
			method: "POST",
			body: JSON.stringify(body),
		});
	});
}

export async function runBacktest(
	mode: GraphMode,
	body: BacktestRequest,
): Promise<GraphResult<Backtest>> {
	return capture(mode, async () => {
		if (mode === "fixture") {
			const document = requireDocument(body.graph);
			return fixtureBacktest(document, body.strategy_id);
		}
		return request<Backtest>("/backtests", {
			method: "POST",
			body: JSON.stringify(body),
		});
	});
}

export async function getBacktest(
	mode: GraphMode,
	id: string,
): Promise<GraphResult<Backtest>> {
	return capture(mode, async () => {
		if (mode === "fixture") {
			const found = fixtureBacktests.find((entry) => entry.id === id);
			if (!found) throw new GraphApiError("BACKTEST_NOT_FOUND", "backtest not found", 404);
			return found;
		}
		return request<Backtest>(`/backtests/${id}`);
	});
}

export async function listBacktests(
	mode: GraphMode,
	strategyId?: string,
): Promise<GraphResult<BacktestSummary[]>> {
	return capture(mode, async () => {
		if (mode === "fixture") {
			return fixtureBacktests
				.filter((entry) => !strategyId || entry.request.strategy_id === strategyId)
				.map((entry) => ({
					id: entry.id,
					created_at: entry.created_at,
					request: entry.request,
					metrics: entry.metrics,
				}));
		}
		const query = strategyId ? `?strategy_id=${encodeURIComponent(strategyId)}` : "";
		return request<BacktestSummary[]>(`/backtests${query}`);
	});
}

/* ------------------------------------------------------------------- strategy rows */

export interface GraphStrategyParams {
	graph: unknown;
	interval_minutes: number;
	max_position_usd: string;
}

export interface GraphStrategyInput {
	name: string;
	chain: string;
	token_address: string;
	token_symbol: string;
	kind: "graph";
	params: GraphStrategyParams;
	status?: "active" | "paused";
}

/** Load any strategy row. The editor decides whether `kind` is "graph". */
export async function getGraphStrategy(
	mode: GraphMode,
	id: string,
): Promise<GraphResult<GraphStrategyRow>> {
	return capture(mode, async () => {
		if (mode === "fixture") {
			if (id === fixtureGraphStrategy.id) return fixtureGraphStrategy;
			const other = fixtureStrategies.find((strategy) => strategy.id === id);
			if (other) {
				return {
					id: other.id,
					name: other.name,
					chain: other.chain,
					token_address: other.token_address,
					token_symbol: other.token_symbol,
					kind: other.kind,
					status: other.status,
					params: { ...other.params },
					last_run_at: other.last_run_at,
					created_at: other.created_at,
				};
			}
			throw new GraphApiError("STRATEGY_NOT_FOUND", "strategy not found", 404);
		}
		const row = await request<GraphStrategyRow>(`/strategies/${id}`);
		return { ...row, params: row.params ?? {} };
	});
}

export async function listGraphRuns(
	mode: GraphMode,
	id: string,
): Promise<GraphResult<GraphStrategyRun[]>> {
	return capture(mode, async () => {
		if (mode === "fixture") {
			return fixtureGraphStrategyRuns
				.filter((run) => run.strategy_id === id)
				.sort((a, b) => b.created_at.localeCompare(a.created_at));
		}
		return request<GraphStrategyRun[]>(`/strategies/${id}/runs`);
	});
}

/** POST /api/v1/strategies. Always live: a fixture build never fabricates a save. */
export async function createGraphStrategy(
	input: GraphStrategyInput,
): Promise<GraphStrategyRow> {
	const row = await request<GraphStrategyRow>("/strategies", {
		method: "POST",
		body: JSON.stringify(input),
	});
	return { ...row, params: row.params ?? {} };
}

/** PATCH /api/v1/strategies/{id}. Always live, same reason as create. */
export async function patchGraphStrategy(
	id: string,
	patch: { name?: string; params?: GraphStrategyParams; status?: "active" | "paused" },
): Promise<GraphStrategyRow> {
	const row = await request<GraphStrategyRow>(`/strategies/${id}`, {
		method: "PATCH",
		body: JSON.stringify(patch),
	});
	return { ...row, params: row.params ?? {} };
}
