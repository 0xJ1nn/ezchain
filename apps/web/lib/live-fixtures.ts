// API-shaped fixtures for `EZCHAIN_WEB_MODE=fixture`.
//
// These mirror the `/api/v1` response models so the pages exercise the same code path in
// both modes. They are sample data and every source is named `fixture`: the UI labels
// them FIXTURE, never LIVE. Money is a decimal string like the API; the quote fixture is
// generated on the server side of the fixture boundary, not in the page.

import {
	ApiError,
	type Chain,
	type MarketList,
	type MarketOverview,
	type MarketQuery,
	type MarketRow,
	type PairInfo,
	type Quote,
	type QuoteQuery,
	type Source,
	type TokenSnapshot,
} from "./api";

const CHAIN_REGISTRY: Chain[] = [
	{
		id: "solana",
		name: "Solana",
		family: "svm",
		native_asset: "SOL",
		tier: "T1",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "jupiter",
			quote_providers: ["jupiter"],
			executable_quotes: true,
			candles: true,
		},
		venues: ["jupiter"],
		notes: "Jupiter Lite paper quotes.",
	},
	{
		id: "ethereum",
		name: "Ethereum",
		family: "evm",
		native_asset: "ETH",
		tier: "T2",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "paper_mark",
			quote_providers: ["paper_mark"],
			executable_quotes: false,
			candles: true,
		},
		venues: [],
		notes: "Paper marks only in P0.",
	},
	{
		id: "base",
		name: "Base",
		family: "evm",
		native_asset: "ETH",
		tier: "T2",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "paper_mark",
			quote_providers: ["paper_mark"],
			executable_quotes: false,
			candles: true,
		},
		venues: [],
		notes: "Paper marks only in P0.",
	},
	{
		id: "bsc",
		name: "BNB Chain",
		family: "evm",
		native_asset: "BNB",
		tier: "T2",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "paper_mark",
			quote_providers: ["paper_mark"],
			executable_quotes: false,
			candles: true,
		},
		venues: [],
		notes: "Paper marks only in P0.",
	},
	{
		id: "arbitrum",
		name: "Arbitrum One",
		family: "evm",
		native_asset: "ETH",
		tier: "T2",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "paper_mark",
			quote_providers: ["paper_mark"],
			executable_quotes: false,
			candles: true,
		},
		venues: [],
		notes: "Paper marks only in P0.",
	},
	{
		id: "polygon",
		name: "Polygon PoS",
		family: "evm",
		native_asset: "POL",
		tier: "T2",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "paper_mark",
			quote_providers: ["paper_mark"],
			executable_quotes: false,
			candles: true,
		},
		venues: [],
		notes: "Paper marks only in P0.",
	},
	{
		id: "optimism",
		name: "Optimism",
		family: "evm",
		native_asset: "ETH",
		tier: "T2",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "paper_mark",
			quote_providers: ["paper_mark"],
			executable_quotes: false,
			candles: true,
		},
		venues: [],
		notes: "Paper marks only in P0.",
	},
	{
		id: "avalanche",
		name: "Avalanche C",
		family: "evm",
		native_asset: "AVAX",
		tier: "T2",
		capabilities: {
			browse: true,
			paper_execute: true,
			live_execute: false,
			quote_kind: "paper_mark",
			quote_providers: ["paper_mark"],
			executable_quotes: false,
			candles: true,
		},
		venues: [],
		notes: "Paper marks only in P0.",
	},
];

const CHAIN_IDS = new Set(CHAIN_REGISTRY.map((chain) => chain.id));

interface Seed {
	chain: string;
	base_token: string;
	quote_token: string;
	pair_address: string;
	dex: string;
	symbol: string;
	name: string;
	price: string;
	liquidity: string;
	volume: string;
	m5: string | null;
	h1: string | null;
	h6: string | null;
	h24: string | null;
	created_minutes_ago: number | null;
	url: string | null;
}

const MINUTE = 60_000;

