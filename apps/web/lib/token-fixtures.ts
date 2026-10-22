// Token page fixtures. These stand in for the server token view model
// (/tokens/{chain}/{mint} per docs/WIREFRAME.md §5) until apps/api exists.
// Every displayed string is reconciled here once; the page only renders it.
// No client-side PnL, quote or accounting math. Unknown values are "—", never zero.

import type { Tone } from "./dashboard-fixtures";

export interface TokenBadge {
	label: string;
	tone?: Tone;
}

export interface TokenChartMarker {
	cx: number;
	cy: number;
	r?: number;
	label: string;
	labelX: number;
	labelY: number;
}

export interface TokenChartLevel {
	y: number;
	label: string;
	className: string;
	labelClass?: string;
}

export interface TokenChartSpec {
	ariaTitle: string;
	ariaDesc: string;
	gridY: number[];
	axisLabels?: { x: number; y: number; text: string }[];
	levels: TokenChartLevel[];
	points: string;
	peak?: TokenChartMarker;
	entry?: TokenChartMarker;
	crosshair?: number;
	last: TokenChartMarker;
	foot: { x: number; y: number; text: string }[];
}

export interface SafetyRow {
	check: string;
	result: string;
	resultTone: Tone;
	detail: string;
	detailMono?: boolean;
	source: string;
}

export interface HolderRow {
	rank: string;
	address: string;
	label: string;
	supply: string;
	balance: string;
	type: string;
	typeTone: Tone;
}

export interface PoolRow {
	pool: string;
	venue: string;
	tvl: string;
	vol: string;
	fee: string;
	sell1: string;
	sell10: string;
}

export interface TradeRow {
	time: string;
	side: "buy" | "sell";
	price: string;
	size: string;
	venue: string;
	wallet: string;
	you?: boolean;
}

export interface DetailRow {
	k: string;
	v: string;
	sub?: string;
	badge?: string;
	badgeTone?: Tone;
	live?: boolean;
	dead?: boolean;
	plain?: boolean;
	err?: boolean;
}

export interface MyPositionCell {
	k: string;
	v?: string;
	vTone?: "ok" | "err";
	badge?: string;
	badgeTone?: Tone;
	sub?: string;
}

export interface NotePart {
	text: string;
	mono?: boolean;
	href?: string;
}

export interface TokenTab {
	id: string;
	label: string;
	count?: string;
	on?: boolean;
}

export interface TokenPageView {
	chain: string;
	chainLabel: string;
	id: string;
	name: string;
	symbol: string;
	avatar: string;
	sub: string;
	mark: string;
	markMuted?: boolean;
	deltas: { text: string; tone: "up" | "down" }[];
	execBadge: { label: string; tone: Tone };
	execNote?: string;
	badges: TokenBadge[];
	solscan: { url: string; label: string } | null;
	chartSub: string;
	chart: TokenChartSpec;
	chartLegend: { label: string; className?: string; borderColor?: string }[];
	lastTradeCaption: string;
	tabs: TokenTab[];
	safety: { checkedAt: string; badge: string; badgeTone: Tone; rows: SafetyRow[]; foot: string };
	holders: {
		sub: string;
		badge: string;
		badgeTone: Tone;
		rows: HolderRow[];
		foot: { left: string; right: string };
	};
	liquidity: { sub: string; rows: PoolRow[]; foot: string };
	myPosition: {
		sub: string;
		detailHref: string;
		cells: MyPositionCell[];
		note: string;
	};
	recentTrades: { rows: TradeRow[] };
	quote: {
		ttl: string;
		side: "buy" | "sell";
		// Fail-closed confirm gate: the page renders disabled={!canConfirm} and shows
		// blockedReason as visible text (aria-describedby), never title-only.
		canConfirm: boolean;
		blockedReason: string;
		payWith?: { amount: string; hint: string };
		rows: DetailRow[];
		riskBadge: string;
		riskBadgeTone: Tone;
		riskRows: DetailRow[];
		note: { tone: "warn" | "err"; parts: NotePart[] };
		confirmLabel: string;
		limitsHref: string;
		foot: string;
	};
	facts: { rows: DetailRow[]; foot: string };
}

