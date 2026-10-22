// FIXTURE data for the visual strategy builder, plus a local "compile" and a
// deterministic dry-run/backtest so the editor and its Playwright tests work with no API.
//
// Nothing here is trading logic: the catalogue mirrors docs/STRATEGY-GRAPH.md §5
// (types, ports, params), the validator implements the §6 rules the editor needs for
// feedback, and the dry-run/backtest results are labelled FIXTURE everywhere they are
// shown. The real evaluator is `ezchain_core.graph.evaluate` — the browser never decides
// a trade.

import type { GraphDocument } from "./graph-doc";
import { GRAPH_LIMITS, GRAPH_SCHEMA, NODE_ID_PATTERN, parseGraphDocument } from "./graph-doc";
import { inputPinType, outputPinType, pinsCompatible } from "./graph-catalogue";
import type {
	Backtest,
	BacktestEquityPoint,
	BacktestMetrics,
	BacktestTrade,
	CatalogueCategory,
	CatalogueInput,
	CatalogueNode,
	CatalogueOutput,
	CatalogueParam,
	DryRunResult,
	GraphCatalogue,
	GraphEffect,
	GraphPinType,
	GraphTraceValues,
	ValidationIssue,
	ValidationReport,
} from "./graph-types";

/* ------------------------------------------------------------------ catalogue (§5) */

interface ParamOptions {
	unit?: string;
	min?: number;
	max?: number;
	choices?: string[];
	required?: boolean;
	default?: string | number | null;
}

function numberParam(name: string, title: string, options: ParamOptions = {}): CatalogueParam {
	return {
		name,
		kind: "number",
		title,
		unit: options.unit ?? null,
		min: options.min ?? null,
		max: options.max ?? null,
		choices: null,
		default: options.default ?? null,
		required: options.required ?? false,
	};
}

function selectParam(
	name: string,
	title: string,
	choices: string[],
	fallback: string,
	options: ParamOptions = {},
): CatalogueParam {
	return {
		name,
		kind: "select",
		title,
		unit: options.unit ?? null,
		min: null,
		max: null,
		choices,
		default: fallback,
		required: options.required ?? true,
	};
}

function textParam(name: string, title: string, options: ParamOptions = {}): CatalogueParam {
	return {
		name,
		kind: "text",
		title,
		unit: options.unit ?? null,
		min: null,
		max: null,
		choices: null,
		default: options.default ?? null,
		required: options.required ?? false,
	};
}

function pin(name: string, type: GraphPinType): CatalogueOutput {
	return { name, type };
}

function input(
	name: string,
	type: GraphPinType,
	options: { required?: boolean; defaultParam?: string | null } = {},
): CatalogueInput {
	return {
		name,
		type,
		required: options.required ?? true,
		default_param: options.defaultParam ?? null,
	};
}

function node(
	type: string,
	title: string,
	category: string,
	description: string,
	parts: {
		inputs?: CatalogueInput[];
		outputs?: CatalogueOutput[];
		params?: CatalogueParam[];
	},
): CatalogueNode {
	return {
		type,
		title,
		category,
		description,
		inputs: parts.inputs ?? [],
		outputs: parts.outputs ?? [],
		params: parts.params ?? [],
	};
}

const CATEGORIES: CatalogueCategory[] = [
	{ id: "events", title: "Events" },
	{ id: "market", title: "Market data" },
	{ id: "indicators", title: "Indicators" },
	{ id: "state", title: "Position & account" },
	{ id: "math", title: "Math & logic" },
	{ id: "flow", title: "Flow" },
	{ id: "actions", title: "Actions" },
	{ id: "guards", title: "Guards" },
];

const SERIES_INDICATOR_OUTPUTS: CatalogueOutput[] = [pin("series", "series"), pin("value", "number")];