const SEEDS: Seed[] = [
	{
		chain: "solana",
		base_token: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		quote_token: "So11111111111111111111111111111111111111112",
		pair_address: "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj",
		dex: "raydium",
		symbol: "BONK",
		name: "Bonk",
		price: "0.00003142",
		liquidity: "4200000",
		volume: "412800000",
		m5: "0.8",
		h1: "6.2",
		h6: "1.4",
		h24: "-3.4",
		created_minutes_ago: 4 * 365 * 24 * 60,
		url: "https://dexscreener.com/solana/8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj",
	},
	{
		chain: "solana",
		base_token: "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
		quote_token: "So11111111111111111111111111111111111111112",
		pair_address: "2mTp7r1WkA4YhQ3YQ1q8mBoN5wJ8hU4zqY9cR7sV6nLd",
		dex: "raydium",
		symbol: "WIF",
		name: "dogwifhat",
		price: "1.842",
		liquidity: "21600000",
		volume: "188400000",
		m5: "-0.2",
		h1: "1.4",
		h6: "4.8",
		h24: "9.8",
		created_minutes_ago: 380 * 24 * 60,
		url: null,
	},
	{
		chain: "solana",
		base_token: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
		quote_token: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
		pair_address: "9kLm3vQa7pR4cT8nW2xY6dF1hJ5sB9uE3gK7mN4qP8z",
		dex: "orca",
		symbol: "JUP",
		name: "Jupiter",
		price: "0.8420",
		liquidity: "18200000",
		volume: "96200000",
		m5: "0.1",
		h1: "-0.8",
		h6: "1.2",
		h24: "2.1",
		created_minutes_ago: 300 * 24 * 60,
		url: null,
	},
	{
		chain: "solana",
		base_token: "MochiLpY8mNw1c7uZ4xQhR2sT6vK9pD3fG5jH8kL2nP",
		quote_token: "So11111111111111111111111111111111111111112",
		pair_address: "4xQd8vT2nWm5YhJ7kL1pR3sB6cF9uE2gK4mN8qP5zW",
		dex: "meteora",
		symbol: "MOCHI",
		name: "Mochi",
		price: "0.004218",
		liquidity: "612000",
		volume: "2800000",
		m5: "1.1",
		h1: "3.1",
		h6: "6.4",
		h24: "12.4",
		created_minutes_ago: 4,
		url: null,
	},
	{
		chain: "solana",
		base_token: "Scam1111111111111111111111111111111111111111",
		quote_token: "So11111111111111111111111111111111111111112",
		pair_address: "ScamPair1111111111111111111111111111111111",
		dex: "raydium",
		symbol: "SCAM",
		name: "Scamcoin",
		price: "0.00000041",
		liquidity: "212",
		volume: "84200",
		m5: "-12.0",
		h1: "-78.4",
		h6: "-90.1",
		h24: "-96.2",
		created_minutes_ago: 27,
		url: null,
	},
	{
		chain: "base",
		base_token: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
		quote_token: "0x4200000000000000000000000000000000000006",
		pair_address: "0x8f7a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a",
		dex: "aerodrome",
		symbol: "AERO",
		name: "Aerodrome",
		price: "1.284",
		liquidity: "38400000",
		volume: "74200000",
		m5: "-0.4",
		h1: "-2.4",
		h6: "-3.2",
		h24: "-6.1",
		created_minutes_ago: 210 * 24 * 60,
		url: null,
	},
	{
		chain: "base",
		base_token: "0xac1bd2486aaf3b5c0fc3fd868558b082a531b2b4",
		quote_token: "0x4200000000000000000000000000000000000006",
		pair_address: "0x1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d",
		dex: "uniswap",
		symbol: "TOSHI",
		name: "Toshi",
		price: "0.0001842",
		liquidity: "3400000",
		volume: "12600000",
		m5: "0.2",
		h1: "0.6",
		h6: "2.0",
		h24: "4.2",
		created_minutes_ago: 420 * 24 * 60,
		url: null,
	},
	{
		chain: "base",
		base_token: "0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
		quote_token: "0x4200000000000000000000000000000000000006",
		pair_address: "0x9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
		dex: "aerodrome",
		symbol: "DEGEN",
		name: "Degen",
		price: "0.007412",
		liquidity: "9800000",
		volume: "48200000",
		m5: "0.5",
		h1: "-1.2",
		h6: "5.1",
		h24: "18.6",
		created_minutes_ago: 300 * 24 * 60,
		url: null,
	},
	{
		chain: "base",
		base_token: "0x532f27101965dd16442e59d40670faf5ebb142e4",
		quote_token: "0x4200000000000000000000000000000000000006",
		pair_address: "0x2c4e6a8b0d2f4a6c8e0b2d4f6a8c0e2b4d6f8a0c",
		dex: "uniswap",
		symbol: "BRETT",
		name: "Brett",
		price: "0.0842",
		liquidity: "14200000",
		volume: "62400000",
		m5: "0.3",
		h1: "2.8",
		h6: "3.6",
		h24: "7.4",
		created_minutes_ago: 200 * 24 * 60,
		url: null,
	},
	{
		chain: "ethereum",
		base_token: "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
		quote_token: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
		pair_address: "0xa1b2c3d4e5f60718293a4b5c6d7e8f901a2b3c4d",
		dex: "uniswap",
		symbol: "UNI",
		name: "Uniswap",
		price: "7.284",
		liquidity: "42800000",
		volume: "188200000",
		m5: "-0.1",
		h1: "-0.4",
		h6: "0.3",
		h24: "1.2",
		created_minutes_ago: 5 * 365 * 24 * 60,
		url: null,
	},
	{
		chain: "ethereum",
		base_token: "0x6982508145454ce325ddbe47a25d4ec3d2311933",
		quote_token: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
		pair_address: "0xf1e2d3c4b5a69788796a5b4c3d2e1f0091827364",
		dex: "uniswap",
		symbol: "PEPE",
		name: "Pepe",
		price: "0.00000942",
		liquidity: "28400000",
		volume: "214600000",
		m5: "1.2",
		h1: "4.2",
		h6: "-0.4",
		h24: "-2.8",
		created_minutes_ago: 2 * 365 * 24 * 60,
		url: null,
	},
	{
		chain: "bsc",
		base_token: "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",
		quote_token: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
		pair_address: "0x5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d6e7",
		dex: "pancakeswap",
		symbol: "CAKE",
		name: "PancakeSwap",
		price: "2.412",
		liquidity: "18600000",
		volume: "42800000",
		m5: "0.2",
		h1: "1.1",
		h6: "2.4",
		h24: "5.6",
		created_minutes_ago: 3 * 24 * 60,
		url: null,
	},
];

