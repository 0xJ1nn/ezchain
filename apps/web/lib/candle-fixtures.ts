// API-shaped closed OHLCV fixtures for `EZCHAIN_WEB_MODE=fixture`.
//
// There is no candle database offline, so fixture mode generates a deterministic, visibly
// labelled sample series around the fixture token's mark. It is sample data, not history:
// every source is `fixture`, the page renders FIXTURE, and a bar's volume is null (unknown)
// on some rows so the UI is exercised against a value it must not print as zero.
//
// The generated bars are always closed: the newest bar's close_time is at or before `now`.
// That mirrors the API contract; a forming bar is never handed to the chart.

import { ApiError, type Candle, type CandleFixtureState, type CandleSeries, type CandleTimeframe } from "./api";
import { fixtureChains, fixtureToken } from "./live-fixtures";

const TIMEFRAME_SECONDS: Record<CandleTimeframe, number> = {
	"1m": 60,
	"5m": 300,
	"15m": 900,
	"1h": 3600,
	"4h": 14_400,
	"1d": 86_400,
};

const STALE_OFFSET_SECONDS = 3 * 60 * 60;
const MAX_BARS = 180;

function hash(value: string): number {
	let state = 2166136261;
	for (let index = 0; index < value.length; index += 1) {
		state ^= value.charCodeAt(index);
		state = Math.imul(state, 16777619);
	}
	return state >>> 0;
}

/** Small deterministic PRNG; the same token and timeframe always produce the same bars. */
function random(seed: number): () => number {
	let state = seed || 1;
	return () => {
		state ^= state << 13;
		state ^= state >>> 17;
		state ^= state << 5;
		return (state >>> 0) / 4294967296;
	};
}

/** Decimal string with no exponent and no trailing zeros; never "0.0000000000". */
function decimal(value: number): string {
	if (!Number.isFinite(value) || value <= 0) return "0";
	const fixed = value >= 1 ? value.toFixed(8) : value.toFixed(12);
	const trimmed = fixed.replace(/0+$/, "").replace(/\.$/, "");
	return trimmed === "" ? "0" : trimmed;
}

function iso(seconds: number): string {
	return new Date(seconds * 1000).toISOString();
}

function emptySeries(
	chain: string,
	address: string,
	timeframe: CandleTimeframe,
	reason: string,
	nowSeconds: number,
): CandleSeries {
	return {
		chain,
		token_address: address,
		timeframe,
		pool_address: null,
		candles: [],
		source: { name: "fixture", url: null, observed_at: iso(nowSeconds) },
		stale: false,
		reason,
	};
}

function buildCandles(
	chain: string,
	address: string,
	timeframe: CandleTimeframe,
	target: number,
	limit: number,
	nowSeconds: number,
	stale: boolean,
): Candle[] {
	const seconds = TIMEFRAME_SECONDS[timeframe];
	const count = Math.min(MAX_BARS, Math.max(1, limit));
	const next = random(hash(`${chain}:${address}:${timeframe}`));
	const lastClosedStart = Math.floor(nowSeconds / seconds) * seconds - seconds;
	const start = lastClosedStart - (count - 1) * seconds;

	const closes: number[] = [];
	let price = target * (0.86 + next() * 0.28);
	for (let index = 0; index < count; index += 1) {
		const shock = (next() - 0.48) * 0.035;
		price = Math.max(price * (1 + shock), target * 0.35);
		closes.push(price);
	}
	const scale = target / closes[closes.length - 1];
	for (let index = 0; index < closes.length; index += 1) {
		closes[index] *= scale;
	}

	return closes.map((close, index) => {
		const previousClose = index === 0 ? close * (1 - (next() - 0.5) * 0.01) : closes[index - 1];
		const open = previousClose;
		const high = Math.max(open, close) * (1 + next() * 0.004);
		const low = Math.min(open, close) * (1 - next() * 0.004);
		// A stale series keeps the same shape but stops hours ago, so the UI can show a
		// stale badge without inventing a different chart.
		const barStart =
			start + index * seconds - (stale ? STALE_OFFSET_SECONDS : 0);
		return {
			open_time: iso(barStart),
			close_time: iso(barStart + seconds),
			timeframe_seconds: seconds,
			open: decimal(open),
			high: decimal(high),
			low: decimal(low),
			close: decimal(close),
			volume: index % 6 === 5 ? null : decimal(5_000 + next() * 900_000),
		};
	});
}

/**
 * Deterministic fixture candles. A token missing from the fixture registry is
 * `CANDLES_NOT_FOUND`, not a flat line. SCAM has no routable pool, like the quote fixture.
 */
export function fixtureCandles(
	chain: string,
	address: string,
	timeframe: CandleTimeframe,
	limit: number,
	state?: CandleFixtureState,
): CandleSeries {
	const nowSeconds = Math.floor(Date.now() / 1000);
	if (!fixtureChains().some((candidate) => candidate.id === chain)) {
		throw new ApiError({
			code: "CANDLES_UNSUPPORTED_CHAIN",
			message: `unknown chain ${chain}`,
			status: 404,
		});
	}
	if (state === "unsupported") {
		throw new ApiError({
			code: "CANDLES_UNSUPPORTED_CHAIN",
			message: `CANDLES_UNSUPPORTED_CHAIN: ${chain} has no candle history provider`,
			status: 404,
		});
	}
	if (state === "error") {
		throw new ApiError({
			code: "UPSTREAM_UNREACHABLE",
			message: "fixture: the candle provider did not answer",
			status: 502,
			retryable: true,
		});
	}
	if (state === "no_pool") {
		return emptySeries(chain, address, timeframe, "NO_POOL", nowSeconds);
	}
	if (state === "empty") {
		return emptySeries(chain, address, timeframe, "CANDLES_EMPTY", nowSeconds);
	}

	let token;
	try {
		token = fixtureToken(chain, address);
	} catch {
		throw new ApiError({
			code: "CANDLES_NOT_FOUND",
			message: `no candle history for ${chain}:${address}`,
			status: 404,
		});
	}
	if (token.symbol === "SCAM") {
		return emptySeries(chain, address, timeframe, "NO_POOL", nowSeconds);
	}

	const reference = Number(token.price_usd);
	const target = Number.isFinite(reference) && reference > 0 ? reference : 1;
	const stale = state === "stale";
	const candles = buildCandles(chain, address, timeframe, target, limit, nowSeconds, stale);
	return {
		chain,
		token_address: address,
		timeframe,
		pool_address: token.pairs[0]?.pair_address ?? null,
		candles,
		source: {
			name: "fixture",
			url: null,
			observed_at: iso(stale ? nowSeconds - STALE_OFFSET_SECONDS : nowSeconds),
		},
		stale,
		reason: null,
	};
}