export const fixtureCatalogue: GraphCatalogue = {
	schema: GRAPH_SCHEMA,
	pin_types: [
		{ id: "exec", title: "Execution" },
		{ id: "bool", title: "Boolean" },
		{ id: "number", title: "Number" },
		{ id: "series", title: "Series" },
	],
	categories: CATEGORIES,
	nodes: [
		node("event.tick", "On tick", "events", "Runs the exec chain once per scheduler tick for this token.", {
			outputs: [pin("out", "exec")],
		}),

		node("data.price", "Price", "market", "Snapshot price in USD. Unknown when no fresh snapshot exists.", {
			outputs: [pin("value", "number")],
		}),
		node("data.change", "Price change", "market", "Percent change over the selected window.", {
			outputs: [pin("value", "number")],
			params: [selectParam("window", "Window", ["5m", "1h", "6h", "24h"], "1h")],
		}),
		node("data.liquidity", "Liquidity", "market", "Pool liquidity in USD.", {
			outputs: [pin("value", "number")],
		}),
		node("data.volume24h", "Volume 24h", "market", "Trailing 24h volume in USD.", {
			outputs: [pin("value", "number")],
		}),
		node("data.pair_age", "Pair age", "market", "Minutes since the pair was created.", {
			outputs: [pin("value", "number")],
		}),
		node("data.candles", "Candles", "market", "Closed bars for one timeframe, oldest first. Never includes the forming bar.", {
			outputs: [
				pin("open", "series"),
				pin("high", "series"),
				pin("low", "series"),
				pin("close", "series"),
				pin("volume", "series"),
			],
			params: [selectParam("timeframe", "Timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"], "5m")],
		}),

		node("ind.sma", "SMA", "indicators", "Simple moving average over a series.", {
			inputs: [input("series", "series")],
			outputs: SERIES_INDICATOR_OUTPUTS,
			params: [numberParam("period", "Period", { min: 1, max: 500, default: 20, required: true, unit: "bars" })],
		}),
		node("ind.ema", "EMA", "indicators", "Exponential moving average, seeded with the SMA of the first period.", {
			inputs: [input("series", "series")],
			outputs: SERIES_INDICATOR_OUTPUTS,
			params: [numberParam("period", "Period", { min: 1, max: 500, default: 21, required: true, unit: "bars" })],
		}),
		node("ind.rsi", "RSI", "indicators", "Wilder relative strength index (0–100).", {
			inputs: [input("series", "series")],
			outputs: SERIES_INDICATOR_OUTPUTS,
			params: [numberParam("period", "Period", { min: 2, max: 200, default: 14, required: true, unit: "bars" })],
		}),
		node("ind.bbands", "Bollinger bands", "indicators", "Middle SMA with upper and lower bands at N population standard deviations.", {
			inputs: [input("series", "series")],
			outputs: [
				pin("upper", "series"),
				pin("middle", "series"),
				pin("lower", "series"),
				pin("upper_value", "number"),
				pin("middle_value", "number"),
				pin("lower_value", "number"),
			],
			params: [
				numberParam("period", "Period", { min: 1, max: 500, default: 20, required: true, unit: "bars" }),
				numberParam("stddev", "Std deviations", { min: 0.1, max: 10, default: 2, required: true }),
			],
		}),
		node("ind.atr", "ATR", "indicators", "Wilder average true range over high/low/close.", {
			inputs: [input("high", "series"), input("low", "series"), input("close", "series")],
			outputs: SERIES_INDICATOR_OUTPUTS,
			params: [numberParam("period", "Period", { min: 1, max: 500, default: 14, required: true, unit: "bars" })],
		}),
		node("ind.macd", "MACD", "indicators", "MACD line, signal line and histogram.", {
			inputs: [input("series", "series")],
			outputs: [
				pin("macd", "series"),
				pin("signal", "series"),
				pin("hist", "series"),
				pin("macd_value", "number"),
				pin("signal_value", "number"),
				pin("hist_value", "number"),
			],
			params: [
				numberParam("fast", "Fast", { min: 1, max: 200, default: 12, required: true, unit: "bars" }),
				numberParam("slow", "Slow", { min: 2, max: 400, default: 26, required: true, unit: "bars" }),
				numberParam("signal", "Signal", { min: 1, max: 200, default: 9, required: true, unit: "bars" }),
			],
		}),
		node("ind.highest", "Highest", "indicators", "Highest value in the last N bars.", {
			inputs: [input("series", "series")],
			outputs: [pin("value", "number")],
			params: [numberParam("period", "Period", { min: 1, max: 500, default: 20, required: true, unit: "bars" })],
		}),
		node("ind.lowest", "Lowest", "indicators", "Lowest value in the last N bars.", {
			inputs: [input("series", "series")],
			outputs: [pin("value", "number")],
			params: [numberParam("period", "Period", { min: 1, max: 500, default: 20, required: true, unit: "bars" })],
		}),
		node("ind.roc", "Rate of change", "indicators", "Percent change over N bars.", {
			inputs: [input("series", "series")],
			outputs: SERIES_INDICATOR_OUTPUTS,
			params: [numberParam("period", "Period", { min: 1, max: 500, default: 12, required: true, unit: "bars" })],
		}),
		node("ind.vwap", "VWAP", "indicators", "Rolling volume-weighted average price.", {
			inputs: [input("high", "series"), input("low", "series"), input("close", "series"), input("volume", "series")],
			outputs: SERIES_INDICATOR_OUTPUTS,
			params: [numberParam("period", "Period", { min: 1, max: 500, default: 20, required: true, unit: "bars" })],
		}),

		node("pos.state", "Position", "state", "Position state from the ledger. Values are unknown when flat where meaningless.", {
			outputs: [
				pin("open", "bool"),
				pin("quantity", "number"),
				pin("avg_cost_usd", "number"),
				pin("value_usd", "number"),
				pin("unrealized_pct", "number"),
				pin("peak_since_entry_usd", "number"),
				pin("drawdown_from_peak_pct", "number"),
				pin("minutes_in_position", "number"),
				pin("minutes_since_last_fill", "number"),
			],
		}),
		node("acct.state", "Account", "state", "Paper account state from the ledger.", {
			outputs: [
				pin("cash_usd", "number"),
				pin("equity_usd", "number"),
				pin("daily_realized_pnl_usd", "number"),
				pin("open_positions", "number"),
			],
		}),
		node("time.now", "Clock", "state", "UTC time helpers.", {
			outputs: [pin("minute_of_day", "number"), pin("weekday", "number")],
		}),
		node("var.get", "Variable", "state", "Read a strategy variable, or its default when never set.", {
			outputs: [pin("value", "number")],
			params: [
				textParam("name", "Name", { required: true }),
				numberParam("default", "Default", { default: null, required: false }),
			],
		}),

		node("math.const", "Constant", "math", "A fixed number.", {
			outputs: [pin("value", "number")],
			params: [numberParam("value", "Value", { default: 0, required: true })],
		}),
		node("math.add", "Add", "math", "a + b. Unknown if either side is unknown.", {
			inputs: [input("a", "number"), input("b", "number")],
			outputs: [pin("value", "number")],
		}),
		node("math.sub", "Subtract", "math", "a − b. Unknown if either side is unknown.", {
			inputs: [input("a", "number"), input("b", "number")],
			outputs: [pin("value", "number")],
		}),
		node("math.mul", "Multiply", "math", "a × b. Unknown if either side is unknown.", {
			inputs: [input("a", "number"), input("b", "number")],
			outputs: [pin("value", "number")],
		}),
		node("math.div", "Divide", "math", "a ÷ b. Division by zero is unknown, never zero.", {
			inputs: [input("a", "number"), input("b", "number")],
			outputs: [pin("value", "number")],
		}),
		node("math.min", "Minimum", "math", "The smaller of two numbers.", {
			inputs: [input("a", "number"), input("b", "number")],
			outputs: [pin("value", "number")],
		}),
		node("math.max", "Maximum", "math", "The larger of two numbers.", {
			inputs: [input("a", "number"), input("b", "number")],
			outputs: [pin("value", "number")],
		}),
		node("math.abs", "Absolute", "math", "Absolute value.", {
			inputs: [input("a", "number")],
			outputs: [pin("value", "number")],
		}),
		node("math.compare", "Compare", "math", "Compare two numbers. The right side may come from the param instead of a wire.", {
			inputs: [input("a", "number"), input("b", "number", { required: false, defaultParam: "b" })],
			outputs: [pin("out", "bool")],
			params: [
				selectParam("op", "Operator", [">", ">=", "<", "<=", "==", "!="], ">"),
				numberParam("b", "Right side", { default: 0, required: false }),
			],
		}),
		node("math.between", "Between", "math", "Inclusive range check: lo ≤ x ≤ hi.", {
			inputs: [input("x", "number"), input("lo", "number"), input("hi", "number")],
			outputs: [pin("out", "bool")],
		}),
		node("logic.and", "And", "math", "Kleene AND: false wins, then unknown, then true.", {
			inputs: [
				input("a", "bool"),
				input("b", "bool"),
				input("c", "bool", { required: false }),
				input("d", "bool", { required: false }),
			],
			outputs: [pin("out", "bool")],
		}),
		node("logic.or", "Or", "math", "Kleene OR: true wins, then unknown, then false.", {
			inputs: [
				input("a", "bool"),
				input("b", "bool"),
				input("c", "bool", { required: false }),
				input("d", "bool", { required: false }),
			],
			outputs: [pin("out", "bool")],
		}),
		node("logic.not", "Not", "math", "Inverts a boolean. Unknown stays unknown.", {
			inputs: [input("a", "bool")],
			outputs: [pin("out", "bool")],
		}),
		node("logic.cross_above", "Cross above", "math", "True when a crossed above b on the last closed bar.", {
			inputs: [input("a", "series"), input("b", "series")],
			outputs: [pin("out", "bool")],
		}),
		node("logic.cross_below", "Cross below", "math", "True when a crossed below b on the last closed bar.", {
			inputs: [input("a", "series"), input("b", "series")],
			outputs: [pin("out", "bool")],
		}),

		node("flow.branch", "Branch", "flow", "Runs one exec path. An unknown condition takes neither branch and fails closed.", {
			inputs: [input("in", "exec"), input("cond", "bool")],
			outputs: [pin("true", "exec"), pin("false", "exec")],
		}),
		node("flow.sequence", "Sequence", "flow", "Fan an exec wire out into up to four ordered outputs.", {
			inputs: [input("in", "exec")],
			outputs: [pin("then0", "exec"), pin("then1", "exec"), pin("then2", "exec"), pin("then3", "exec")],
		}),
		node("flow.gate_cooldown", "Cooldown gate", "flow", "Passes only when the last fill is at least N minutes old. Unknown last fill passes.", {
			inputs: [input("in", "exec")],
			outputs: [pin("out", "exec")],
			params: [numberParam("minutes", "Minutes", { min: 0, max: 10080, default: 60, required: true, unit: "min" })],
		}),

		node("act.buy", "Buy", "actions", "Order intent to buy. Risk, quote and sizing still apply downstream.", {
			inputs: [input("in", "exec")],
			outputs: [pin("out", "exec")],
			params: [
				numberParam("amount_usd", "Amount", { min: 0, default: null, unit: "USD" }),
				numberParam("pct_cash", "Percent of cash", { min: 0, max: 100, default: null, unit: "%" }),
				numberParam("max_position_usd", "Max position", { min: 0, default: null, unit: "USD" }),
			],
		}),
		node("act.sell", "Sell", "actions", "Order intent to sell a fraction of the position. Flat is a hold, not a sell.", {
			inputs: [input("in", "exec")],
			outputs: [pin("out", "exec")],
			params: [numberParam("pct_position", "Percent of position", { min: 0, max: 100, default: 100, required: true, unit: "%" })],
		}),
		node("act.notify", "Notify", "actions", "Records a notification effect in the run log.", {
			inputs: [input("in", "exec")],
			outputs: [pin("out", "exec")],
			params: [
				textParam("message", "Message", { required: true }),
				selectParam("level", "Level", ["info", "warn"], "info"),
			],
		}),
		node("act.set_var", "Set variable", "actions", "Writes a strategy variable after the tick is recorded.", {
			inputs: [input("in", "exec"), input("value", "number")],
			outputs: [pin("out", "exec")],
			params: [textParam("name", "Name", { required: true })],
		}),

		node("guard.take_profit", "Take profit", "guards", "Hit when unrealized gain reaches the threshold.", {
			outputs: [pin("hit", "bool")],
			params: [numberParam("pct", "Take profit", { min: 0, max: 1000, default: 20, required: true, unit: "%" })],
		}),
		node("guard.stop_loss", "Stop loss", "guards", "Hit when unrealized loss reaches the threshold.", {
			outputs: [pin("hit", "bool")],
			params: [numberParam("pct", "Stop loss", { min: 0, max: 1000, default: 8, required: true, unit: "%" })],
		}),
		node("guard.trailing_stop", "Trailing stop", "guards", "Arms once the peak clears the activation, hits on the giveback.", {
			outputs: [pin("armed", "bool"), pin("hit", "bool")],
			params: [
				numberParam("activate_pct", "Activate at", { min: 0, max: 1000, default: 10, required: true, unit: "%" }),
				numberParam("giveback_pct", "Give back", { min: 0, max: 100, default: 5, required: true, unit: "%" }),
			],
		}),
	],
};

