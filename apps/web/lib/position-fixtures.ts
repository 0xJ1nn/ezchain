// Position tile fixtures. These stand in for the server PositionView until apps/api
// exists. The tile renders these strings; it never recomputes PnL or executable value.

export type TileTone = "win" | "loss" | "flat" | "error";

export interface TileMarketCell {
	k: string;
	v: string;
	src: string;
	vTone?: "ok" | "err" | "muted";
	srcTone?: "live" | "stale" | "dead";
}

export interface TileMoneyCell {
	k: string;
	v: string;
	tone?: "ok" | "err";
}

export interface TileStep {
	label: string;
	state: "on" | "next" | "off";
}

export interface TileLevel {
	y: number;
	label: string;
	className: string;
	labelClass?: string;
}

export interface TileChart {
	ariaLabel: string;
	gridY: number[];
	levels: TileLevel[];
	points: string;
	dot: { x: number; y: number };
	peak?: { x: number; y: number; label: string };
	axis: { x: number; y: number; text: string }[];
	foot: { x: number; y: number; text: string }[];
}

export interface PositionTileView {
	id: string;
	symbol: string;
	name: string;
	sub: string;
	tone: TileTone;
	state: string;
	stateTone: "default" | "ok" | "err" | "warn" | "info";
	pnlPct: string;
	pnlUsd: string;
	pnlSol?: string;
	pnlTone: "ok" | "err" | "muted";
	price: string;
	priceTone?: "muted";
	mark: string;
	markTone: "live" | "stale" | "dead";
	markToneClass?: "err";
	tags: { label: string; tone?: "default" | "ghost" | "info" | "err" | "warn" }[];
	chart: TileChart;
	market: TileMarketCell[];
	money: TileMoneyCell[];
	ladder:
		| {
				kind: "steps";
				state: string;
				stateTone: "default" | "info" | "err" | "warn";
				bag: string;
				next: string;
				steps: TileStep[];
		  }
		| {
				kind: "note";
				state: string;
				stateTone: "err";
				note: string;
				lastExecutable: string;
		  };
	prov: { label: string; tone?: "default" | "ghost" | "ok" | "err" | "warn"; className?: string }[];
	budget:
		| { kind: "bar"; width: string; cap: string }
		| { kind: "text"; cap: string };
	actions: { label: string; className: string; disabled?: boolean }[];
	note: string;
}

const bonkChart: TileChart = {
	ariaLabel: "BONK price with entry, trail, bank and ladder levels",
	gridY: [4, 56, 100],
	levels: [
		{ y: 88, label: "ENTRY $0.000024", className: "level level-entry" },
		{ y: 22, label: "BANK +69.6% · sold 30.5%", className: "level" },
		{ y: 64, label: "ARM +25%", className: "level level-arm" },
		{ y: 40, label: "TRAIL", className: "level level-trail" },
	],
	points:
		"0,90 40,86 80,88 120,80 160,76 200,68 240,72 280,62 320,58 360,46 400,52 440,36 480,30 520,26 560,58",
	dot: { x: 560, y: 58 },
	peak: { x: 520, y: 16, label: "PEAK +75%" },
	axis: [
		{ x: 566, y: 7, text: "$0.000046" },
		{ x: 566, y: 59, text: "$0.000033" },
		{ x: 566, y: 103, text: "$0.000020" },
	],
	foot: [
		{ x: 2, y: 109, text: "0m" },
		{ x: 270, y: 109, text: "10m" },
		{ x: 530, y: 109, text: "20m" },
	],
};

const aerodromeChart: TileChart = {
	ariaLabel: "AERO price with entry and stop levels",
	gridY: [18, 52, 86],
	levels: [
		{ y: 50, label: "ENTRY $1.400", className: "level level-entry" },
		{ y: 96, label: "HARD SL −12% $1.232", className: "level level-stop" },
		{ y: 30, label: "ARM +25% (not covered)", className: "level level-arm" },
	],
	points:
		"0,52 30,56 60,54 90,61 120,58 150,64 180,62 210,68 240,72 270,70 300,75 330,73 360,78 390,76 420,80 450,77 480,82 510,79 540,84 560,83",
	dot: { x: 560, y: 83 },
	axis: [
		{ x: 566, y: 21, text: "$1.36" },
		{ x: 566, y: 55, text: "$1.30" },
		{ x: 566, y: 89, text: "$1.24" },
	],
	foot: [
		{ x: 2, y: 109, text: "0m" },
		{ x: 270, y: 109, text: "20m" },
		{ x: 525, y: 109, text: "41m" },
	],
};

