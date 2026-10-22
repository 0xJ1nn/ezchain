// Dashboard fixtures. These stand in for the server view models until apps/api exists.
// The numbers are reconciled here once; the UI only formats them. No client-side PnL math.

export type Tone = "default" | "ok" | "err" | "warn" | "info" | "ghost";

const MINUS = "\u2212";

function usd(n: number) {
	return `${n < 0 ? MINUS : ""}$${Math.abs(n).toLocaleString("en-US", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	})}`;
}

function signedUsd(n: number) {
	return `${n >= 0 ? "+" : MINUS}$${Math.abs(n).toLocaleString("en-US", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	})}`;
}

function usd0(n: number) {
	return `${n < 0 ? MINUS : ""}$${Math.abs(n).toLocaleString("en-US", {
		maximumFractionDigits: 0,
	})}`;
}

function pct(n: number) {
	return `${n >= 0 ? "+" : MINUS}${Math.abs(n).toFixed(1)}%`;
}

export interface PositionFixture {
	id: string;
	symbol: string;
	name: string;
	chain: string;
	strategy: string;
	state: string;
	stateTone: Tone;
	valueUsd: number;
	pnlUsd: number | null;
	unrealizedUsd: number | null;
	pnlPct: number | null;
	next: string;
	markAge: string;
	markTone: "live" | "stale" | "dead";
	nextTone?: "default" | "err";
	valueLabel?: string;
}

const positionFixtures: PositionFixture[] = [
	{
		id: "bonk",
		symbol: "BO",
		name: "Bonk",
		chain: "Solana",
		strategy: "Momentum",
		state: "RIDING",
		stateTone: "info",
		valueUsd: 127.6,
		pnlUsd: 38.12,
		unrealizedUsd: 19.22,
		pnlPct: 42.6,
		next: "+200% rung",
		markAge: "1s",
		markTone: "live",
	},
	{
		id: "aerodrome",
		symbol: "AE",
		name: "Aerodrome",
		chain: "Base",
		strategy: "Manual",
		state: "PRE-BANK",
		stateTone: "default",
		valueUsd: 67.9,
		pnlUsd: -6.12,
		unrealizedUsd: -6.12,
		pnlPct: -8.3,
		next: "stop −12%",
		markAge: "3s",
		markTone: "live",
	},
	{
		id: "mochi",
		symbol: "MO",
		name: "Mochi",
		chain: "Solana",
		strategy: "Momentum",
		state: "ENTERING",
		stateTone: "info",
		valueUsd: 25.0,
		pnlUsd: null,
		unrealizedUsd: null,
		pnlPct: null,
		next: "fill pending",
		markAge: "6s",
		markTone: "stale",
	},
	{
		id: "scamcoin",
		symbol: "SC",
		name: "Scamcoin",
		chain: "Solana",
		strategy: "Manual",
		state: "UNSELLABLE",
		stateTone: "err",
		valueUsd: 0.0041,
		pnlUsd: -142.3,
		unrealizedUsd: -142.3,
		pnlPct: -96.2,
		next: "no route",
		markAge: "2m",
		markTone: "dead",
		nextTone: "err",
		valueLabel: "$0.0041*",
	},
];

export interface OpenPositionRow {
	id: string;
	symbol: string;
	name: string;
	chain: string;
	strategy: string;
	state: string;
	stateTone: Tone;
	pnlPct: string;
	pnlUsd: string;
	value: string;
	next: string;
	markAge: string;
	markTone: "live" | "stale" | "dead";
	valueTone?: "default" | "muted";
	nextTone?: "default" | "err";
}

export const openPositions: OpenPositionRow[] = positionFixtures.map((p) => ({
	id: p.id,
	symbol: p.symbol,
	name: p.name,
	chain: p.chain,
	strategy: p.strategy,
	state: p.state,
	stateTone: p.stateTone,
	pnlPct: p.pnlPct === null ? "—" : pct(p.pnlPct),
	pnlUsd: p.pnlUsd === null ? "" : signedUsd(p.pnlUsd),
	value: p.valueLabel ?? usd(p.valueUsd),
	next: p.next,
	markAge: p.markAge,
	markTone: p.markTone,
	nextTone: p.nextTone,
}));

export const totals = {
	equityUsd: 3482.16,
	realizedUsd: 126.4,
	feesUsd: 18.22,
	unrealizedUsd: positionFixtures.reduce((sum, p) => sum + (p.unrealizedUsd ?? 0), 0),
	openPositionValueUsd: positionFixtures.reduce((sum, p) => sum + p.valueUsd, 0),
	// Exposure includes wallet balances, not only open positions. The cap is a risk limit.
	exposureUsd: 1284,
	exposureCapUsd: 3400,
	singleTokenUsd: 127.6,
	singleTokenCapUsd: 3400 * 0.1,
};

export const equity = {
	value: usd(totals.equityUsd),
	delta: "+4.8%",
	sol: "23.59 SOL",
	realized: signedUsd(totals.realizedUsd),
	realizedMeta: `28 closes · fees ${usd(totals.feesUsd)}`,
	unrealized: signedUsd(totals.unrealizedUsd),
	unrealizedTone: totals.unrealizedUsd >= 0 ? ("ok" as const) : ("err" as const),
	unrealizedMeta: "4 open positions",
	drawdown: "−6.2%",
	drawdownMeta: "from peak $3,712 · cap −20%",
	exposure: `${Math.round((totals.exposureUsd / totals.exposureCapUsd) * 100)}%`,
	exposureMeta: `${usd0(totals.exposureUsd)} of ${usd0(totals.exposureCapUsd)} cap`,
	mode: "PAPER",
	modeMeta: "no live keys in use",
};