const bonkChart: TokenChartSpec = {
	ariaTitle: "BONK intraday price with entry, stop, trail and bank levels",
	ariaDesc:
		"BONK executable mid $0.00003142, up 6.2% on the hour. Entry $0.00002400 at 10:42, " +
		"stop $0.00001560, trail $0.00003096, bank L1 $0.00004070 filled at 10:53:44. " +
		"Peak +72.0% at 10:53; last trade $0.00003138.",
	gridY: [56, 142, 228],
	axisLabels: [
		{ x: 848, y: 59, text: "0.000040" },
		{ x: 848, y: 145, text: "0.000030" },
		{ x: 848, y: 231, text: "0.000020" },
	],
	levels: [
		{ y: 194, label: "ENTRY $0.00002400 · pos_7f3a91", className: "level level-entry" },
		{ y: 266, label: "STOP $0.00001560", className: "level level-stop", labelClass: "level-label err" },
		{ y: 134, label: "TRAIL $0.00003096", className: "level level-trail" },
		{ y: 50, label: "BANK L1 $0.00004070 · filled 10:53:44", className: "level" },
	],
	points:
		"0,200 40,195 80,203 120,198 160,206 200,196 240,200 280,190 320,194 360,186 400,190 " +
		"440,182 480,188 520,180 540,178 560,186 567,194 580,186 600,176 620,166 640,152 660,140 " +
		"680,126 700,104 715,72 729,45 745,62 760,80 780,100 800,116 820,126 840,130",
	peak: { cx: 729, cy: 45, r: 3, label: "PEAK +72.0%", labelX: 640, labelY: 36 },
	entry: { cx: 567, cy: 194, r: 2.5, label: "ENTRY 10:42", labelX: 470, labelY: 212 },
	crosshair: 840,
	last: { cx: 840, cy: 130, r: 3.5, label: "$0.00003142", labelX: 748, labelY: 152 },
	foot: [
		{ x: 2, y: 294, text: "10:00" },
		{ x: 270, y: 294, text: "10:20" },
		{ x: 540, y: 294, text: "10:40" },
		{ x: 790, y: 294, text: "11:01" },
	],
};

const scamChart: TokenChartSpec = {
	ariaTitle: "Scamcoin collapse with no sell route",
	ariaDesc:
		"Scamcoin last trade $0.00000041. Entry $0.00001080; the stop could not execute because " +
		"no route returned a full-size quote. Last exact quote $0.0041, 2m old. The position is not flat.",
	gridY: [56, 142, 228],
	// SCAM variant drops the numeric axis: the last mark sits on the floor of the plot and a
	// "0.0000000" label next to it would read as a below-zero price, which is impossible.
	// numeric labels are given in the panel sub ("last trade · not executable") and the legend.
	axisLabels: [],
	levels: [
		{ y: 64, label: "ENTRY $0.00001080", className: "level level-entry" },
		{ y: 266, label: "STOP · COULD NOT EXECUTE", className: "level level-stop", labelClass: "level-label err" },
	],
	points:
		"0,70 40,72 80,68 120,74 160,78 200,82 240,90 280,102 320,120 360,150 400,190 440,226 " +
		"480,252 520,266 560,272 600,276 640,278 680,279 720,280 760,280 800,281 840,281",
	last: { cx: 840, cy: 280, r: 3.5, label: "$0.00000041", labelX: 742, labelY: 258 },
	foot: [
		{ x: 2, y: 294, text: "10:20" },
		{ x: 270, y: 294, text: "10:40" },
		{ x: 540, y: 294, text: "10:59" },
		{ x: 790, y: 294, text: "11:00" },
	],
};

