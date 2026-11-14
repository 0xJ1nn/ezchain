// The fixture cost model: the web fixture's stand-in for the server's one formula.
//
// This mirrors `packages/core-py/src/ezchain_core/money.py` (preview_cost) plus
// `paper_fee_bps = 30` from the API Settings, for fixture mode only. Live mode never
// computes here — the web displays the API's `cost_preview` verbatim (CONTRACT I10).
// The mirror is pinned by e2e/cost-sheet.spec.ts against the journal's
// max(paper fee, venue fee) rule, with the same cases as
// tests/paper/test_quote_fee.py. Unknown values stay null and render as an em dash.

export const FIXTURE_PAPER_FEE_BPS = 30;
export const FIXTURE_SLIPPAGE_BPS = 50;

export interface CostModelInput {
	kind: string;
	notional_usd: string;
	venue_fee_usd: string;
	amount_out: string | null;
}

export interface CostModelView {
	platform_fee_usd: string;
	venue_fee_usd: string;
	network_fee_usd: string | null;
	price_impact_pct: string | null;
	slippage_bps: number | null;
	worst_case_out: string | null;
	fill_fee_usd: string;
}

function round8(value: number): number {
	return Math.round(value * 1e8) / 1e8;
}

function text(value: number): string {
	return String(round8(value));
}

/** Eight-decimal quantize, exactly like Python's bps_fee at the ledger quantum. */
export function bpsFee(notionalUsd: string, feeBps: number): number {
	const notional = Number(notionalUsd);
	return round8((notional * feeBps) / 10_000);
}

export function previewCost(input: CostModelInput): CostModelView {
	const paperLeg = bpsFee(input.notional_usd, FIXTURE_PAPER_FEE_BPS);
	// The venue leg is a receipt figure: quantize it before the max, exactly as the
	// server does, so `fill_fee_usd` is the fee the journal books.
	const venueFee = round8(Number(input.venue_fee_usd));
	const fillFee = input.kind === "paper_mark" ? paperLeg : Math.max(paperLeg, venueFee);
	const slippageBps = input.kind === "paper_mark" ? null : FIXTURE_SLIPPAGE_BPS;
	const amountOut = input.amount_out === null ? null : Number(input.amount_out);
	return {
		platform_fee_usd: "0",
		venue_fee_usd: input.venue_fee_usd,
		network_fee_usd: null,
		price_impact_pct: null,
		slippage_bps: slippageBps,
		worst_case_out:
			slippageBps === null || amountOut === null
				? null
				: text(amountOut * (1 - slippageBps / 10_000)),
		fill_fee_usd: text(fillFee),
	};
}