export interface AttributionRow {
	strategy: string;
	mode: string;
	trades: string;
	winRate: string;
	fees: string;
	netPnl: string;
	netTone: "default" | "ok" | "err";
	share: string;
}

const attributionFixtures = [
	{ strategy: "Momentum v1.2", mode: "PAPER", trades: "18", winRate: "44.4%", feesUsd: 11.4, netUsd: 142.1 },
	{ strategy: "DCA weekly", mode: "PAPER", trades: "6", winRate: "—", feesUsd: 3.12, netUsd: 12.8 },
	{ strategy: "Mean reversion v0.4", mode: "PAPER", trades: "9", winRate: "22.2%", feesUsd: 3.7, netUsd: -28.5 },
	{ strategy: "Manual", mode: "PAPER", trades: "3", winRate: "33.3%", feesUsd: 0, netUsd: 0 },
];

const attributionNet = attributionFixtures.reduce((sum, row) => sum + row.netUsd, 0);

export const attribution: AttributionRow[] = attributionFixtures.map((row) => ({
	strategy: row.strategy,
	mode: row.mode,
	trades: row.trades,
	winRate: row.winRate,
	fees: usd(row.feesUsd),
	netPnl: signedUsd(row.netUsd),
	netTone: row.netUsd > 0 ? "ok" : row.netUsd < 0 ? "err" : "default",
	share: `${((row.netUsd / attributionNet) * 100).toFixed(1)}%`,
}));

export const risk = {
	dailyLoss: "$42 / $250",
	dailyLossWidth: "17%",
	exposure: `${Math.round((totals.exposureUsd / totals.exposureCapUsd) * 100)}% / 60%`,
	exposureWidth: "63%",
	singleToken: `Bonk ${((totals.singleTokenUsd / totals.equityUsd) * 100).toFixed(1)}% / 10%`,
	singleTokenWidth: "37%",
};

export const automations = [
	{ status: "ON", tone: "ok" as Tone, title: "Trailing exit · all positions", sub: "last fired 2m ago · 12 fires today", action: "Edit", href: "/automations/builder" },
	{ status: "ON", tone: "ok" as Tone, title: "DCA · AERO weekly", sub: "next run Mon 09:00", action: "Edit", href: "/automations/builder" },
	{ status: "PAUSED", tone: "warn" as Tone, title: "Loss-streak guard", sub: "paused by owner 1h ago", action: "Resume", href: "/automations" },
];

export const signals = [
	{ tag: "FLOW", tone: "info" as Tone, title: "BONK buy pressure ↑", sub: "score 0.82 · 2m · → Momentum" },
	{ tag: "WALLET", tone: "warn" as Tone, title: "Smart wallet 7x…c1 entering MOCHI", sub: "score 0.71 · 6m · watched only" },
	{ tag: "PRICE", tone: "default" as Tone, title: "AERO broke 1h low", sub: "score 0.44 · 14m · → risk alert" },
];

export const chains = [
	{ initial: "S", name: "Solana", detail: "slot 312,884,102 · 14 ms", status: "ok", tone: "ok" as Tone },
	{ initial: "B", name: "Base", detail: "block 24.19M · 38 ms", status: "ok", tone: "ok" as Tone },
	{ initial: "E", name: "Ethereum", detail: "block 21.04M · 61 ms", status: "gas high", tone: "warn" as Tone },
];

export interface WalletRow {
	initial: string;
	name: string;
	chains: string;
	badge: string;
	badgeTone: Tone;
	value: string;
	sync: string;
}

export const wallets: WalletRow[] = [
	{ initial: "M", name: "Main (hot)", chains: "Solana · Base · $1,842.20", badge: "local vault", badgeTone: "default", value: "$1,842.20", sync: "fixture" },
	{ initial: "L", name: "Ledger (hardware)", chains: "Ethereum · Base · $1,402.10", badge: "hardware", badgeTone: "info", value: "$1,402.10", sync: "fixture" },
	{ initial: "W", name: "Cold watch", chains: "Bitcoin · watch-only", badge: "watch", badgeTone: "ghost", value: "$237.86", sync: "fixture" },
	{ initial: "B", name: "Binance", chains: "read-only API · USDT, BTC", badge: "exchange", badgeTone: "ghost", value: "$0.00", sync: "scope: read" },
];

export interface ActivityRow {
	action: string;
	actionTone: Tone;
	title: string;
	sub: string;
	amount: string;
	amountTone?: "default" | "ok";
}

export const activity: ActivityRow[] = [
	{ action: "BUY", actionTone: "ok", title: "25.00 USDC → 5,928.4 MOCHI", sub: "Momentum v1.2 · Solana · 6s ago", amount: "−$25.00" },
	{ action: "SELL", actionTone: "err", title: "0.5 BONK → 46.20 USDC", sub: "bank at +125% · 19m ago", amount: "+$46.20", amountTone: "ok" },
	{ action: "FEE", actionTone: "ghost", title: "Priority fee · Solana", sub: "intent 8f2a… · 19m ago", amount: "−$0.09" },
	{ action: "ADJ", actionTone: "warn", title: "Chain reconcile · Base", sub: "wallet Main · +0.0004 ETH dust", amount: "—" },
];