export function fixtureNode(type: string): CatalogueNode | undefined {
	return fixtureCatalogue.nodes.find((node) => node.type === type);
}

/* ------------------------------------------------------------ validation (§6 rules) */

const ORDER_ACTIONS = new Set(["act.buy", "act.sell"]);
const EVENT_TYPE = "event.tick";

function issue(
	code: string,
	message: string,
	nodeId: string | null = null,
	port: string | null = null,
): ValidationIssue {
	return { code, message, node: nodeId, port };
}

function paramNumber(node: CatalogueNode, name: string): number | null {
	const param = node.params.find((entry) => entry.name === name);
	if (!param || param.kind !== "number") return null;
	return typeof param.default === "number" ? param.default : null;
}

/** Bars of history this node needs before it can produce a value. */
export function nodeWarmup(spec: CatalogueNode): number {
	const period = paramNumber(spec, "period");
	switch (spec.type) {
		case "ind.rsi":
			return (period ?? 14) + 1;
		case "ind.sma":
		case "ind.ema":
		case "ind.highest":
		case "ind.lowest":
		case "ind.roc":
		case "ind.vwap":
		case "ind.bbands":
		case "ind.atr":
			return period ?? 14;
		case "ind.macd":
			return (paramNumber(spec, "slow") ?? 26) + (paramNumber(spec, "signal") ?? 9);
		default:
			return 0;
	}
}