const bonkSafetyRows: SafetyRow[] = [
	{ check: "Mint authority", result: "revoked", resultTone: "ok", detail: "—", detailMono: true, source: "RPC · 4s" },
	{ check: "Freeze authority", result: "revoked", resultTone: "ok", detail: "—", detailMono: true, source: "RPC · 4s" },
	{ check: "Top-10 concentration", result: "18.0%", resultTone: "ok", detail: "threshold 25% · pools included", source: "Helius · 2m" },
	{ check: "Honeypot simulation", result: "pass", resultTone: "ok", detail: "sell 1 SOL simulated, 0 errors", source: "sim · 11:00" },
	{ check: "Transfer tax", result: "0.0%", resultTone: "ok", detail: "buy and sell", source: "sim · 11:00" },
	{ check: "LP custody", result: "burned / locked", resultTone: "ok", detail: "3 pools · 100%", source: "indexer · 6m" },
	{ check: "RugCheck score", result: "12 / 100", resultTone: "ok", detail: "low risk · no flags", source: "rugcheck · 3m" },
];

const scamSafetyRows: SafetyRow[] = [
	{ check: "Mint authority", result: "revoked", resultTone: "ok", detail: "—", detailMono: true, source: "RPC · 3m" },
	{
		check: "Freeze authority",
		result: "unknown",
		resultTone: "warn",
		detail: "query timed out · not assumed safe",
		source: "RPC · 3m",
	},
	{ check: "Top-10 concentration", result: "86.4%", resultTone: "err", detail: "threshold 25% · pools included", source: "Helius · 4m" },
	{ check: "Honeypot simulation", result: "fail", resultTone: "err", detail: "sell 1 SOL simulated, 2 errors", source: "sim · 10:58" },
	{
		check: "Transfer tax",
		result: "unknown",
		resultTone: "warn",
		detail: "could not complete the sell simulation",
		source: "sim · 10:58",
	},
	{ check: "LP custody", result: "unlocked", resultTone: "err", detail: "1 pool · 0% burned", source: "indexer · 6m" },
	{ check: "RugCheck score", result: "98 / 100", resultTone: "err", detail: "high risk · 4 flags", source: "rugcheck · 5m" },
];

const bonkHolders: HolderRow[] = [
	{ rank: "1", address: "9WzDXw…2fLp", label: "Raydium AMM", supply: "6.42%", balance: "4.92T", type: "pool", typeTone: "ghost" },
	{ rank: "2", address: "JUP6Lk…3vQa", label: "Jupiter vault", supply: "2.18%", balance: "1.67T", type: "router", typeTone: "ghost" },
	{ rank: "3", address: "5Q544f…9mTz", label: "Binance hot", supply: "1.94%", balance: "1.49T", type: "exchange", typeTone: "ghost" },
	{ rank: "4", address: "8LmT3k…4xQd", label: "Treasury (locked)", supply: "1.51%", balance: "1.16T", type: "locked 2028", typeTone: "info" },
	{ rank: "5", address: "whirLb…4Kd2", label: "Orca whirlpool", supply: "1.42%", balance: "1.09T", type: "pool", typeTone: "ghost" },
	{ rank: "6", address: "FWznbc…8pYe", label: "Kraken", supply: "1.21%", balance: "0.93T", type: "exchange", typeTone: "ghost" },
	{ rank: "7", address: "H8sMJn…1cVr", label: "Coinbase", supply: "1.08%", balance: "0.83T", type: "exchange", typeTone: "ghost" },
	{ rank: "8", address: "pAMMBa…7tKq", label: "PumpSwap pool", supply: "0.91%", balance: "0.70T", type: "pool", typeTone: "ghost" },
	{ rank: "9", address: "2xT9vd…5nWm", label: "Wintermute", supply: "0.72%", balance: "0.55T", type: "market maker", typeTone: "warn" },
	{ rank: "10", address: "Bybit1…7hQz", label: "Bybit", supply: "0.61%", balance: "0.47T", type: "exchange", typeTone: "ghost" },
];

