// Declarative strategy fixtures. A strategy is configuration only: a kind, a token and
// numeric params. The web never evaluates a strategy locally; it posts the config to the
// engine and renders the engine's decision and run log.

export type StrategyKind = "momentum" | "dip" | "dca" | "threshold";
export type StrategyStatus = "active" | "paused";
export type StrategyParams = Record<string, string | number>;

export interface Strategy {
	id: string;
	name: string;
	chain: string;
	token_address: string;
	token_symbol: string;
	kind: StrategyKind;
	params: StrategyParams;
	status: StrategyStatus;
	last_run_at: string | null;
	created_at: string;
	// Fixture-only sample. The P0 list endpoint does not return per-strategy PnL, so live
	// mode renders — rather than inventing a number.
	pnl_usd?: string | null;
}

export interface StrategyRun {
	id: string;
	strategy_id: string;
	action: string;
	reason: string;
	inputs: Record<string, unknown>;
	order_id: string | null;
	created_at: string;
}

export const strategyKindMeta: Record<
	StrategyKind,
	{ label: string; description: string; rule: string }
> = {
	momentum: {
		label: "Momentum",
		description: "Buy strength, exit on reversal, take-profit or stop-loss.",
		rule: "lookback change ≥ entry_change_pct",
	},
	dip: {
		label: "Dip",
		description: "Buy a drawdown, exit on take-profit or stop-loss.",
		rule: "lookback change ≤ −entry_change_pct",
	},
	dca: {
		label: "DCA",
		description: "Buy a fixed amount on an interval until the position cap.",
		rule: "every interval_minutes until max_position_usd",
	},
	threshold: {
		label: "Threshold",
		description: "Buy below a price, sell above a price.",
		rule: "mark ≤ buy_below_usd, then mark ≥ sell_above_usd",
	},
};

export interface ParamField {
	name: string;
	label: string;
	hint: string;
	kinds: StrategyKind[];
	defaultValue: string;
	type: "number" | "text";
	min?: string;
	step?: string;
}

// One home for the declarative parameter vocabulary. The form renders these fields; the
// API stores the values and the engine interprets them. No duplicated defaults in the UI.
export const strategyParamFields: ParamField[] = [
	{
		name: "order_usd",
		label: "Order size (USD)",
		hint: "Paper notional per order",
		kinds: ["momentum", "dip", "dca", "threshold"],
		defaultValue: "100",
		type: "number",
		min: "1",
		step: "0.01",
	},
	{
		name: "take_profit_pct",
		label: "Take profit (%)",
		hint: "Exit when unrealized reaches this",
		kinds: ["momentum", "dip"],
		defaultValue: "20",
		type: "number",
		step: "0.1",
	},
	{
		name: "stop_loss_pct",
		label: "Stop loss (%)",
		hint: "Exit when unrealized falls this far",
		kinds: ["momentum", "dip"],
		defaultValue: "8",
		type: "number",
		step: "0.1",
	},
	{
		name: "lookback",
		label: "Lookback",
		hint: "Window used for the signal, e.g. 1h",
		kinds: ["momentum", "dip"],
		defaultValue: "1h",
		type: "text",
	},
	{
		name: "entry_change_pct",
		label: "Entry change (%)",
		hint: "Momentum buys above; dip buys below the negative",
		kinds: ["momentum", "dip"],
		defaultValue: "5",
		type: "number",
		step: "0.1",
	},
	{
		name: "exit_change_pct",
		label: "Exit change (%)",
		hint: "Exit the lookback trade at this change",
		kinds: ["momentum", "dip"],
		defaultValue: "-3",
		type: "number",
		step: "0.1",
	},
	{
		name: "interval_minutes",
		label: "Interval (minutes)",
		hint: "One decision per window",
		kinds: ["momentum", "dip", "dca", "threshold"],
		defaultValue: "60",
		type: "number",
		min: "1",
		step: "1",
	},
	{
		name: "max_position_usd",
		label: "Max position (USD)",
		hint: "Hard ceiling for this strategy",
		kinds: ["momentum", "dip", "dca", "threshold"],
		defaultValue: "500",
		type: "number",
		min: "1",
		step: "0.01",
	},
	{
		name: "buy_below_usd",
		label: "Buy below (USD)",
		hint: "Threshold entry price",
		kinds: ["threshold"],
		defaultValue: "0.00002",
		type: "number",
		min: "0",
		step: "any",
	},
	{
		name: "sell_above_usd",
		label: "Sell above (USD)",
		hint: "Threshold exit price",
		kinds: ["threshold"],
		defaultValue: "0.00005",
		type: "number",
		min: "0",
		step: "any",
	},
];

