// Fixture movers for `EZCHAIN_WEB_MODE=fixture`.
//
// These are the answers the API would give, already ranked. The sample lists are
// written out in rank order on purpose: this module must not rank, filter or pick a
// row, because the ranking is the server's job. Every source is named `fixture`, so the
// page can label the table FIXTURE and never present it as a live feed.

import { ApiError } from "./api";
import type {
	SignalDirection,
	SignalExclusions,
	SignalMover,
	SignalMovers,
	SignalQuery,
	SignalWindow,
} from "./signals-api";

interface MoverSeed {
	chain: string;
	token_address: string;
	pair_address: string;
	dex: string;
	symbol: string;
	name: string;
	price_usd: string;
	liquidity_usd: string;
	volume_24h_usd: string;
}

const BONK: MoverSeed = {
	chain: "solana",
	token_address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
	pair_address: "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj",
	dex: "raydium",
	symbol: "BONK",
	name: "Bonk",
	price_usd: "0.00003142",
	liquidity_usd: "4200000",
	volume_24h_usd: "412800000",
};
const WIF: MoverSeed = {
	chain: "solana",
	token_address: "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
	pair_address: "2mTp7r1WkA4YhQ3YQ1q8mBoN5wJ8hU4zqY9cR7sV6nLd",
	dex: "raydium",
	symbol: "WIF",
	name: "dogwifhat",
	price_usd: "1.842",
	liquidity_usd: "21600000",
	volume_24h_usd: "188400000",
};
const JUP: MoverSeed = {
	chain: "solana",
	token_address: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
	pair_address: "9kLm3vQa7pR4cT8nW2xY6dF1hJ5sB9uE3gK7mN4qP8z",
	dex: "orca",
	symbol: "JUP",
	name: "Jupiter",
	price_usd: "0.8420",
	liquidity_usd: "18200000",
	volume_24h_usd: "96200000",
};
const MOCHI: MoverSeed = {
	chain: "solana",
	token_address: "MochiLpY8mNw1c7uZ4xQhR2sT6vK9pD3fG5jH8kL2nP",
	pair_address: "4xQd8vT2nWm5YhJ7kL1pR3sB6cF9uE2gK4mN8qP5zW",
	dex: "meteora",
	symbol: "MOCHI",
	name: "Mochi",
	price_usd: "0.004218",
	liquidity_usd: "612000",
	volume_24h_usd: "2800000",
};
const AERO: MoverSeed = {
	chain: "base",
	token_address: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
	pair_address: "0x8f7a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a",
	dex: "aerodrome",
	symbol: "AERO",
	name: "Aerodrome",
	price_usd: "1.284",
	liquidity_usd: "38400000",
	volume_24h_usd: "74200000",
};
const TOSHI: MoverSeed = {
	chain: "base",
	token_address: "0xac1bd2486aaf3b5c0fc3fd868558b082a531b2b4",
	pair_address: "0x1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d",
	dex: "uniswap",
	symbol: "TOSHI",
	name: "Toshi",
	price_usd: "0.0001842",
	liquidity_usd: "3400000",
	volume_24h_usd: "12600000",
};
const DEGEN: MoverSeed = {
	chain: "base",
	token_address: "0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
	pair_address: "0x9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
	dex: "aerodrome",
	symbol: "DEGEN",
	name: "Degen",
	price_usd: "0.007412",
	liquidity_usd: "9800000",
	volume_24h_usd: "48200000",
};
const BRETT: MoverSeed = {
	chain: "base",
	token_address: "0x532f27101965dd16442e59d40670faf5ebb142e4",
	pair_address: "0x2c4e6a8b0d2f4a6c8e0b2d4f6a8c0e2b4d6f8a0c",
	dex: "uniswap",
	symbol: "BRETT",
	name: "Brett",
	price_usd: "0.0842",
	liquidity_usd: "14200000",
	volume_24h_usd: "62400000",
};
const UNI: MoverSeed = {
	chain: "ethereum",
	token_address: "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
	pair_address: "0xa1b2c3d4e5f60718293a4b5c6d7e8f901a2b3c4d",
	dex: "uniswap",
	symbol: "UNI",
	name: "Uniswap",
	price_usd: "7.284",
	liquidity_usd: "42800000",
	volume_24h_usd: "188200000",
};
const PEPE: MoverSeed = {
	chain: "ethereum",
	token_address: "0x6982508145454ce325ddbe47a25d4ec3d2311933",
	pair_address: "0xf1e2d3c4b5a69788796a5b4c3d2e1f0091827364",
	dex: "uniswap",
	symbol: "PEPE",
	name: "Pepe",
	price_usd: "0.00000942",
	liquidity_usd: "28400000",
	volume_24h_usd: "214600000",
};
const CAKE: MoverSeed = {
	chain: "bsc",
	token_address: "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",
	pair_address: "0x5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d6e7",
	dex: "pancakeswap",
	symbol: "CAKE",
	name: "PancakeSwap",
	price_usd: "2.412",
	liquidity_usd: "18600000",
	volume_24h_usd: "42800000",
};

/** One ranked sample row: a seed and the change it was ranked on. */
interface RankedSeed {
	seed: MoverSeed;
	change_pct: string;
}