const scamHolders: HolderRow[] = [
	{ rank: "1", address: "7xQp…9dEf", label: "—", supply: "38.6%", balance: "3.86B", type: "unlabelled", typeTone: "ghost" },
	{ rank: "2", address: "4fGh…2kJm", label: "—", supply: "22.1%", balance: "2.21B", type: "unlabelled", typeTone: "ghost" },
	{ rank: "3", address: "pAMMBa…7tKq", label: "Raydium pool", supply: "18.4%", balance: "1.84B", type: "pool", typeTone: "ghost" },
];

const bonkPools: PoolRow[] = [
	{ pool: "BONK / SOL", venue: "Raydium AMM", tvl: "$2.41M", vol: "$268.4M", fee: "0.25%", sell1: "−0.3%", sell10: "−2.9%" },
	{ pool: "BONK / USDC", venue: "Orca Whirlpool", tvl: "$1.18M", vol: "$102.2M", fee: "0.30%", sell1: "−0.4%", sell10: "−3.6%" },
	{ pool: "BONK / SOL", venue: "Meteora DLMM", tvl: "$0.61M", vol: "$42.2M", fee: "0.20%", sell1: "−0.6%", sell10: "−6.1%" },
];

const scamPools: PoolRow[] = [
	{ pool: "SCAM / SOL", venue: "Raydium AMM", tvl: "$212", vol: "$1.4K", fee: "0.25%", sell1: "—", sell10: "—" },
];

const bonkTrades: TradeRow[] = [
	{ time: "11:01:02", side: "buy", price: "0.00003138", size: "$1,842", venue: "Raydium", wallet: "3xQ…8f" },
	{ time: "10:59:48", side: "sell", price: "0.00003122", size: "$612", venue: "Jupiter", wallet: "9kL…2m" },
	{ time: "10:58:31", side: "sell", price: "0.00003104", size: "$3,280", venue: "Orca", wallet: "7pR…4c" },
	{ time: "10:56:12", side: "buy", price: "0.00003128", size: "$884", venue: "Raydium", wallet: "2mT…9v" },
	{ time: "10:53:44", side: "sell", price: "0.00004070", size: "$46.20", venue: "Jupiter", wallet: "Main", you: true },
	{ time: "10:42:08", side: "buy", price: "0.00002400", size: "$89.48", venue: "Jupiter", wallet: "Main", you: true },
];

const scamTrades: TradeRow[] = [
	{ time: "10:59:58", side: "sell", price: "0.00000042", size: "$312", venue: "Raydium", wallet: "8kQ…3d" },
	{ time: "10:58:12", side: "sell", price: "0.00000044", size: "$1,204", venue: "Raydium", wallet: "p2m…7w" },
	{ time: "10:57:40", side: "sell", price: "0.00000051", size: "$96", venue: "Raydium", wallet: "5vT…8n" },
	{ time: "10:55:02", side: "buy", price: "0.00000048", size: "$54", venue: "Raydium", wallet: "9dL…1x" },
	{ time: "10:52:31", side: "sell", price: "0.00000055", size: "$2,410", venue: "Raydium", wallet: "3fR…9k" },
	{ time: "10:41:26", side: "buy", price: "0.00001080", size: "$148.00", venue: "Raydium", wallet: "Main", you: true },
];

// Tab counts derive from the table rows below; the page never hardcodes them.
const bonkTabs: TokenTab[] = [
	{ id: "chart", label: "Chart", on: true },
	{ id: "safety", label: "Safety", count: String(bonkSafetyRows.length) },
	{ id: "holders", label: "Holders", count: String(bonkHolders.length) },
	{ id: "liquidity", label: "Liquidity", count: String(bonkPools.length) },
	{ id: "mine", label: "My position" },
];