interface GraphIndex {
	nodes: Map<string, GraphDocument["nodes"][number]>;
	specs: Map<string, CatalogueNode>;
}

function indexDocument(
	document: GraphDocument,
	catalogue: GraphCatalogue,
): GraphIndex {
	const nodes = new Map<string, GraphDocument["nodes"][number]>();
	const specs = new Map<string, CatalogueNode>();
	for (const node of document.nodes) {
		nodes.set(node.id, node);
		const spec = catalogue.nodes.find((entry) => entry.type === node.type);
		if (spec) specs.set(node.id, spec);
	}
	return { nodes, specs };
}

function hasCycle(document: GraphDocument): string[] {
	const adjacency = new Map<string, string[]>();
	for (const edge of document.edges) {
		const list = adjacency.get(edge.from.node) ?? [];
		list.push(edge.to.node);
		adjacency.set(edge.from.node, list);
	}
	const state = new Map<string, 0 | 1 | 2>();
	const cycleNodes: string[] = [];
	const visit = (id: string, stack: string[]): boolean => {
		const current = state.get(id) ?? 0;
		if (current === 1) {
			const from = stack.indexOf(id);
			const loop = stack.slice(from === -1 ? 0 : from);
			cycleNodes.push(loop[0] ?? id);
			return true;
		}
		if (current === 2) return false;
		state.set(id, 1);
		stack.push(id);
		for (const next of adjacency.get(id) ?? []) {
			if (visit(next, stack)) return true;
		}
		stack.pop();
		state.set(id, 2);
		return false;
	};
	for (const node of document.nodes) {
		if ((state.get(node.id) ?? 0) === 0 && visit(node.id, [])) break;
	}
	return cycleNodes;
}

function warmupPerTimeframe(document: GraphDocument, index: GraphIndex): Record<string, number> {
	// The longest indicator chain hanging off each candles node decides its warmup.
	const children = new Map<string, string[]>();
	for (const edge of document.edges) {
		const list = children.get(edge.from.node) ?? [];
		list.push(edge.to.node);
		children.set(edge.from.node, list);
	}
	const memo = new Map<string, number>();
	const depth = (id: string, seen: Set<string>): number => {
		if (memo.has(id)) return memo.get(id) ?? 0;
		if (seen.has(id)) return 0;
		seen.add(id);
		const spec = index.specs.get(id);
		const own = spec ? nodeWarmup(spec) : 0;
		let downstream = 0;
		for (const next of children.get(id) ?? []) {
			downstream = Math.max(downstream, depth(next, seen));
		}
		const total = own + downstream;
		memo.set(id, total);
		return total;
	};
	const warmup: Record<string, number> = {};
	for (const node of document.nodes) {
		if (node.type !== "data.candles") continue;
		const timeframe = typeof node.params.timeframe === "string" ? node.params.timeframe : "5m";
		const bars = depth(node.id, new Set());
		warmup[timeframe] = Math.max(warmup[timeframe] ?? 0, Math.max(1, bars));
	}
	return warmup;
}

/**
 * The local "compile" step. Mirrors the §6 error/warning codes the editor surfaces, so a
 * graph that fails here never reaches the save button. The API is still the authority:
 * in live mode the editor shows the API report, not this one.
 */