/** Extra pools shown on a token page; the market row only carries the best pair. */
const EXTRA_PAIRS: Record<string, PairInfo[]> = {
	"solana:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": [
		{
			pair_address: "9WzDXw2fLpQhR6sT1vK8mN3xY5cB4jD7fG2hJ9kL6nP",
			dex: "orca",
			quote_token: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
			price_usd: "0.00003139",
			liquidity_usd: "1180000",
			volume_24h_usd: "102200000",
			url: null,
		},
		{
			pair_address: "5Q544f9mTzR2sW7xK1vN8cB4jD6hF3gL9pQ2wE5rT8y",
			dex: "meteora",
			quote_token: "So11111111111111111111111111111111111111112",
			price_usd: "0.00003145",
			liquidity_usd: "610000",
			volume_24h_usd: "42200000",
			url: null,
		},
	],
};

function fixtureSource(now: number, ageSeconds: number): Source {
	return {
		name: "fixture",
		url: null,
		observed_at: new Date(now - ageSeconds * 1000).toISOString(),
	};
}

function fail(code: string, message: string, status = 502, retryable = false): never {
	throw new ApiError({ code, message, status, retryable });
}

function checkChain(chain: string) {
	if (chain !== "all" && !CHAIN_IDS.has(chain)) {
		fail("CHAIN_NOT_FOUND", `unknown chain ${chain}`, 404);
	}
}

function rowFromSeed(seed: Seed, source: Source): MarketRow {
	return {
		chain: seed.chain,
		pair_address: seed.pair_address,
		dex: seed.dex,
		base_token: seed.base_token,
		quote_token: seed.quote_token,
		symbol: seed.symbol,
		name: seed.name,
		price_usd: seed.price,
		liquidity_usd: seed.liquidity,
		volume_24h_usd: seed.volume,
		price_change_m5: seed.m5,
		price_change_h1: seed.h1,
		price_change_h6: seed.h6,
		price_change_h24: seed.h24,
		pair_created_at:
			seed.created_minutes_ago === null
				? null
				: new Date(Date.now() - seed.created_minutes_ago * MINUTE).toISOString(),
		url: seed.url,
		source,
	};
}

export function fixtureChains(): Chain[] {
	return CHAIN_REGISTRY.map((chain) => ({ ...chain, capabilities: { ...chain.capabilities } }));
}

/**
 * Fixture coverage built from the fixture registry and its sample rows. It exercises the
 * overview renderer without pretending a market scan ran; every source is `fixture` and
 * the page labels the result FIXTURE.
 */
export function fixtureMarketOverview(): MarketOverview {
	const now = Date.now();
	const source = fixtureSource(now, 4);
	const chains = CHAIN_REGISTRY.map((chain) => {
		const rows = SEEDS.filter((seed) => seed.chain === chain.id);
		return {
			id: chain.id,
			name: chain.name,
			family: chain.family,
			quote_kind: chain.capabilities.quote_kind,
			status: rows.length > 0 ? ("ok" as const) : ("no_data" as const),
			rows: rows.length,
			last_observed_at: rows.length > 0 ? source.observed_at : null,
			age_seconds: rows.length > 0 ? 4 : null,
			last_attempt_at: rows.length > 0 ? source.observed_at : null,
			last_error: null,
		};
	});
	return {
		as_of: source.observed_at,
		chains,
		totals: {
			chains: chains.length,
			covered: chains.filter((chain) => chain.status === "ok").length,
			stale: 0,
			no_data: chains.filter((chain) => chain.status === "no_data").length,
			error: 0,
			snapshots: chains.reduce((sum, chain) => sum + chain.rows, 0),
		},
	};
}