const mochiChart: TileChart = {
	ariaLabel: "Entering position, no fills yet",
	gridY: [24, 56, 88],
	levels: [{ y: 56, label: "LIMIT $0.004218", className: "level level-entry" }],
	points: "",
	dot: { x: 560, y: 56 },
	axis: [],
	foot: [
		{ x: 2, y: 109, text: "0s" },
		{ x: 480, y: 109, text: "submitted 6s ago" },
	],
};

const scamcoinChart: TileChart = {
	ariaLabel: "Scamcoin collapsed chart with no sell route",
	gridY: [20, 56, 92],
	levels: [
		{ y: 22, label: "ENTRY $0.0000108", className: "level level-entry" },
		{ y: 100, label: "STOP COULD NOT EXECUTE", className: "level level-stop", labelClass: "level-label err" },
	],
	points:
		"0,24 20,24 40,26 60,30 80,32 100,28 120,34 140,40 160,44 180,50 200,54 220,58 240,64 260,70 280,72 300,78 320,80 340,84 360,88 380,90 400,92 420,94 440,95 460,96 480,97 500,98 520,99 540,99 560,100",
	dot: { x: 560, y: 100 },
	axis: [],
	foot: [
		{ x: 2, y: 109, text: "0m" },
		{ x: 270, y: 109, text: "42m" },
		{ x: 505, y: 109, text: "84m" },
	],
};

