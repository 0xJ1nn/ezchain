// Markets fixtures. These stand in for the `/markets` server view model until apps/api
// exists. Every money, size and percentage value is already the display string the table
// renders; the page formats nothing and computes nothing.

import type { Tone } from "./dashboard-fixtures";
import { pct, usd } from "./format";

export type ChangeTone = "ok" | "err";
export type SparkTone = "ink" | "err";

export interface MarketMove {
	value: string;
	tone: ChangeTone;
}

export type MarketPosition =
	| {
			kind: "open";
			href: string;
			badge: string;
			badgeTone: Tone;
			value?: string;
			valueTitle?: string;
			valueTone?: "ok" | "err" | "muted";
	  }
	| { kind: "closed"; value: string }
	| { kind: "none" };

export interface MarketRow {
	id: string;
	// Only tokens with a built route may link. Every other token cell stays plain text.
	tokenHref?: string;
	symbol: string;
	symbolTone?: "err";
	avatar: string;
	name: string;
	sub: string;
	chain: string;
	chainInitial: string;
	price: string;
	change1h: MarketMove;
	change24h: MarketMove;
	mcap: string;
	liq: string;
	liqTone?: "warn" | "err";
	vol: string;
	holders: string;
	safety: string;
	safetyTone: Tone;
	spark: number[];
	sparkTone?: SparkTone;
	sparkLabel: string;
	position: MarketPosition;
}

function move(n: number): MarketMove {
	return { value: pct(n), tone: n < 0 ? "err" : "ok" };
}

function sparkLabel(symbol: string, values: number[]) {
	const first = values[0];
	const last = values[values.length - 1];
	const direction = last > first ? "up" : last < first ? "down" : "flat";
	return `${symbol} 7-day trend, ${direction}`;
}

interface MarketSeed extends Omit<MarketRow, "change1h" | "change24h" | "sparkLabel"> {
	change1h: number;
	change24h: number;
}