const scamTabs: TokenTab[] = [
	{ id: "chart", label: "Chart", on: true },
	{ id: "safety", label: "Safety", count: String(scamSafetyRows.length) },
	{ id: "holders", label: "Holders", count: String(scamHolders.length) },
	{ id: "liquidity", label: "Liquidity", count: String(scamPools.length) },
	{ id: "mine", label: "My position" },
];

const bonk: TokenPageView = {
	chain: "solana",
	chainLabel: "Solana",
	id: "bonk",
	name: "Bonk",
	symbol: "BONK",
	avatar: "BO",
	sub: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 · Solana T1 · Jupiter, Raydium, PumpSwap",
	mark: "$0.00003142",
	deltas: [
		{ text: "+6.2% 1h", tone: "up" },
		{ text: "−3.4% 24h", tone: "down" },
	],
	execBadge: { label: "buy + sell", tone: "ok" },
	badges: [
		{ label: "safety pass", tone: "ok" },
		{ label: "mint revoked", tone: "ok" },
		{ label: "freeze revoked", tone: "ok" },
		{ label: "top10 18.0%" },
		{ label: "honeypot pass", tone: "ok" },
		{ label: "rugcheck 12/100" },
		{ label: "liq $4.20M", tone: "ghost" },
		{ label: "holders 812.4K", tone: "ghost" },
	],
	solscan: {
		url: "https://solscan.io/token/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		label: "solscan.io/token/DezX…B263",
	},
	chartSub: "executable mid · venue quotes, not indexer candles",
	chart: bonkChart,
	chartLegend: [
		{ label: "Entry", className: "l-entry" },
		{ label: "Stop", className: "l-stop" },
		{ label: "Trail", className: "l-trail" },
		{ label: "Bank L1", borderColor: "var(--line-2)" },
	],
	lastTradeCaption: "last trade $0.00003138 · 11:01:02 · spread 0.4bp",
	tabs: bonkTabs,
	safety: {
		checkedAt: "11:00:12",
		badge: "pass",
		badgeTone: "ok",
		rows: bonkSafetyRows,
		foot: "Safety is evidence, not a guarantee. A pass still requires an executable sell quote at the intended size.",
	},
	holders: {
		sub: "top 10 = 18.0% of supply",
		badge: "snapshot 2m",
		badgeTone: "ghost",
		rows: bonkHolders,
		foot: {
			left: "Your wallets: 2,591,333 BONK · 0.0000034% of supply · Main (hot)",
			right: "Unknown labels stay unlabelled; no heuristics are presented as facts.",
		},
	},
	liquidity: {
		sub: "$4.20M across 3 pools · depth measured with real quotes",
		rows: bonkPools,
		foot: "Impact is real quote output at size, not a constant-product estimate. A 50 SOL sell currently returns −14.8%.",
	},
	myPosition: {
		sub: "pos_7f3a91 · Momentum v1.2",
		detailHref: "/positions#bonk",
		cells: [
			{ k: "PnL", v: "+42.6%", vTone: "ok", sub: "+$38.12" },
			{ k: "Equity", v: "$127.60", sub: "banked $46.20" },
			{ k: "Qty", v: "2,591,333", sub: "of 3,728,333" },
			{ k: "State", badge: "RIDING", badgeTone: "info", sub: "next: RUNG +200%" },
		],
		note:
			"the quote panel below proposes a new position in the same token. Adding here would " +
			"raise combined BONK exposure to $164.20 (4.7% of equity).",
	},
	recentTrades: { rows: bonkTrades },
	quote: {
		ttl: "ttl 12s",
		side: "buy",
		canConfirm: false,
		blockedReason: "Execution is not wired in M1",
		payWith: {
			amount: "0.30",
			hint: "≈ $44.29 at SOL/USD $147.62 · wallet Main (hot)",
		},
		rows: [
			{ k: "Route", v: "Jupiter v6 → Raydium CLMM", plain: true },
			{ k: "Expected out", v: "1,403,571 BONK" },
			{ k: "Minimum out", v: "1,389,535", sub: "1.00% slip" },
			{ k: "Price impact", v: "0.42%" },
			{ k: "Venue fee", v: "0.0009 SOL", sub: "$0.13" },
			{ k: "Account rent", v: "0.00204 SOL", sub: "$0.30 · refundable" },
			{ k: "Quote source · age", v: "jupiter · 1s · fixture", plain: true },
		],
		riskBadge: "cap reduces size",
		riskBadgeTone: "warn",
		riskRows: [
			{ k: "Size after caps", v: "$36.60", sub: "from $44.29" },
			{ k: "Daily spend after", v: "$250.00 / $250.00", badge: "at cap", badgeTone: "warn" },
			{ k: "Position after fill", v: "$164.20 · 4.7% equity" },
			{ k: "Worst case (stop −35%)", v: "−$12.81", err: true },
			{
				k: "Full-size sell check",
				v: "OK · 1 SOL sell quote −0.3% (covers the 0.248 SOL clip) · Raydium · fixture",
			},
		],
		note: {
			tone: "warn",
			parts: [
				{ text: "Daily cap remaining is " },
				{ text: "$36.60", mono: true },
				{ text: " of " },
				{ text: "$250.00", mono: true },
				{ text: ". Confirm clips this order to the cap (" },
				{ text: "$36.60", mono: true },
				{
					text:
						") and re-quotes at that size; the quote above is for the requested 0.30 SOL. " +
						"Override requires approval on ",
				},
				{ text: "Risk", href: "/risk" },
				{ text: "." },
			],
		},
		confirmLabel: "Review & confirm",
		limitsHref: "/risk",
		foot:
			"Nothing is signed on this screen. Confirm shows the same quote, its TTL and the worst " +
			"case; an expired quote is re-quoted, never reused.",
	},
	facts: {
		rows: [
			{ k: "Market cap", v: "$2.41B" },
			{ k: "FDV", v: "$3.14B" },
			{ k: "Liquidity", v: "$4.20M" },
			{ k: "24h volume", v: "$412.8M" },
			{ k: "Holders", v: "812.4K" },
			{ k: "Decimals", v: "5" },
			{ k: "Chain tier", v: "Solana · T1", plain: true },
			{ k: "Executable", v: "", badge: "buy + sell", badgeTone: "ok" },
		],
		foot: "Prices from venue quotes; supply and holders from the chain. Fixture snapshot; source/age fields land with the API.",
	},
};

