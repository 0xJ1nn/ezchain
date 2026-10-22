// Sample execution records for `EZCHAIN_WEB_MODE=fixture`.
//
// These mirror the `/api/v1/paper/orders` and `/fills` response models so the activity,
// performance and report screens exercise the same rendering path with and without an
// API. Every page that reads them prints FIXTURE. They are not accounting truth and the
// reconciliation report is deliberately *not* fixtured: that endpoint proves the database
// and only a live API can answer it.

import type { PaperFillRecord, PaperOrderRecord } from "./paper-api";

function minutesAgo(minutes: number) {
	return new Date(Date.now() - minutes * 60_000).toISOString();
}

export const fixturePaperOrders: PaperOrderRecord[] = [
	{
		id: "paper-ord-1041",
		chain: "solana",
		token_address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		side: "buy",
		amount_usd: "43.62",
		status: "filled",
		reason: null,
		quote_kind: "jupiter",
		created_at: minutesAgo(38),
		filled_at: minutesAgo(38),
	},
	{
		id: "paper-ord-1038",
		chain: "solana",
		token_address: "So11111111111111111111111111111111111111112",
		side: "buy",
		amount_usd: "50.00",
		status: "filled",
		reason: null,
		quote_kind: "jupiter",
		created_at: minutesAgo(1_450),
		filled_at: minutesAgo(1_450),
	},
	{
		id: "paper-ord-1031",
		chain: "base",
		token_address: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
		side: "buy",
		amount_usd: "74.03",
		status: "filled",
		reason: null,
		quote_kind: "paper_mark",
		created_at: minutesAgo(2_880),
		filled_at: minutesAgo(2_880),
	},
	{
		id: "paper-ord-1024",
		chain: "solana",
		token_address: "Scam1111111111111111111111111111111111111111",
		side: "sell",
		amount_usd: "148.00",
		status: "rejected",
		reason: "NO_SELL_ROUTE",
		quote_kind: "paper_mark",
		created_at: minutesAgo(4_310),
		filled_at: null,
	},
	{
		id: "paper-ord-1019",
		chain: "solana",
		token_address: "7atgF8KQo4wJrD5ATGX7t1V2zVvykPJbFfNeVf1icFv1",
		side: "buy",
		amount_usd: "25.00",
		status: "filled",
		reason: null,
		quote_kind: "paper_mark",
		created_at: minutesAgo(5_760),
		filled_at: minutesAgo(5_760),
	},
];

export const fixturePaperFills: PaperFillRecord[] = [
	{
		id: "paper-fill-1041",
		order_id: "paper-ord-1041",
		chain: "solana",
		token_address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		side: "buy",
		quantity: "2000000",
		price_usd: "0.00002181",
		notional_usd: "43.62",
		fee_usd: "0.13",
		quote_kind: "jupiter",
		created_at: minutesAgo(38),
	},
	{
		id: "paper-fill-1038",
		order_id: "paper-ord-1038",
		chain: "solana",
		token_address: "So11111111111111111111111111111111111111112",
		side: "buy",
		quantity: "0.3448",
		price_usd: "144.98",
		notional_usd: "50.00",
		fee_usd: "0.15",
		quote_kind: "jupiter",
		created_at: minutesAgo(1_450),
	},
	{
		id: "paper-fill-1031",
		order_id: "paper-ord-1031",
		chain: "base",
		token_address: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
		side: "buy",
		quantity: "52.88",
		price_usd: "1.4000",
		notional_usd: "74.03",
		fee_usd: "0.22",
		quote_kind: "paper_mark",
		created_at: minutesAgo(2_880),
	},
	{
		id: "paper-fill-1019",
		order_id: "paper-ord-1019",
		chain: "solana",
		token_address: "7atgF8KQo4wJrD5ATGX7t1V2zVvykPJbFfNeVf1icFv1",
		side: "buy",
		quantity: "5927.00",
		price_usd: "0.004218",
		notional_usd: "25.00",
		fee_usd: "0.08",
		quote_kind: "paper_mark",
		created_at: minutesAgo(5_760),
	},
];

export function fixtureOrders(limit: number): PaperOrderRecord[] {
	return fixturePaperOrders.slice(0, limit).map((row) => ({ ...row }));
}

export function fixtureFills(limit: number): PaperFillRecord[] {
	return fixturePaperFills.slice(0, limit).map((row) => ({ ...row }));
}