export function validateGraphDocument(
	document: GraphDocument,
	catalogue: GraphCatalogue = fixtureCatalogue,
): ValidationReport {
	const errors: ValidationIssue[] = [];
	const warnings: ValidationIssue[] = [];
	const index = indexDocument(document, catalogue);

	if (document.schema !== GRAPH_SCHEMA) {
		errors.push(issue("GRAPH_SCHEMA", `The document schema must be "${GRAPH_SCHEMA}".`));
	}
	if (document.nodes.length > GRAPH_LIMITS.nodes) {
		errors.push(issue("GRAPH_SCHEMA", `At most ${GRAPH_LIMITS.nodes} nodes are allowed.`));
	}
	if (document.edges.length > GRAPH_LIMITS.edges) {
		errors.push(issue("GRAPH_SCHEMA", `At most ${GRAPH_LIMITS.edges} edges are allowed.`));
	}
	if (document.comments.length > GRAPH_LIMITS.comments) {
		errors.push(issue("GRAPH_SCHEMA", `At most ${GRAPH_LIMITS.comments} comments are allowed.`));
	}
	if (JSON.stringify(document).length > GRAPH_LIMITS.bytes) {
		errors.push(issue("GRAPH_SCHEMA", "The document exceeds 256 KiB."));
	}

	const seenIds = new Set<string>();
	for (const entry of [...document.nodes, ...document.comments]) {
		if (!NODE_ID_PATTERN.test(entry.id)) {
			errors.push(issue("GRAPH_SCHEMA", `Id "${entry.id}" must match [A-Za-z0-9_-]{1,40}.`, entry.id));
		}
		if (seenIds.has(entry.id)) {
			errors.push(issue("GRAPH_SCHEMA", `Duplicate id "${entry.id}".`, entry.id));
		}
		seenIds.add(entry.id);
	}

	for (const graphNode of document.nodes) {
		if (!index.specs.has(graphNode.id)) {
			errors.push(issue("NODE_TYPE_UNKNOWN", `Unknown node type "${graphNode.type}".`, graphNode.id));
			continue;
		}
		const spec = index.specs.get(graphNode.id) as CatalogueNode;
		for (const [key, value] of Object.entries(graphNode.params ?? {})) {
			const param = spec.params.find((entry) => entry.name === key);
			if (!param) {
				errors.push(issue("PARAM_INVALID", `Unknown parameter "${key}".`, graphNode.id));
				continue;
			}
			if (param.kind === "number") {
				const amount = typeof value === "number" ? value : Number(value);
				if (!Number.isFinite(amount)) {
					errors.push(issue("PARAM_INVALID", `${param.title} must be a number.`, graphNode.id));
					continue;
				}
				if (param.min !== null && amount < Number(param.min)) {
					errors.push(issue("PARAM_INVALID", `${param.title} must be ≥ ${param.min}.`, graphNode.id));
				}
				if (param.max !== null && amount > Number(param.max)) {
					errors.push(issue("PARAM_INVALID", `${param.title} must be ≤ ${param.max}.`, graphNode.id));
				}
			}
			if (param.kind === "select" && param.choices && !param.choices.includes(String(value))) {
				errors.push(
					issue("PARAM_INVALID", `${param.title} must be one of ${param.choices.join(", ")}.`, graphNode.id),
				);
			}
			if (param.kind === "text" && String(value).length > 200) {
				errors.push(issue("PARAM_INVALID", `${param.title} must be 200 characters or fewer.`, graphNode.id));
			}
		}
		for (const param of spec.params) {
			const value = graphNode.params?.[param.name];
			if (value === undefined || String(value).trim() === "") {
				if (param.required) {
					errors.push(issue("PARAM_INVALID", `${param.title} is required.`, graphNode.id));
				}
				continue;
			}
		}
		if (spec.type === "act.buy") {
			const amount = graphNode.params?.amount_usd;
			const pct = graphNode.params?.pct_cash;
			const hasAmount = amount !== undefined && String(amount).trim() !== "";
			const hasPct = pct !== undefined && String(pct).trim() !== "";
			if (hasAmount === hasPct) {
				errors.push(
					issue(
						"PARAM_INVALID",
						"Buy needs exactly one of amount_usd or pct_cash.",
						graphNode.id,
					),
				);
			}
		}
		if (spec.type === "act.notify") {
			const message = graphNode.params?.message;
			if (typeof message === "string" && message.length > 200) {
				errors.push(issue("PARAM_INVALID", "Message must be 200 characters or fewer.", graphNode.id));
			}
		}
		const warmup = nodeWarmup(spec);
		if (warmup > 500) {
			warnings.push(
				issue("WARMUP_LONG", `${spec.title} needs ${warmup} bars of history.`, graphNode.id),
			);
		}
	}

	const inputCount = new Map<string, number>();
	const execOutCount = new Map<string, number>();
	const outgoing = new Map<string, number>();
	for (const edge of document.edges) {
		const fromNode = index.nodes.get(edge.from.node);
		const toNode = index.nodes.get(edge.to.node);
		if (!fromNode || !toNode) {
			errors.push(
				issue(
					"GRAPH_SCHEMA",
					`Edge references a missing node (${edge.from.node} → ${edge.to.node}).`,
					fromNode ? edge.to.node : edge.from.node,
				),
			);
			continue;
		}
		const sourceType = outputPinType(catalogue, fromNode.type, edge.from.port);
		const targetType = inputPinType(catalogue, toNode.type, edge.to.port);
		if (!sourceType) {
			errors.push(
				issue("PORT_UNKNOWN", `${fromNode.type} has no output "${edge.from.port}".`, edge.from.node, edge.from.port),
			);
			continue;
		}
		if (!targetType) {
			errors.push(
				issue("PORT_UNKNOWN", `${toNode.type} has no input "${edge.to.port}".`, edge.to.node, edge.to.port),
			);
			continue;
		}
		if (!pinsCompatible(sourceType, targetType)) {
			errors.push(
				issue(
					"PIN_TYPE_MISMATCH",
					`${sourceType} output cannot feed a ${targetType} input.`,
					edge.to.node,
					edge.to.port,
				),
			);
		}
		const key = `${edge.to.node}:${edge.to.port}`;
		inputCount.set(key, (inputCount.get(key) ?? 0) + 1);
		if (sourceType === "exec") {
			const execKey = `${edge.from.node}:${edge.from.port}`;
			execOutCount.set(execKey, (execOutCount.get(execKey) ?? 0) + 1);
		}
		outgoing.set(edge.from.node, (outgoing.get(edge.from.node) ?? 0) + 1);
	}

	for (const [key, count] of inputCount) {
		if (count > 1) {
			const [nodeId, port] = key.split(":");
			errors.push(issue("INPUT_MULTIPLE", `Input "${port}" accepts one wire (found ${count}).`, nodeId, port));
		}
	}
	for (const [key, count] of execOutCount) {
		if (count > 1) {
			const [nodeId, port] = key.split(":");
			errors.push(
				issue("EXEC_FANOUT", `Exec output "${port}" accepts one wire (found ${count}); use flow.sequence.`, nodeId, port),
			);
		}
	}

	for (const graphNode of document.nodes) {
		const spec = index.specs.get(graphNode.id);
		if (!spec) continue;
		for (const port of spec.inputs) {
			const connected = (inputCount.get(`${graphNode.id}:${port.name}`) ?? 0) > 0;
			if (!connected && port.required) {
				errors.push(
					issue("INPUT_REQUIRED", `Input "${port.name}" (${port.type}) must be connected.`, graphNode.id, port.name),
				);
			}
		}
	}

	for (const cycleNode of hasCycle(document)) {
		errors.push(issue("CYCLE", "The graph contains a cycle. Loops are not allowed in v1.", cycleNode));
	}

	const eventIds = document.nodes.filter((entry) => entry.type === EVENT_TYPE).map((entry) => entry.id);
	if (eventIds.length === 0) {
		errors.push(issue("NO_EVENT", "No event.tick node: nothing would ever run this graph."));
	}

	// Exec reachability from every event node.
	const execTargets = new Map<string, string[]>();
	for (const edge of document.edges) {
		const fromNode = index.nodes.get(edge.from.node);
		if (!fromNode) continue;
		const sourceType = outputPinType(catalogue, fromNode.type, edge.from.port);
		if (sourceType !== "exec") continue;
		const list = execTargets.get(edge.from.node) ?? [];
		list.push(edge.to.node);
		execTargets.set(edge.from.node, list);
	}
	const reachable = new Set<string>();
	const stack = [...eventIds];
	while (stack.length > 0) {
		const id = stack.pop() as string;
		if (reachable.has(id)) continue;
		reachable.add(id);
		for (const next of execTargets.get(id) ?? []) {
			if (!reachable.has(next)) stack.push(next);
		}
	}

	const orderActions = document.nodes.filter((entry) => ORDER_ACTIONS.has(entry.type));
	const reachableOrders = orderActions.filter((entry) => reachable.has(entry.id));
	if (orderActions.length === 0) {
		errors.push(
			issue("NO_ACTION", "No order action: add a Buy or Sell wired to an exec pin."),
		);
	} else if (reachableOrders.length === 0) {
		errors.push(
			issue("NO_ACTION", "No order action is reachable from the event. Wire a Buy or Sell to an exec path."),
		);
	}
	for (const action of orderActions) {
		if (!reachable.has(action.id)) {
			errors.push(
				issue(
					"ACTION_UNREACHABLE",
					`${action.type} is not reachable from an event, so it never fires.`,
					action.id,
				),
			);
		}
	}

	for (const graphNode of document.nodes) {
		const spec = index.specs.get(graphNode.id);
		if (!spec) continue;
		const isSource = spec.outputs.length > 0;
		const feedsNothing = (outgoing.get(graphNode.id) ?? 0) === 0;
		if (isSource && feedsNothing && spec.outputs.every((output) => output.type !== "exec")) {
			warnings.push(issue("NODE_ORPHAN", `${spec.title} feeds nothing.`, graphNode.id));
		}
	}

	const reachableBuys = reachableOrders.filter((entry) => entry.type === "act.buy");
	const reachableSells = reachableOrders.filter((entry) => entry.type === "act.sell");
	if (reachableBuys.length > 0 && reachableSells.length === 0) {
		warnings.push(issue("NO_EXIT", "Buys are reachable but no sell is: this graph never exits."));
	}
	if (reachableOrders.length > 1) {
		warnings.push(
			issue(
				"DUPLICATE_ORDER_PATH",
				`${reachableOrders.length} order actions are reachable; only the first per tick fires.`,
			),
		);
	}

	return {
		ok: errors.length === 0,
		errors,
		warnings,
		warmup_bars: warmupPerTimeframe(document, index),
	};
}