const seeds: MarketSeed[] = [
	{
		id: "bonk",
		tokenHref: "/tokens/solana/bonk",
		symbol: "BONK",
		avatar: "BO",
		name: "Bonk",
		sub: "DezXAZ…W1u",
		chain: "Solana",
		chainInitial: "S",
		price: "0.00003142",
		change1h: 6.2,
		change24h: -3.4,
		mcap: "$2.41B",
		liq: "$4.20M",
		vol: "$412.8M",
		holders: "812.4K",
		safety: "pass",
		safetyTone: "ok",
		spark: [18, 17, 19, 20, 18, 21, 22, 20, 23, 22, 24, 23],
		position: {
			kind: "open",
			href: "/positions/bonk",
			badge: "RIDING",
			badgeTone: "info",
			value: pct(42.6),
			valueTone: "ok",
		},
	},
	{
		id: "wif",
		symbol: "WIF",
		avatar: "WI",
		name: "dogwifhat",
		sub: "dogwifhat · EKpQ…TD",
		chain: "Solana",
		chainInitial: "S",
		price: "1.842",
		change1h: 1.4,
		change24h: 9.8,
		mcap: "$1.84B",
		liq: "$21.6M",
		vol: "$188.4M",
		holders: "241.2K",
		safety: "pass",
		safetyTone: "ok",
		spark: [20, 19, 21, 20, 22, 24, 23, 25, 24, 26, 25, 27],
		position: { kind: "closed", value: pct(-11.0) },
	},
	{
		id: "jup",
		symbol: "JUP",
		avatar: "JU",
		name: "Jupiter",
		sub: "JUPyiw…k1",
		chain: "Solana",
		chainInitial: "S",
		price: "0.8420",
		change1h: -0.8,
		change24h: 2.1,
		mcap: "$1.26B",
		liq: "$18.2M",
		vol: "$96.2M",
		holders: "412.8K",
		safety: "pass",
		safetyTone: "ok",
		spark: [22, 23, 21, 22, 20, 21, 19, 20, 18, 19, 18, 17],
		position: { kind: "closed", value: pct(15.3) },
	},
	{
		id: "mochi",
		symbol: "MOCHI",
		avatar: "MO",
		name: "Mochi",
		sub: "Mochi…4xQ",
		chain: "Solana",
		chainInitial: "S",
		price: "0.004218",
		change1h: 3.1,
		change24h: 12.4,
		mcap: "$4.1M",
		liq: "$612K",
		liqTone: "warn",
		vol: "$2.8M",
		holders: "1.8K",
		safety: "thin liq",
		safetyTone: "warn",
		spark: [10, 12, 11, 14, 16, 15, 18, 17, 19, 21, 20, 22],
		position: {
			kind: "open",
			href: "/positions/mochi",
			badge: "ENTERING",
			badgeTone: "info",
			value: `${usd(25)} in`,
			valueTitle: "cost basis · awaiting fill",
			valueTone: "muted",
		},
	},
	{
		id: "scam",
		tokenHref: "/tokens/solana/scam",
		symbol: "SCAM",
		symbolTone: "err",
		avatar: "SC",
		name: "Scamcoin",
		sub: "Scam…9zz",
		chain: "Solana",
		chainInitial: "S",
		price: "0.00000041",
		change1h: -78.4,
		change24h: -96.2,
		mcap: "$41.2K",
		liq: "$212",
		liqTone: "err",
		vol: "$84.2K",
		holders: "—",
		safety: "fail · honeypot",
		safetyTone: "err",
		spark: [30, 28, 26, 20, 14, 9, 6, 4, 3, 2, 2, 1],
		sparkTone: "err",
		position: {
			kind: "open",
			href: "/positions/scamcoin",
			badge: "UNSELLABLE",
			badgeTone: "err",
			value: pct(-96.2),
			valueTone: "err",
		},
	},
	{
		id: "aerodrome",
		symbol: "AERO",
		avatar: "AE",
		name: "Aerodrome",
		sub: "Aerodrome · 0x940…2b",
		chain: "Base",
		chainInitial: "B",
		price: "1.284",
		change1h: -2.4,
		change24h: -6.1,
		mcap: "$1.18B",
		liq: "$38.4M",
		vol: "$74.2M",
		holders: "92.1K",
		safety: "pass",
		safetyTone: "ok",
		spark: [24, 23, 24, 22, 21, 22, 20, 19, 20, 18, 19, 18],
		position: {
			kind: "open",
			href: "/positions/aerodrome",
			badge: "PRE-BANK",
			badgeTone: "default",
			value: pct(-8.3),
			valueTone: "err",
		},
	},
	{
		id: "toshi",
		symbol: "TOSHI",
		avatar: "TO",
		name: "Toshi",
		sub: "Toshi · 0xac1…7f",
		chain: "Base",
		chainInitial: "B",
		price: "0.0001842",
		change1h: 0.6,
		change24h: 4.2,
		mcap: "$77.2M",
		liq: "$3.4M",
		vol: "$12.6M",
		holders: "61.4K",
		safety: "pass",
		safetyTone: "ok",
		spark: [14, 15, 14, 16, 17, 16, 18, 17, 19, 18, 19, 20],
		position: { kind: "closed", value: "0.0%" },
	},
	{
		id: "degen",
		symbol: "DEGEN",
		avatar: "DE",
		name: "Degen",
		sub: "Degen · 0x4ed…9c",
		chain: "Base",
		chainInitial: "B",
		price: "0.007412",
		change1h: -1.2,
		change24h: 18.6,
		mcap: "$274M",
		liq: "$9.8M",
		vol: "$48.2M",
		holders: "188.2K",
		safety: "pass",
		safetyTone: "ok",
		spark: [12, 13, 12, 14, 13, 16, 18, 17, 19, 21, 20, 22],
		position: { kind: "none" },
	},
	{
		id: "brett",
		symbol: "BRETT",
		avatar: "BR",
		name: "Brett",
		sub: "Brett · 0x532…f3",
		chain: "Base",
		chainInitial: "B",
		price: "0.0842",
		change1h: 2.8,
		change24h: 7.4,
		mcap: "$842M",
		liq: "$14.2M",
		vol: "$62.4M",
		holders: "142.6K",
		safety: "pass",
		safetyTone: "ok",
		spark: [16, 17, 16, 18, 19, 18, 20, 21, 20, 22, 23, 22],
		position: { kind: "none" },
	},
	{
		id: "uni",
		symbol: "UNI",
		avatar: "UN",
		name: "Uniswap",
		sub: "Uniswap · 0x1f9…ad",
		chain: "Ethereum",
		chainInitial: "E",
		price: "7.284",
		change1h: -0.4,
		change24h: 1.2,
		mcap: "$4.38B",
		liq: "$42.8M",
		vol: "$188.2M",
		holders: "982.4K",
		safety: "pass",
		safetyTone: "ok",
		spark: [21, 22, 21, 22, 23, 22, 23, 24, 23, 24, 25, 24],
		position: { kind: "none" },
	},
	{
		id: "pepe",
		symbol: "PEPE",
		avatar: "PE",
		name: "Pepe",
		sub: "Pepe · 0x698…76",
		chain: "Ethereum",
		chainInitial: "E",
		price: "0.00000942",
		change1h: 4.2,
		change24h: -2.8,
		mcap: "$3.96B",
		liq: "$28.4M",
		vol: "$214.6M",
		holders: "412.8K",
		safety: "pass",
		safetyTone: "ok",
		spark: [19, 20, 18, 19, 21, 20, 18, 19, 17, 18, 18, 19],
		position: { kind: "none" },
	},
	{
		id: "cake",
		symbol: "CAKE",
		avatar: "CA",
		name: "PancakeSwap",
		sub: "PancakeSwap · 0x0e0…b4",
		chain: "BNB",
		chainInitial: "N",
		price: "2.412",
		change1h: 1.1,
		change24h: 5.6,
		mcap: "$742M",
		liq: "$18.6M",
		vol: "$42.8M",
		holders: "1.42M",
		safety: "pass",
		safetyTone: "ok",
		spark: [17, 18, 17, 18, 19, 18, 19, 20, 19, 21, 20, 21],
		position: { kind: "none" },
	},
];