export const positionTiles: PositionTileView[] = [
	{
		id: "bonk",
		symbol: "BO",
		name: "Bonk",
		sub: "BONK // jupiter // Solana ↗",
		tone: "win",
		state: "RIDING",
		stateTone: "info",
		pnlPct: "+42.6%",
		pnlUsd: "+$38.12",
		pnlSol: "+0.258 SOL",
		pnlTone: "ok",
		price: "$0.00003142",
		mark: "live jupiter · 1s",
		markTone: "live",
		tags: [
			{ label: "PAPER" },
			{ label: "solana", tone: "ghost" },
			{ label: "2h old", tone: "ghost" },
			{ label: "held 19m", tone: "ghost" },
		],
		chart: bonkChart,
		market: [
			{ k: "MCap", v: "$2.41B", src: "live", srcTone: "live" },
			{ k: "Liq", v: "$4.20M", src: "live", srcTone: "live" },
			{ k: "Holders", v: "812.4K", src: "3s", srcTone: "live" },
			{ k: "1H", v: "+6.2%", src: "dexscreener", vTone: "ok" },
			{ k: "M5", v: "+1.8%", src: "dexscreener", vTone: "ok" },
			{ k: "BSR", v: "1.34", src: "1h buys/sells" },
		],
		money: [
			{ k: "In", v: "$89.48" },
			{ k: "Banked", v: "$46.20", tone: "ok" },
			{ k: "Realized", v: "+$18.90", tone: "ok" },
			{ k: "Unreal", v: "+$19.22", tone: "ok" },
		],
		ladder: {
			kind: "steps",
			state: "RIDING",
			stateTone: "info",
			bag: "69.5%",
			next: "NEXT +200% RUNG",
			steps: [
				{ label: "ARM +25", state: "on" },
				{ label: "BANK +70", state: "on" },
				{ label: "RUNG +200", state: "next" },
				{ label: "FINAL +500", state: "off" },
			],
		},
		prov: [
			{ label: "mark live · 1s", tone: "ghost" },
			{ label: "exec ✓", tone: "ok" },
			{ label: "gate ✓", tone: "ok" },
			{ label: "rule momentum_v1", tone: "ghost" },
			{ label: "net +0.258 SOL · fees 0.0009", className: "net ok" },
		],
		budget: { kind: "text", cap: "RIDING · NO MAX HOLD" },
		actions: [
			{ label: "Sell now", className: "btn danger sm", disabled: true },
			{ label: "⋯", className: "btn sm", disabled: true },
		],
		note: "First bank at +69.6% (30.5% of bag); trailing the remainder at 8% giveback.",
	},
	{
		id: "aerodrome",
		symbol: "AE",
		name: "Aerodrome",
		sub: "AERO // uniswap v3 // Base ↗",
		tone: "loss",
		state: "PRE-BANK",
		stateTone: "default",
		pnlPct: "−8.3%",
		pnlUsd: "−$6.12",
		pnlSol: "−0.041 SOL",
		pnlTone: "err",
		price: "$1.284",
		mark: "live uniswap · 3s",
		markTone: "live",
		tags: [
			{ label: "PAPER" },
			{ label: "base", tone: "ghost" },
			{ label: "41m old", tone: "ghost" },
			{ label: "held 41m", tone: "ghost" },
		],
		chart: aerodromeChart,
		market: [
			{ k: "MCap", v: "$1.18B", src: "live", srcTone: "live" },
			{ k: "Liq", v: "$38.4M", src: "live", srcTone: "live" },
			{ k: "Holders", v: "92.1K", src: "12s", srcTone: "live" },
			{ k: "1H", v: "−2.4%", src: "dexscreener", vTone: "err" },
			{ k: "M5", v: "−0.9%", src: "dexscreener", vTone: "err" },
			{ k: "BSR", v: "0.86", src: "1h buys/sells" },
		],
		money: [
			{ k: "In", v: "$74.02" },
			{ k: "Banked", v: "—" },
			{ k: "Realized", v: "—" },
			{ k: "Unreal", v: "−$6.12", tone: "err" },
		],
		ladder: {
			kind: "steps",
			state: "PRE-BANK",
			stateTone: "default",
			bag: "100%",
			next: "NEXT +25% ARM",
			steps: [
				{ label: "ARM +25", state: "next" },
				{ label: "BANK +125", state: "off" },
				{ label: "RUNG +200", state: "off" },
				{ label: "FINAL +500", state: "off" },
			],
		},
		prov: [
			{ label: "mark live · 3s", tone: "ghost" },
			{ label: "exec ✓", tone: "ok" },
			{ label: "gate ✓", tone: "ok" },
			{ label: "rule manual", tone: "ghost" },
			{ label: "net −0.041 SOL · fees 0.0006", className: "net err" },
		],
		budget: { kind: "bar", width: "17%", cap: "3h 19m LEFT / 4h 0m" },
		actions: [
			{ label: "Sell now", className: "btn danger sm", disabled: true },
			{ label: "⋯", className: "btn sm", disabled: true },
		],
		note: "Manual entry · hard stop −12% · no automation attached.",
	},
	{
		id: "mochi",
		symbol: "MO",
		name: "Mochi",
		sub: "MOCHI // jupiter // Solana ↗",
		tone: "flat",
		state: "ENTERING",
		stateTone: "info",
		pnlPct: "—",
		pnlUsd: "awaiting fill",
		pnlTone: "muted",
		price: "$0.004218",
		priceTone: "muted",
		mark: "entry fallback · 6s",
		markTone: "stale",
		tags: [
			{ label: "PAPER" },
			{ label: "ENTERING", tone: "info" },
			{ label: "solana", tone: "ghost" },
		],
		chart: mochiChart,
		market: [
			{ k: "MCap", v: "$4.1M", src: "live", srcTone: "live" },
			{ k: "Liq", v: "$612K", src: "live", srcTone: "live" },
			{ k: "Holders", v: "1.8K", src: "unknown age" },
			{ k: "1H", v: "+3.1%", src: "dexscreener", vTone: "ok" },
			{ k: "M5", v: "—", src: "no sample" },
			{ k: "BSR", v: "1.02", src: "1h buys/sells" },
		],
		money: [
			{ k: "In", v: "$25.00" },
			{ k: "Banked", v: "—" },
			{ k: "Realized", v: "—" },
			{ k: "Unreal", v: "—" },
		],
		ladder: {
			kind: "steps",
			state: "PRE-BANK",
			stateTone: "default",
			bag: "—",
			next: "no fills yet",
			steps: [
				{ label: "ARM +25", state: "off" },
				{ label: "BANK +125", state: "off" },
				{ label: "RUNG +200", state: "off" },
				{ label: "FINAL +500", state: "off" },
			],
		},
		prov: [
			{ label: "mark entry fallback · 6s", tone: "warn" },
			{ label: "quote ttl 12s", tone: "ghost" },
			{ label: "rule momentum_v1", tone: "ghost" },
			{ label: "—", className: "net" },
		],
		budget: { kind: "text", cap: "intent submitted · waiting for confirmation" },
		actions: [
			{ label: "Cancel intent", className: "btn sm", disabled: true },
			{ label: "⋯", className: "btn sm", disabled: true },
		],
		note: "Entering is a state, not a spinner. Show the intent, its ttl and the last known quote.",
	},
	{
		id: "scamcoin",
		symbol: "SC",
		name: "Scamcoin",
		sub: "SCAM // raydium // Solana ↗",
		tone: "error",
		state: "UNSELLABLE",
		stateTone: "err",
		pnlPct: "−96.2%",
		pnlUsd: "−$142.30",
		pnlSol: "−0.964 SOL",
		pnlTone: "err",
		price: "$0.00000041",
		priceTone: "muted",
		mark: "NO SELL ROUTE · last exact quote 2m",
		markTone: "dead",
		markToneClass: "err",
		tags: [
			{ label: "PAPER" },
			{ label: "UNSELLABLE", tone: "err" },
			{ label: "solana", tone: "ghost" },
		],
		chart: scamcoinChart,
		market: [
			{ k: "MCap", v: "$41.2K", src: "stale 2m", srcTone: "stale" },
			{ k: "Liq", v: "$212", src: "stale 2m", srcTone: "stale" },
			{ k: "Holders", v: "—", src: "unknown" },
			{ k: "1H", v: "−78.4%", src: "dexscreener", vTone: "err" },
			{ k: "M5", v: "−31.0%", src: "dexscreener", vTone: "err" },
			{ k: "BSR", v: "0.04", src: "1h buys/sells", vTone: "err" },
		],
		money: [
			{ k: "In", v: "$148.00" },
			{ k: "Banked", v: "—" },
			{ k: "Realized", v: "—" },
			{ k: "Unreal", v: "−$142.30", tone: "err" },
		],
		ladder: {
			kind: "note",
			state: "NO SELL ROUTE",
			stateTone: "err",
			lastExecutable: "$0.0041",
			note:
				"Sell is unavailable. No route returned a full-size quote. Nothing was sent. Last exact quote: 2m ago. The position is not flat.",
		},
		prov: [
			{ label: "mark no_sell_route · 2m", tone: "err" },
			{ label: "exec ✗", tone: "ghost" },
			{ label: "rule manual", tone: "ghost" },
			{ label: "unrealized −0.964 SOL", className: "net err" },
		],
		budget: { kind: "text", cap: "max hold exceeded · exit blocked" },
		actions: [
			{ label: "Retry sell", className: "btn danger sm", disabled: true },
			{ label: "SURE? WRITE OFF", className: "btn armed sm", disabled: true },
		],
		note: "Write-off is paper-only, two-step, and writes an explicit ledger reason. It is not a sell.",
	},
];