const scam: TokenPageView = {
	chain: "solana",
	chainLabel: "Solana",
	id: "scam",
	name: "Scamcoin",
	symbol: "SCAM",
	avatar: "SC",
	sub: "mint not verified · Solana T1 · Raydium",
	mark: "$0.00000041",
	markMuted: true,
	deltas: [
		{ text: "−78.4% 1h", tone: "down" },
		{ text: "−96.2% 24h", tone: "down" },
	],
	execBadge: { label: "NO SELL ROUTE", tone: "err" },
	execNote: "last exact quote $0.0041 · 2m",
	badges: [
		{ label: "safety fail", tone: "err" },
		{ label: "mint revoked", tone: "ok" },
		{ label: "freeze unknown", tone: "warn" },
		{ label: "top10 86.4%", tone: "err" },
		{ label: "honeypot fail", tone: "err" },
		{ label: "rugcheck 98/100", tone: "err" },
		{ label: "liq $212 · stale 2m", tone: "ghost" },
		{ label: "holders —", tone: "ghost" },
	],
	solscan: null,
	chartSub: "last trade · not executable",
	chart: scamChart,
	chartLegend: [
		{ label: "Entry", className: "l-entry" },
		{ label: "Stop (could not execute)", className: "l-stop" },
	],
	lastTradeCaption: "last trade $0.00000041 · 10:59:58 · spread unknown",
	tabs: scamTabs,
	safety: {
		checkedAt: "10:58:07",
		badge: "fail",
		badgeTone: "err",
		rows: scamSafetyRows,
		foot:
			"Safety is evidence, not a guarantee. A fail blocks new intents for this token; it does " +
			"not force an exit. Unknown checks stay unknown.",
	},
	holders: {
		sub: "holder count unknown · stale snapshot · 2m",
		badge: "stale snapshot · 2m",
		badgeTone: "warn",
		rows: scamHolders,
		foot: {
			left: "Your wallets: 13.7M SCAM · Main (hot)",
			right: "Stale snapshot: unverified labels are —, not guesses.",
		},
	},
	liquidity: {
		sub: "$212 across 1 pool · depth unknown",
		rows: scamPools,
		foot:
			"No full-size sell quote has returned since 2m ago. Depth is unknown, not zero; the " +
			"full-size sell check cannot pass.",
	},
	myPosition: {
		sub: "Manual · paper",
		detailHref: "/positions#scamcoin",
		cells: [
			{ k: "PnL", v: "−96.2%", vTone: "err", sub: "at mark · not executable · −$142.30" },
			{ k: "Value", v: "$0.0041", sub: "executable · last exact quote · 2m" },
			{ k: "In", v: "$148.00", sub: "no banked" },
			{ k: "State", badge: "UNSELLABLE", badgeTone: "err", sub: "no sell route" },
		],
		note:
			"this position cannot be exited at size. The last full-size sell quote failed 2m ago " +
			"($0.0041); the current mark is not executable. At the executable basis the position " +
			"is −100.0% (−$148.00). Nothing was sent and the position is not flat.",
	},
	recentTrades: { rows: scamTrades },
	quote: {
		ttl: "no quote",
		side: "sell",
		canConfirm: false,
		blockedReason: "No sell route — nothing can be sent",
		rows: [
			{ k: "Route", v: "no route", sub: "last full-size quote failed 2m ago", err: true, plain: true },
			{ k: "Expected out", v: "—" },
			{ k: "Minimum out", v: "—" },
			{ k: "Price impact", v: "—" },
			{ k: "Venue fee", v: "—" },
			{ k: "Account rent", v: "—" },
			{ k: "Quote source · age", v: "none · last exact quote 2m", dead: true, plain: true },
		],
		riskBadge: "no route",
		riskBadgeTone: "err",
		riskRows: [
			{ k: "Size after caps", v: "—" },
			{ k: "Daily spend after", v: "—" },
			{ k: "Position after fill", v: "—" },
			{ k: "Worst case (stop −35%)", v: "—" },
			{ k: "Full-size sell check", v: "FAIL · no route", err: true },
		],
		note: {
			tone: "err",
			parts: [
				{ text: "No sell route at full size. Nothing would be sent; the position is not flat. " },
				{ text: "Last exact quote " },
				{ text: "$0.0041", mono: true },
				{ text: " · 2m ago. New buys for this token are blocked while safety checks fail." },
			],
		},
		confirmLabel: "No route · nothing to confirm",
		limitsHref: "/risk",
		foot:
			"Nothing is signed on this screen. With no route there is nothing to confirm; the page " +
			"keeps the last exact quote instead of inventing a new one.",
	},
	facts: {
		rows: [
			{ k: "Market cap", v: "$41.2K", sub: "stale · 2m" },
			{ k: "FDV", v: "—" },
			{ k: "Liquidity", v: "$212", sub: "stale · 2m" },
			{ k: "24h volume", v: "—" },
			{ k: "Holders", v: "—" },
			{ k: "Decimals", v: "—" },
			{ k: "Chain tier", v: "Solana · T1", plain: true },
			{ k: "Executable", v: "", badge: "no sell route", badgeTone: "err" },
		],
		foot: "Prices from venue quotes; supply and holders from the chain. Unknown is —, never zero.",
	},
};

export const tokenPages: Record<string, TokenPageView> = {
	"solana/bonk": bonk,
	"solana/scam": scam,
};
