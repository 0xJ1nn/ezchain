// Display helpers for candle results. The API owns the series; these functions only pick
// words and tones. An empty series or a failed request is a reason, never a flat line.

import type {
	ApiError,
	CandleFixtureState,
	CandleSeries,
	CandleTimeframe,
} from "./api";

export const TIMEFRAMES: { value: CandleTimeframe; label: string }[] = [
	{ value: "1m", label: "1m" },
	{ value: "5m", label: "5m" },
	{ value: "15m", label: "15m" },
	{ value: "1h", label: "1h" },
	{ value: "4h", label: "4h" },
	{ value: "1d", label: "1d" },
];

const TIMEFRAME_VALUES = new Set(TIMEFRAMES.map((entry) => entry.value));

export function parseCandleTimeframe(value: string | string[] | undefined): CandleTimeframe {
	const raw = Array.isArray(value) ? value[0] : value;
	return raw && TIMEFRAME_VALUES.has(raw as CandleTimeframe)
		? (raw as CandleTimeframe)
		: "1h";
}

const FIXTURE_STATES = new Set<CandleFixtureState>([
	"unsupported",
	"no_pool",
	"empty",
	"error",
	"stale",
]);

/** Fixture-only QA state from `?candles=`; live mode never reads it. */
export function parseCandleFixtureState(
	value: string | string[] | undefined,
): CandleFixtureState | undefined {
	const raw = Array.isArray(value) ? value[0] : value;
	return raw && FIXTURE_STATES.has(raw as CandleFixtureState)
		? (raw as CandleFixtureState)
		: undefined;
}

export interface CandleLoadError {
	code: string;
	message: string;
	retryable?: boolean;
}

export type CandleLoad =
	| { ok: true; series: CandleSeries }
	| { ok: false; error: CandleLoadError };

export function candleErrorFrom(error: unknown): CandleLoadError {
	const apiError = error as Partial<ApiError> | null;
	return {
		code: typeof apiError?.code === "string" ? apiError.code : "CANDLES_UNAVAILABLE",
		message:
			typeof apiError?.message === "string"
				? apiError.message
				: error instanceof Error
					? error.message
					: "the candle request failed",
		retryable: apiError?.retryable === true,
	};
}

export interface CandleCopy {
	title: string;
	body: string;
}

export function candleErrorCopy(error: CandleLoadError): CandleCopy {
	switch (error.code) {
		case "CANDLES_UNSUPPORTED_CHAIN":
			return {
				title: "No candle provider for this chain",
				body: "The chain registry does not advertise closed OHLCV history, so no chart is drawn. This is a capability gap, not a flat price.",
			};
		case "CANDLES_NOT_FOUND":
			return {
				title: "No candle history for this token",
				body: "The API has no series for this token and timeframe. No line is fabricated to fill the gap.",
			};
		case "UPSTREAM_UNREACHABLE":
			return {
				title: "Candle provider unavailable",
				body: "The bars could not be obtained. Retry later; an old series is not substituted and no chart is fabricated.",
			};
		case "CHAIN_NOT_FOUND":
			return {
				title: "Unknown chain",
				body: "The API does not know this chain, so it cannot serve candle history.",
			};
		default:
			return {
				title: "Candles unavailable",
				body: error.message,
			};
	}
}

export function candleReasonCopy(reason: string | null | undefined): CandleCopy | null {
	if (!reason) return null;
	switch (reason) {
		case "NO_POOL":
			return {
				title: "No pool found",
				body: "The API could not find a dex pool for this token, so there is no price history to draw and no chart is fabricated. Unknown is not zero.",
			};
		case "CANDLES_EMPTY":
			return {
				title: "No closed bars",
				body: "The provider returned no closed bars for this timeframe. An empty series is shown as a reason, not a flat line.",
			};
		default:
			return {
				title: reason,
				body: "The API returned no series with this reason. No chart is fabricated.",
			};
	}
}

export function lastClose(series: CandleSeries): string | null {
	return series.candles.at(-1)?.close ?? null;
}