export const marketRows: MarketRow[] = seeds.map((row) => ({
	...row,
	change1h: move(row.change1h),
	change24h: move(row.change24h),
	sparkLabel: sparkLabel(row.symbol, row.spark),
}));

const chains = ["Solana", "Base", "Ethereum", "BNB"] as const;

export interface ChainTab {
	id: string;
	label: string;
	count: number;
	active: boolean;
}

export const chainTabs: ChainTab[] = [
	{ id: "all", label: "All chains", count: marketRows.length, active: true },
	...chains.map((chain) => ({
		id: chain.toLowerCase(),
		label: chain,
		count: marketRows.filter((row) => row.chain === chain).length,
		active: false,
	})),
];

export type NewPairCall =
	| { kind: "blocked"; label: string }
	| { kind: "watch"; label: string }
	| { kind: "quote" };

export interface NewPairRow {
	age: string;
	symbol: string;
	sub: string;
	chain: string;
	liq: string;
	mcap: string;
	mcapTitle?: string;
	gate: string;
	gateTone: Tone;
	call: NewPairCall;
}

export const newPairs: NewPairRow[] = [
	{
		age: "4m",
		symbol: "NOVA",
		sub: "Nova…7r2",
		chain: "Solana",
		liq: "$48.2K",
		mcap: "$312K",
		gate: "mint auth active",
		gateTone: "warn",
		call: { kind: "blocked", label: "blocked by rule" },
	},
	{
		age: "12m",
		symbol: "GRIZ",
		sub: "Griz…2p8",
		chain: "Base",
		liq: "$186K",
		mcap: "$1.8M",
		gate: "pass",
		gateTone: "ok",
		call: { kind: "quote" },
	},
	{
		age: "27m",
		symbol: "PUMP2",
		sub: "Pump…9kd",
		chain: "Solana",
		liq: "$92.4K",
		mcap: "$640K",
		gate: "LP unlocked",
		gateTone: "err",
		call: { kind: "watch", label: "watch only" },
	},
	{
		age: "38m",
		symbol: "MOCHI",
		sub: "Mochi…4xQ",
		chain: "Solana",
		liq: "$612K",
		mcap: "$4.1M",
		gate: "thin liq",
		gateTone: "warn",
		call: { kind: "quote" },
	},
	{
		age: "51m",
		symbol: "BASED",
		sub: "Based…8j4",
		chain: "Base",
		liq: "$1.24M",
		mcap: "—",
		mcapTitle: "mcap unknown in fixture",
		gate: "pass",
		gateTone: "ok",
		call: { kind: "quote" },
	},
];