// The lists below are in rank order. The 1-hour gainers are BONK, PEPE, MOCHI, BRETT,
// WIF, CAKE and TOSHI; the 1-hour losers are AERO, DEGEN, JUP and UNI; the 24-hour
// gainers are DEGEN, MOCHI, WIF, BRETT, CAKE, TOSHI, JUP and UNI; the 24-hour losers
// are AERO, BONK and PEPE.
const RANKED: Record<string, RankedSeed[]> = {
	"h1:up": [
		{ seed: BONK, change_pct: "6.2" },
		{ seed: PEPE, change_pct: "4.2" },
		{ seed: MOCHI, change_pct: "3.1" },
		{ seed: BRETT, change_pct: "2.8" },
		{ seed: WIF, change_pct: "1.4" },
		{ seed: CAKE, change_pct: "1.1" },
		{ seed: TOSHI, change_pct: "0.6" },
	],
	"h1:down": [
		{ seed: AERO, change_pct: "-2.4" },
		{ seed: DEGEN, change_pct: "-1.2" },
		{ seed: JUP, change_pct: "-0.8" },
		{ seed: UNI, change_pct: "-0.4" },
	],
	"h24:up": [
		{ seed: DEGEN, change_pct: "18.6" },
		{ seed: MOCHI, change_pct: "12.4" },
		{ seed: WIF, change_pct: "9.8" },
		{ seed: BRETT, change_pct: "7.4" },
		{ seed: CAKE, change_pct: "5.6" },
		{ seed: TOSHI, change_pct: "4.2" },
		{ seed: JUP, change_pct: "2.1" },
		{ seed: UNI, change_pct: "1.2" },
	],
	"h24:down": [
		{ seed: AERO, change_pct: "-6.1" },
		{ seed: BONK, change_pct: "-3.4" },
		{ seed: PEPE, change_pct: "-2.8" },
	],
};

/**
 * The rows of the market read that never reached the table, per window and direction.
 *
 * Two sample rows carry no change at all, one sits below the liquidity floor and the
 * rest moved in the other direction. The counts match the sample the API would read.
 */
const EXCLUDED: Record<string, SignalExclusions> = {
	"h1:up": {
		unknown_token: 0,
		duplicate_token: 0,
		stale: 0,
		unknown_change: 2,
		unknown_liquidity: 0,
		below_liquidity_floor: 1,
		wrong_direction: 4,
	},
	"h1:down": {
		unknown_token: 0,
		duplicate_token: 0,
		stale: 0,
		unknown_change: 2,
		unknown_liquidity: 0,
		below_liquidity_floor: 1,
		wrong_direction: 7,
	},
	"h24:up": {
		unknown_token: 0,
		duplicate_token: 0,
		stale: 0,
		unknown_change: 2,
		unknown_liquidity: 0,
		below_liquidity_floor: 1,
		wrong_direction: 3,
	},
	"h24:down": {
		unknown_token: 0,
		duplicate_token: 0,
		stale: 0,
		unknown_change: 2,
		unknown_liquidity: 0,
		below_liquidity_floor: 1,
		wrong_direction: 8,
	},
};

const ROWS_READ = 14;
const OBSERVED_AGE_SECONDS = 4;
const CANDIDATE_ROWS = 200;
const MIN_LIQUIDITY_USD = "10000";
const MAX_AGE_SECONDS = 60;
const LIMIT = 10;

function fixtureSource(now: number) {
	return {
		name: "fixture",
		url: null,
		observed_at: new Date(now - OBSERVED_AGE_SECONDS * 1000).toISOString(),
	};
}

export function fixtureSignalMovers(query: SignalQuery = {}): SignalMovers {
	const window: SignalWindow = query.window ?? "h1";
	const direction: SignalDirection = query.direction ?? "up";
	const chain = query.chain ?? "all";
	const now = Date.now();

	if (query.fixtureState === "error") {
		throw new ApiError({
			code: "UPSTREAM_UNREACHABLE",
			message: "the signals read could not reach the market source",
			status: 502,
			retryable: true,
		});
	}

	const source = fixtureSource(now);
	const key = `${window}:${direction}`;
	const ranked = RANKED[key] ?? [];
	const excluded = EXCLUDED[key] ?? {
		unknown_token: 0,
		duplicate_token: 0,
		stale: 0,
		unknown_change: 2,
		unknown_liquidity: 0,
		below_liquidity_floor: 1,
		wrong_direction: 11,
	};
	const empty = query.fixtureState === "empty";
	const selected = empty ? [] : ranked;

	const rows: SignalMover[] = selected.map((entry, index) => ({
		rank: index + 1,
		chain: entry.seed.chain,
		token_address: entry.seed.token_address,
		pair_address: entry.seed.pair_address,
		dex: entry.seed.dex,
		symbol: entry.seed.symbol,
		name: entry.seed.name,
		price_usd: entry.seed.price_usd,
		liquidity_usd: entry.seed.liquidity_usd,
		volume_24h_usd: entry.seed.volume_24h_usd,
		change_pct: entry.change_pct,
		source,
		observed_at: source.observed_at,
		age_seconds: OBSERVED_AGE_SECONDS,
	}));

	return {
		window,
		direction,
		as_of: source.observed_at,
		rows,
		total: rows.length,
		limit: LIMIT,
		truncated: false,
		reason: rows.length === 0 ? "NO_QUALIFIED_MOVERS" : null,
		rows_read: ROWS_READ,
		exclusions: excluded,
		applied: {
			chain,
			window,
			direction,
			min_liquidity_usd: MIN_LIQUIDITY_USD,
			max_age_seconds: MAX_AGE_SECONDS,
			candidate_rows: CANDIDATE_ROWS,
			candidate_sort: window === "h24" ? "change_h24" : "trending",
			limit: LIMIT,
		},
		source,
		stale: false,
	};
}