/* --------------------------------------------------------------- fixture snapshots */

export const FIXTURE_NOW = "2026-10-22T09:41:00Z";

export const fixtureGraphDocument: GraphDocument = {
	schema: GRAPH_SCHEMA,
	nodes: [
		{ id: "tick", type: "event.tick", params: {}, ui: { x: 40, y: 40 } },
		{ id: "seq", type: "flow.sequence", params: {}, ui: { x: 260, y: 40 } },
		{ id: "branch", type: "flow.branch", params: {}, ui: { x: 540, y: 40 } },
		{ id: "buy", type: "act.buy", params: { amount_usd: "100", max_position_usd: "250" }, ui: { x: 820, y: 20 } },
		{ id: "candles", type: "data.candles", params: { timeframe: "5m" }, ui: { x: 40, y: 220 } },
		{ id: "fast", type: "ind.ema", params: { period: 9 }, ui: { x: 300, y: 160 } },
		{ id: "slow", type: "ind.ema", params: { period: 21 }, ui: { x: 300, y: 340 } },
		{ id: "cross", type: "logic.cross_above", params: {}, ui: { x: 540, y: 240 } },
		{ id: "take_profit", type: "guard.take_profit", params: { pct: 20 }, ui: { x: 300, y: 520 } },
		{ id: "exit_branch", type: "flow.branch", params: {}, ui: { x: 540, y: 480 } },
		{ id: "sell", type: "act.sell", params: { pct_position: 100 }, ui: { x: 820, y: 460 } },
	],
	edges: [
		{ from: { node: "tick", port: "out" }, to: { node: "seq", port: "in" } },
		{ from: { node: "seq", port: "then0" }, to: { node: "branch", port: "in" } },
		{ from: { node: "seq", port: "then1" }, to: { node: "exit_branch", port: "in" } },
		{ from: { node: "candles", port: "close" }, to: { node: "fast", port: "series" } },
		{ from: { node: "candles", port: "close" }, to: { node: "slow", port: "series" } },
		{ from: { node: "fast", port: "series" }, to: { node: "cross", port: "a" } },
		{ from: { node: "slow", port: "series" }, to: { node: "cross", port: "b" } },
		{ from: { node: "cross", port: "out" }, to: { node: "branch", port: "cond" } },
		{ from: { node: "branch", port: "true" }, to: { node: "buy", port: "in" } },
		{ from: { node: "take_profit", port: "hit" }, to: { node: "exit_branch", port: "cond" } },
		{ from: { node: "exit_branch", port: "true" }, to: { node: "sell", port: "in" } },
	],
	comments: [{ id: "entry", text: "Entry: EMA(9/21) cross above", ui: { x: 280, y: -40, w: 520, h: 220 } }],
};