export const positionsSummary = {
	open: "4",
	openMeta: "3 strategies · 2 chains",
	executableValue: "$220.50",
	executableMeta: "at current marks · 1.49 SOL",
	unrealized: "−$129.20",
	unrealizedMeta: "−38.4% · after fees",
	exposure: "38%",
	exposureMeta: "of $3,400 risk cap",
	realizedToday: "+$12.10",
	realizedMeta: "6 closes · fees $2.84",
	needsAttention: "1",
	needsAttentionMeta: "no sell route",
};

export const closedPositions = [
	{
		closed: "12:04",
		symbol: "JUP",
		name: "Jupiter",
		chain: "Solana",
		strategy: "Momentum v1.2",
		inUsd: "$120.00",
		outUsd: "$138.40",
		pnl: "+$18.40",
		pnlPct: "+15.3%",
		pnlTone: "ok" as const,
		hold: "38m",
		exit: "TRAIL",
		exitTone: "ok" as const,
	},
	{
		closed: "11:41",
		symbol: "WIF",
		name: "dogwifhat",
		chain: "Solana",
		strategy: "Momentum v1.2",
		inUsd: "$80.00",
		outUsd: "$71.20",
		pnl: "−$8.80",
		pnlPct: "−11.0%",
		pnlTone: "err" as const,
		hold: "12m",
		exit: "STOP",
		exitTone: "err" as const,
	},
	{
		closed: "10:58",
		symbol: "TOS",
		name: "Toshi",
		chain: "Base",
		strategy: "DCA weekly",
		inUsd: "$50.00",
		outUsd: "$50.00",
		pnl: "—",
		pnlPct: "0.0%",
		pnlTone: "default" as const,
		hold: "4d",
		exit: "MANUAL",
		exitTone: "ghost" as const,
	},
];
