// Read-only market lookup for the browser-local watchlist.
//
// The watchlist itself never reaches the API: it is a localStorage key. This route takes
// the starred keys from the browser, asks the same markets read model for each token and
// reports a missing row instead of dropping it. It changes no server state.

import { NextResponse, type NextRequest } from "next/server";
import { asApiError, getMarkets, type MarketRow } from "@/lib/api";
import {
	WATCHLIST_MAX_ENTRIES,
	normalizeBaseToken,
	normalizeChain,
	watchlistKey,
} from "@/lib/watchlist";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CHAIN_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;
const TOKEN_RE = /^[A-Za-z0-9._-]{1,128}$/;

interface WatchlistRowResult {
	key: string;
	row: MarketRow;
}

interface WatchlistUnavailable {
	key: string;
	code: string;
	message: string;
}

type WatchlistLookup =
	| { row: WatchlistRowResult }
	| { unavailable: WatchlistUnavailable };

function parseKey(raw: string): { chain: string; baseToken: string } | null {
	const separator = raw.indexOf(":");
	if (separator <= 0 || separator === raw.length - 1) return null;
	const chain = raw.slice(0, separator);
	const baseToken = raw.slice(separator + 1);
	if (!CHAIN_RE.test(chain) || !TOKEN_RE.test(baseToken)) return null;
	return { chain: normalizeChain(chain), baseToken: normalizeBaseToken(baseToken) };
}

// A watchlist can hold many tokens; four concurrent reads keep a large list from
// flooding the markets feed while still finishing in a few round trips.
async function mapLimit<T, R>(
	items: T[],
	limit: number,
	run: (item: T) => Promise<R>,
): Promise<R[]> {
	const results = new Array<R>(items.length);
	let next = 0;
	async function worker() {
		while (next < items.length) {
			const index = next;
			next += 1;
			results[index] = await run(items[index]);
		}
	}
	await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
	return results;
}

export async function GET(request: NextRequest) {
	const requested = [...new Set(request.nextUrl.searchParams.getAll("key"))].slice(
		0,
		WATCHLIST_MAX_ENTRIES,
	);
	if (requested.length === 0) {
		return NextResponse.json({
			rows: [],
			unavailable: [],
			looked_up_at: new Date().toISOString(),
		});
	}

	const rows: WatchlistRowResult[] = [];
	const unavailable: WatchlistUnavailable[] = [];

	const lookups = await mapLimit(
		requested,
		4,
		async (raw): Promise<WatchlistLookup> => {
			const parsed = parseKey(raw);
			if (!parsed) {
				return {
					unavailable: {
						key: raw,
						code: "WATCHLIST_KEY_INVALID",
						message: "The stored watchlist key is not a chain and token pair.",
					},
				};
			}
			try {
				const list = await getMarkets({
					chain: parsed.chain,
					q: parsed.baseToken,
					sort: "trending",
					limit: 20,
				});
				const row = list.rows.find(
					(candidate) =>
						normalizeChain(candidate.chain) === parsed.chain &&
						normalizeBaseToken(candidate.base_token) === parsed.baseToken,
				);
				if (!row) {
					return {
						unavailable: {
							key: watchlistKey(parsed.chain, parsed.baseToken),
							code: "MARKET_ROW_MISSING",
							message: "The market feed returned no pair for this token.",
						},
					};
				}
				return { row: { key: watchlistKey(row.chain, row.base_token), row } };
			} catch (error) {
				const api = asApiError(error);
				return {
					unavailable: {
						key: watchlistKey(parsed.chain, parsed.baseToken),
						code: api.code,
						message: api.message,
					},
				};
			}
		},
	);

	for (const result of lookups) {
		if ("row" in result) rows.push(result.row);
		else unavailable.push(result.unavailable);
	}

	return NextResponse.json({
		rows,
		unavailable,
		looked_up_at: new Date().toISOString(),
	});
}