export function fixtureMarketList(query: MarketQuery = {}): MarketList {
	const chain = query.chain ?? "all";
	checkChain(chain);
	const sort = query.sort ?? "trending";
	const limit = Math.min(100, Math.max(1, query.limit ?? 50));
	const search = (query.q ?? "").trim().toLowerCase();

	const now = Date.now();
	const source = fixtureSource(now, 4);
	const rows = SEEDS.filter((seed) => chain === "all" || seed.chain === chain)
		.filter((seed) => {
			if (!search) return true;
			return (
				seed.symbol.toLowerCase().includes(search) ||
				seed.name.toLowerCase().includes(search) ||
				seed.base_token.toLowerCase().includes(search)
			);
		})
		.map((seed) => rowFromSeed(seed, source));

	const sorted = [...rows].sort((a, b) => {
		if (sort === "liquidity") {
			return Number(b.liquidity_usd) - Number(a.liquidity_usd);
		}
		if (sort === "new") {
			const aTime = a.pair_created_at ? Date.parse(a.pair_created_at) : 0;
			const bTime = b.pair_created_at ? Date.parse(b.pair_created_at) : 0;
			return bTime - aTime;
		}
		return Number(b.volume_24h_usd) - Number(a.volume_24h_usd);
	});

	return { rows: sorted.slice(0, limit), source, stale: false };
}

function findSeed(chain: string, address: string): Seed {
	const needle = address.toLowerCase();
	const seed = SEEDS.find(
		(candidate) =>
			candidate.chain === chain &&
			(candidate.base_token.toLowerCase() === needle ||
				candidate.symbol.toLowerCase() === needle ||
				candidate.pair_address.toLowerCase() === needle),
	);
	if (!seed) fail("NOT_FOUND", `no token ${chain}:${address}`, 404);
	return seed;
}

export function fixtureToken(chain: string, address: string): TokenSnapshot {
	checkChain(chain);
	const seed = findSeed(chain, address);
	const now = Date.now();
	const source = fixtureSource(now, 4);
	const row = rowFromSeed(seed, source);
	const extras = EXTRA_PAIRS[`${chain}:${seed.base_token}`] ?? [];
	const pairs: PairInfo[] = [
		{
			pair_address: row.pair_address,
			dex: row.dex,
			quote_token: row.quote_token,
			price_usd: row.price_usd,
			liquidity_usd: row.liquidity_usd,
			volume_24h_usd: row.volume_24h_usd,
			url: row.url,
		},
		...extras,
	];
	return {
		chain,
		address: seed.base_token,
		symbol: seed.symbol,
		name: seed.name,
		price_usd: seed.price,
		liquidity_usd: seed.liquidity,
		volume_24h_usd: seed.volume,
		price_change_m5: seed.m5,
		price_change_h1: seed.h1,
		price_change_h6: seed.h6,
		price_change_h24: seed.h24,
		pairs,
		safety: { status: "unknown", reason: "SAFETY_NOT_IMPLEMENTED" },
		source,
		stale: false,
	};
}

/** Trim fixture float output to a decimal string; display only, never page math. */
function decimal(value: number): string {
	if (!Number.isFinite(value)) return "0";
	return value
		.toFixed(8)
		.replace(/0+$/, "")
		.replace(/\.$/, "");
}

export function fixtureQuote(query: QuoteQuery): Quote {
	checkChain(query.chain);
	const snapshot = fixtureToken(query.chain, query.token);
	const amount = Number(query.amountUsd);
	if (!Number.isFinite(amount) || amount <= 0) {
		fail("INVALID_AMOUNT", "amount_usd must be greater than zero", 422);
	}
	// Parity with the quote service: SCAM has no exit, so neither side can quote.
	if (snapshot.symbol === "SCAM") {
		fail(
			"NO_SELL_ROUTE",
			query.side === "sell" ? "no full-size sell route" : "buy has no full-size sell route",
			502,
		);
	}
	const price = Number(snapshot.price_usd);
	if (!Number.isFinite(price) || price <= 0) {
		fail("NO_SELL_ROUTE", "no executable paper mark", 502);
	}
	const isSolana = query.chain === "solana";
	const now = Date.now();
	const sell = query.side === "sell";
	return {
		chain: query.chain,
		token_in: sell ? snapshot.address : "USDC",
		token_out: sell ? "USDC" : snapshot.address,
		side: sell ? "sell" : "buy",
		// A sell is sized from the USD notional at the observed mark.
		amount_in: decimal(sell ? amount / price : amount),
		amount_out: decimal(sell ? amount : amount / price),
		price: snapshot.price_usd ?? "0",
		price_impact_pct: null,
		fee_usd: "0",
		kind: isSolana ? "jupiter" : "paper_mark",
		route: isSolana ? "Jupiter v6 → Raydium" : null,
		source: fixtureSource(now, 1),
	};
}
