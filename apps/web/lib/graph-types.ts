// Wire types for the visual strategy graph (docs/STRATEGY-GRAPH.md §1, §2, §6, §9).
//
// These are the shapes the API returns and the editor renders. Nothing here computes a
// price, a PnL or a position: money values arrive as decimal strings and unknown is null
// (CONTRACT I3 — unknown is never zero).

export type GraphPinType = "exec" | "bool" | "number" | "series";

/** Document-level pin type table, from the catalogue (§2). */
export interface CataloguePinType {
	id: GraphPinType;
	title: string;
}

export interface CatalogueCategory {
	id: string;
	title: string;
}

export interface CatalogueInput {
	name: string;
	type: GraphPinType;
	/** Unconnected is a validation error unless the node declares `default_param`. */
	required: boolean;
	default_param: string | null;
}

export interface CatalogueOutput {
	name: string;
	type: GraphPinType;
}

export type ParamKind = "number" | "select" | "text";

export interface CatalogueParam {
	name: string;
	kind: ParamKind;
	title: string;
	unit: string | null;
	min: number | string | null;
	max: number | string | null;
	choices: string[] | null;
	default: string | number | null;
	required: boolean;
}

export interface CatalogueNode {
	type: string;
	title: string;
	category: string;
	description: string;
	inputs: CatalogueInput[];
	outputs: CatalogueOutput[];
	params: CatalogueParam[];
}

/** `GET /api/v1/graph/catalogue` (§9). */
export interface GraphCatalogue {
	schema: string;
	pin_types: CataloguePinType[];
	categories: CatalogueCategory[];
	nodes: CatalogueNode[];
}

/** One row of `ValidationReport.errors` / `.warnings` (§6). */
export interface ValidationIssue {
	code: string;
	message: string;
	node: string | null;
	port: string | null;
}

/** `POST /api/v1/graph/validate` (§9) — always 200 for a well-formed document. */
export interface ValidationReport {
	ok: boolean;
	errors: ValidationIssue[];
	warnings: ValidationIssue[];
	warmup_bars: Record<string, number>;
}

export type GraphTraceValues = Record<string, string | number | boolean | null>;

/** The evaluator trace: last value per node plus the ordered exec path (§4). */
export interface GraphTrace {
	values: GraphTraceValues;
	exec: string[];
}

export interface GraphEffect {
	kind: string;
	node: string;
	action?: string | null;
	amount_usd?: string | null;
	sell_fraction?: string | null;
	message?: string | null;
	name?: string | null;
	level?: string | null;
	suppressed?: boolean;
}

export interface GraphDecision {
	action: string;
	amount_usd: string | null;
	sell_fraction: string | null;
	reason: string;
	effects: GraphEffect[];
}

export interface DryRunCandles {
	bars: number;
	last_close_time: string | null;
	stale: boolean;
}

export interface DryRunContext {
	now: string;
	price_usd: string | null;
	candles: Record<string, DryRunCandles>;
	position: Record<string, unknown> | null;
	account: Record<string, unknown> | null;
}

/** `POST /api/v1/graph/dry-run` (§9). Never places an order. */
export interface DryRunResult {
	decision: GraphDecision;
	trace: GraphTrace;
	context: DryRunContext;
	mode: "DRY_RUN";
}

export type Numeric = string | number | null;

export interface BacktestMetrics {
	net_pnl_usd: Numeric;
	return_pct: Numeric;
	max_drawdown_pct: Numeric;
	win_rate_pct: Numeric;
	profit_factor: Numeric;
	trades: Numeric;
	exposure_pct: Numeric;
	sharpe: Numeric;
}

export interface BacktestEquityPoint {
	t: string;
	equity_usd: string;
	drawdown_pct: Numeric;
}

export interface BacktestTrade {
	entry_time: string;
	exit_time: string | null;
	side: string;
	qty: string;
	entry_price: string;
	exit_price: string;
	pnl_usd: string;
	pnl_pct: Numeric;
	fees_usd: string;
	reason: string | null;
}

export interface Backtest {
	id: string;
	created_at: string;
	label: "BACKTEST";
	request: Record<string, unknown>;
	data_source: string | null;
	fill_model: string | null;
	metrics: BacktestMetrics;
	equity: BacktestEquityPoint[];
	trades: BacktestTrade[];
	node_hits: Record<string, number>;
}

export interface BacktestSummary {
	id: string;
	created_at: string;
	request: Record<string, unknown>;
	metrics: BacktestMetrics;
}

export interface DryRunRequest {
	graph: unknown;
	chain: string;
	token_address: string;
	max_position_usd?: string;
}

export interface BacktestRequest {
	graph: unknown;
	strategy_id?: string;
	chain: string;
	token_address: string;
	timeframe: string;
	bars: number;
	fee_bps: number;
	slippage_bps: number;
	starting_cash_usd: string;
}

/** Result envelope, matching the `paper-api` style so pages handle one failure shape. */
export type GraphResult<T> =
	| { ok: true; mode: "fixture" | "live"; data: T }
	| { ok: false; mode: "fixture" | "live"; error: { code: string; message: string; report?: ValidationReport } };