export interface GraphStrategyRow {
	id: string;
	name: string;
	chain: string;
	token_address: string;
	token_symbol: string;
	kind: string;
	status: string;
	params: Record<string, unknown>;
	last_run_at: string | null;
	created_at: string;
}

export const fixtureGraphStrategy: GraphStrategyRow = {
	id: "str-bonk-graph",
	name: "BONK EMA cross",
	chain: "solana",
	token_address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
	token_symbol: "BONK",
	kind: "graph",
	status: "active",
	params: {
		graph: fixtureGraphDocument,
		interval_minutes: 15,
		max_position_usd: "250",
	},
	last_run_at: "2026-10-22T09:30:00Z",
	created_at: "2026-10-20T11:00:00Z",
};

export interface GraphStrategyRun {
	id: string;
	strategy_id: string;
	action: string;
	reason: string;
	inputs: Record<string, unknown>;
	order_id: string | null;
	created_at: string;
}

export const fixtureGraphStrategyRuns: GraphStrategyRun[] = [
	{
		id: "run-graph-2",
		strategy_id: "str-bonk-graph",
		action: "buy",
		reason: "graph:branch.true → buy · 1 order intent · paper order accepted",
		inputs: {
			graph_trace: {
				values: {
					candles: "0.00003142",
					fast: "0.00002980",
					slow: "0.00002910",
					cross: true,
					take_profit: false,
					buy: null,
					sell: null,
				},
				exec: ["tick", "seq", "branch", "buy"],
			},
			effects: [{ kind: "order", node: "buy", action: "buy", amount_usd: "100" }],
			context_digest: "fixture-context-1",
		},
		order_id: "paper-ord-1044",
		created_at: "2026-10-22T09:30:00Z",
	},
	{
		id: "run-graph-1",
		strategy_id: "str-bonk-graph",
		action: "hold",
		reason: "graph:branch.true not taken (cross unknown)",
		inputs: {
			graph_trace: {
				values: { candles: "0.00003090", fast: "0.00003010", slow: "0.00003020", cross: false },
				exec: ["tick", "seq", "branch"],
			},
			effects: [],
			context_digest: "fixture-context-0",
		},
		order_id: null,
		created_at: "2026-10-22T09:15:00Z",
	},
];

/* ------------------------------------------------------- deterministic fixture results */

function hashText(text: string): number {
	let hash = 0x811c9dc5;
	for (let i = 0; i < text.length; i += 1) {
		hash ^= text.charCodeAt(i);
		hash = Math.imul(hash, 0x01000193) >>> 0;
	}
	return hash >>> 0;
}

function decimal(value: number, places: number): string {
	return value.toFixed(places);
}

function fixtureNumberFor(node: GraphDocument["nodes"][number]): string {
	switch (node.type) {
		case "data.price":
			return "0.00003142";
		case "data.change":
			return "6.40";
		case "data.liquidity":
			return "412000.00";
		case "data.volume24h":
			return "1880000.00";
		case "data.pair_age":
			return "3120";
		case "math.const":
			return typeof node.params.value === "number" ? String(node.params.value) : String(node.params.value ?? "0");
		case "pos.state":
			return "0.00003010";
		case "acct.state":
			return "9709.36";
		case "time.now":
			return "561";
		default: {
			const h = hashText(node.id);
			if (node.type.startsWith("guard.")) return decimal((h % 4000) / 100, 2);
			if (node.type.startsWith("ind.")) return decimal(20 + (h % 6000) / 100, 2);
			return decimal((h % 90000) / 100, 2);
		}
	}
}

function fixtureValueFor(node: GraphDocument["nodes"][number]): string | boolean | null {
	if (node.type === "logic.cross_above" || node.type === "logic.cross_below") return true;
	if (node.type === "pos.state") return true;
	if (node.type.startsWith("guard.")) return hashText(node.id) % 2 === 0;
	if (node.type.startsWith("logic.") || node.type === "math.compare" || node.type === "math.between") {
		return hashText(node.id) % 3 !== 0;
	}
	if (node.type.startsWith("act.") || node.type === "event.tick" || node.type.startsWith("flow.")) return null;
	return fixtureNumberFor(node);
}

function execOrder(document: GraphDocument): string[] {
	const byNode = new Map<string, GraphDocument["edges"][number]>();
	for (const edge of document.edges) {
		if (edge.from.port === "in") continue;
		const spec = fixtureNode(
			document.nodes.find((entry) => entry.id === edge.from.node)?.type ?? "",
		);
		const sourcePort = spec?.outputs.find((output) => output.name === edge.from.port);
		if (sourcePort?.type !== "exec") continue;
		if (!byNode.has(edge.from.node)) byNode.set(edge.from.node, edge);
	}
	const order: string[] = [];
	const seen = new Set<string>();
	const walk = (id: string) => {
		if (seen.has(id)) return;
		seen.add(id);
		order.push(id);
		const edge = byNode.get(id);
		if (edge) walk(edge.to.node);
	};
	for (const node of document.nodes.filter((entry) => entry.type === EVENT_TYPE)) {
		walk(node.id);
	}
	return order;
}

function traceValues(document: GraphDocument): GraphTraceValues {
	const values: GraphTraceValues = {};
	for (const node of document.nodes) {
		if (node.type.startsWith("act.") || node.type.startsWith("flow.") || node.type === EVENT_TYPE) {
			values[node.id] = null;
			continue;
		}
		values[node.id] = fixtureValueFor(node);
	}
	return values;
}