export const strategyChains = [
	{ id: "solana", name: "Solana" },
	{ id: "base", name: "Base" },
	{ id: "ethereum", name: "Ethereum" },
	{ id: "arbitrum", name: "Arbitrum" },
	{ id: "polygon", name: "Polygon" },
	{ id: "bnb", name: "BNB Chain" },
] as const;

export const fixtureStrategies: Strategy[] = [
	{
		id: "str-bonk-momentum",
		name: "BONK momentum",
		chain: "solana",
		token_address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		token_symbol: "BONK",
		kind: "momentum",
		params: {
			order_usd: 100,
			take_profit_pct: 20,
			stop_loss_pct: 8,
			lookback: "1h",
			entry_change_pct: 5,
			exit_change_pct: -3,
			interval_minutes: 60,
			max_position_usd: 500,
		},
		status: "active",
		last_run_at: "2026-10-22T09:41:08Z",
		created_at: "2026-10-14T14:02:00Z",
		pnl_usd: "38.12",
	},
	{
		id: "str-aero-dip",
		name: "AERO dip",
		chain: "base",
		token_address: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
		token_symbol: "AERO",
		kind: "dip",
		params: {
			order_usd: 75,
			take_profit_pct: 15,
			stop_loss_pct: 10,
			lookback: "4h",
			entry_change_pct: 6,
			exit_change_pct: -4,
			interval_minutes: 120,
			max_position_usd: 300,
		},
		status: "paused",
		last_run_at: "2026-10-22T08:12:40Z",
		created_at: "2026-10-15T09:30:00Z",
		pnl_usd: "-6.12",
	},
	{
		id: "str-sol-dca",
		name: "SOL weekly DCA",
		chain: "solana",
		token_address: "So11111111111111111111111111111111111111112",
		token_symbol: "SOL",
		kind: "dca",
		params: {
			order_usd: 50,
			interval_minutes: 10080,
			max_position_usd: 1000,
		},
		status: "active",
		last_run_at: "2026-10-21T06:00:00Z",
		created_at: "2026-10-01T06:00:00Z",
		pnl_usd: "12.80",
	},
	{
		id: "str-bonk-range",
		name: "BONK range",
		chain: "solana",
		token_address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		token_symbol: "BONK",
		kind: "threshold",
		params: {
			order_usd: 40,
			buy_below_usd: 0.00002,
			sell_above_usd: 0.00005,
			interval_minutes: 30,
			max_position_usd: 200,
		},
		status: "active",
		last_run_at: "2026-10-22T09:30:00Z",
		created_at: "2026-10-18T11:15:00Z",
		pnl_usd: "4.40",
	},
];

export const fixtureStrategyRuns: StrategyRun[] = [
	{
		id: "run-1",
		strategy_id: "str-bonk-momentum",
		action: "enter",
		reason: "momentum:lookback +6.4% ≥ entry 5.0% · paper order accepted",
		inputs: { lookback: "1h", change_pct: 6.4, mark_usd: 0.00003142, order_usd: 100 },
		order_id: "paper-ord-1041",
		created_at: "2026-10-22T09:41:08Z",
	},
	{
		id: "run-2",
		strategy_id: "str-bonk-range",
		action: "hold",
		reason: "threshold:mark $0.00003142 is between buy $0.00002000 and sell $0.00005000",
		inputs: { mark_usd: 0.00003142, buy_below_usd: 0.00002, sell_above_usd: 0.00005 },
		order_id: null,
		created_at: "2026-10-22T09:30:00Z",
	},
	{
		id: "run-3",
		strategy_id: "str-aero-dip",
		action: "skip",
		reason: "strategy_paused · no decision taken",
		inputs: { status: "paused" },
		order_id: null,
		created_at: "2026-10-22T08:12:40Z",
	},
	{
		id: "run-4",
		strategy_id: "str-sol-dca",
		action: "buy",
		reason: "dca:interval elapsed · $50.00 paper buy",
		inputs: { interval_minutes: 10080, order_usd: 50 },
		order_id: "paper-ord-1038",
		created_at: "2026-10-21T06:00:00Z",
	},
	{
		id: "run-5",
		strategy_id: "str-bonk-momentum",
		action: "hold",
		reason: "momentum:lookback +2.1% below entry 5.0%",
		inputs: { lookback: "1h", change_pct: 2.1, mark_usd: 0.00003010 },
		order_id: null,
		created_at: "2026-10-22T08:41:00Z",
	},
	{
		id: "run-6",
		strategy_id: "str-aero-dip",
		action: "exit",
		reason: "stop_loss −10.2% ≤ −10.0% · paper order accepted",
		inputs: { change_pct: -10.2, stop_loss_pct: 10 },
		order_id: "paper-ord-1031",
		created_at: "2026-10-21T20:04:00Z",
	},
];