/** A deterministic dry run over the document — labelled FIXTURE in the UI. */
export function fixtureDryRun(document: GraphDocument): DryRunResult {
	const exec = execOrder(document);
	const effects: GraphEffect[] = [];
	let orderSeen = false;
	for (const id of exec) {
		const node = document.nodes.find((entry) => entry.id === id);
		if (!node) continue;
		if (node.type === "act.buy" || node.type === "act.sell") {
			const suppressed = orderSeen;
			orderSeen = true;
			effects.push({
				kind: "order",
				node: node.id,
				action: node.type === "act.buy" ? "buy" : "sell",
				amount_usd: node.params.amount_usd !== undefined ? String(node.params.amount_usd) : null,
				sell_fraction:
					node.type === "act.sell"
						? decimal(Number(node.params.pct_position ?? 100) / 100, 4)
						: null,
				suppressed,
			});
		}
		if (node.type === "act.notify") {
			effects.push({
				kind: "notify",
				node: node.id,
				message: String(node.params.message ?? ""),
				level: String(node.params.level ?? "info"),
			});
		}
		if (node.type === "act.set_var") {
			effects.push({ kind: "set_var", node: node.id, name: String(node.params.name ?? "") });
		}
	}

	const firstOrder = effects.find((effect) => effect.kind === "order");
	const action = firstOrder ? String(firstOrder.action) : "hold";
	const reason = firstOrder
		? `FIXTURE dry run: ${exec.join(" → ")} · ${effects.length} effect(s)`
		: `FIXTURE dry run: no order action on the exec path (${exec.join(" → ") || "no event"})`;

	return {
		decision: {
			action,
			amount_usd: firstOrder?.amount_usd ?? null,
			sell_fraction: firstOrder?.sell_fraction ?? null,
			reason,
			effects,
		},
		trace: { values: traceValues(document), exec },
		context: {
			now: FIXTURE_NOW,
			price_usd: "0.00003142",
			candles: { "5m": { bars: 240, last_close_time: "2026-10-22T09:40:00Z", stale: false } },
			position: { open: false, quantity: "0", value_usd: null },
			account: { cash_usd: "9709.36", equity_usd: "9709.36" },
		},
		mode: "DRY_RUN",
	};
}

const FIXTURE_METRICS: BacktestMetrics = {
	net_pnl_usd: "142.80",
	return_pct: "1.43",
	max_drawdown_pct: "-6.20",
	win_rate_pct: "46.15",
	profit_factor: "1.62",
	trades: "13",
	exposure_pct: "38.40",
	sharpe: "1.18",
};

function fixtureEquity(): BacktestEquityPoint[] {
	const points: BacktestEquityPoint[] = [];
	let equity = 10000;
	let peak = equity;
	for (let i = 0; i < 48; i += 1) {
		const h = hashText(`equity-${i}`);
		const swing = ((h % 160) - 60) / 100;
		equity = Math.max(8000, equity + swing + (i % 7 === 0 ? 6 : 0));
		peak = Math.max(peak, equity);
		const drawdown = ((equity - peak) / peak) * 100;
		const stamp = new Date(Date.UTC(2026, 9, 1, 0, i * 60));
		points.push({
			t: stamp.toISOString(),
			equity_usd: equity.toFixed(2),
			drawdown_pct: drawdown.toFixed(2),
		});
	}
	return points;
}

function fixtureTrades(): BacktestTrade[] {
	return [
		{
			entry_time: "2026-10-02T04:00:00Z",
			exit_time: "2026-10-02T09:00:00Z",
			side: "buy",
			qty: "3181670.17",
			entry_price: "0.00003143",
			exit_price: "0.00003410",
			pnl_usd: "84.90",
			pnl_pct: "8.49",
			fees_usd: "0.62",
			reason: "take_profit",
		},
		{
			entry_time: "2026-10-05T12:00:00Z",
			exit_time: "2026-10-05T19:00:00Z",
			side: "buy",
			qty: "2958579.88",
			entry_price: "0.00003380",
			exit_price: "0.00003190",
			pnl_usd: "-56.20",
			pnl_pct: "-5.62",
			fees_usd: "0.58",
			reason: "stop_loss",
		},
		{
			entry_time: "2026-10-09T02:00:00Z",
			exit_time: "2026-10-10T11:00:00Z",
			side: "buy",
			qty: "2702702.70",
			entry_price: "0.00003700",
			exit_price: "0.00004120",
			pnl_usd: "114.10",
			pnl_pct: "11.35",
			fees_usd: "0.71",
			reason: "take_profit",
		},
	];
}

/** A deterministic backtest result — labelled BACKTEST + FIXTURE, never PAPER or LIVE. */
export function fixtureBacktest(document: GraphDocument, strategyId?: string): Backtest {
	const hits: Record<string, number> = {};
	for (const node of document.nodes) {
		hits[node.id] = hashText(`${node.id}:hits`) % 240;
	}
	return {
		id: "bt-fixture-1",
		created_at: FIXTURE_NOW,
		label: "BACKTEST",
		request: {
			strategy_id: strategyId ?? null,
			timeframe: "5m",
			bars: 480,
			fee_bps: 10,
			slippage_bps: 5,
			starting_cash_usd: "10000",
		},
		data_source: "FIXTURE sample · not a market feed",
		fill_model: "FIXTURE next-bar open · fee 10 bps · slippage 5 bps",
		metrics: { ...FIXTURE_METRICS },
		equity: fixtureEquity(),
		trades: fixtureTrades(),
		node_hits: hits,
	};
}

export const fixtureBacktests: Backtest[] = [fixtureBacktest(fixtureGraphDocument, fixtureGraphStrategy.id)];

/** Load a document out of a strategy row's `params.graph`, or report why it cannot be. */
export function graphFromParams(
	params: Record<string, unknown> | undefined,
): { ok: true; document: GraphDocument } | { ok: false; message: string } {
	const graph = params?.graph;
	if (!graph) {
		return { ok: false, message: "This strategy has no params.graph document." };
	}
	const parsed = parseGraphDocument(graph);
	if (!parsed.ok) return { ok: false, message: parsed.message };
	return { ok: true, document: parsed.document };
}

