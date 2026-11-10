import { expect, test } from "./test";
import { previewCost } from "../lib/cost-model";
import { fixtureQuote } from "../lib/live-fixtures";
import { formatAmount, formatUsd } from "../lib/live-format";

// The pre-trade cost sheet (bug #154): every trade surface previews the
// trade before the click, one line each with source and age. These tests are deliberately
// written against the fixture cost model — the same numbers the pages render — so "values
// agree with the fixture cost model" is a wiring assertion, not a retyped constant.

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";

test("the TS cost model matches the journal's max(paper fee, venue fee) rule", () => {
	// Pinned against tests/paper/test_quote_fee.py: $100 notional, 30 bps paper fee.
	const cheap = previewCost({
		kind: "jupiter",
		notional_usd: "100",
		venue_fee_usd: "0.10",
		amount_out: null,
	});
	expect(cheap.fill_fee_usd).toBe("0.3");

	const pricey = previewCost({
		kind: "jupiter",
		notional_usd: "100",
		venue_fee_usd: "1.50",
		amount_out: null,
	});
	expect(pricey.fill_fee_usd).toBe("1.5");

	// Jupiter reports no fee: the 30 bps paper leg is the whole fill fee.
	const none = previewCost({
		kind: "jupiter",
		notional_usd: "100",
		venue_fee_usd: "0",
		amount_out: null,
	});
	expect(none.fill_fee_usd).toBe("0.3");

	// A paper mark carries no venue slippage: the worst case is unknown, never zero.
	const mark = previewCost({
		kind: "paper_mark",
		notional_usd: "100",
		venue_fee_usd: "0",
		amount_out: null,
	});
	expect(mark.slippage_bps).toBeNull();
	expect(mark.worst_case_out).toBeNull();
});

test("the token-page quote panel shows the full cost sheet with source and age", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?side=buy&amount=100`);

	const quote = fixtureQuote({ chain: "solana", token: BONK_MINT, side: "buy", amountUsd: "100" });
	const model = previewCost({
		notional_usd: quote.amount_in,
		venue_fee_usd: quote.fee_usd,
		kind: quote.kind,
		amount_out: quote.amount_out,
	});

	const sheet = page.locator("#quote [data-testid='cost-sheet']");
	await expect(sheet).toBeVisible();
	await expect(sheet).toContainText("PAPER");
	// Fixture mode stays labelled: the page banner says FIXTURE, every row names
	// its fixture source (e.g. "Fixture · 1s") — sample data never looks live.
	await expect(page.getByText("FIXTURE", { exact: true }).first()).toBeVisible();
	await expect(sheet.getByText(/Fixture · \d+s/).first()).toBeVisible();

	// One line each: platform fee is a known zero (self-hosted), venue fee agrees with
	// the fixture quote, unknown lines print an em dash — never zero.
	await expect(sheet).toContainText("Platform fee");
	await expect(sheet).toContainText("Venue fee");
	await expect(sheet).toContainText("Network / priority fee");
	await expect(sheet).toContainText("Estimated impact");
	await expect(sheet).toContainText("Slippage window");
	await expect(sheet).toContainText("Receiver's worst case");

	expect(quote.cost_preview?.fill_fee_usd).toBe(model.fill_fee_usd);
	await expect(sheet).toContainText(formatUsd(model.fill_fee_usd));
	await expect(sheet).toContainText(formatUsd("0"));
	await expect(sheet).toContainText(/—/);
	// Estimated impact is unknown in the fixture model: em dash, never a fabricated zero.
	await expect(sheet.locator("tr", { hasText: "Estimated impact" })).toContainText("—");

	// The worst case agrees with the fixture model: expected out inside the window.
	const worst = model.worst_case_out;
	if (!worst) throw new Error("fixture Jupiter quote must carry a worst case");
	await expect(sheet).toContainText(formatAmount(worst));

	// Slippage window agrees with the fixture model's requested window.
	if (model.slippage_bps === null) throw new Error("fixture Jupiter quote must carry slippage");
	await expect(sheet).toContainText("±0.50%");
});

test("the quick-trade ticket shows the same cost sheet rows", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?side=buy&amount=100`);

	const quote = fixtureQuote({ chain: "solana", token: BONK_MINT, side: "buy", amountUsd: "100" });
	const model = previewCost({
		notional_usd: quote.amount_in,
		venue_fee_usd: quote.fee_usd,
		kind: quote.kind,
		amount_out: quote.amount_out,
	});

	const ticketSheet = page.locator("#ticket [data-testid='cost-sheet']");
	await expect(ticketSheet).toBeVisible();
	await expect(ticketSheet).toContainText("Platform fee");
	await expect(ticketSheet).toContainText("Venue fee");
	await expect(ticketSheet).toContainText("Receiver's worst case");
	await expect(ticketSheet).toContainText(formatUsd(model.fill_fee_usd));
});

test("sell previews the worst case in USD at the same window", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?side=sell&amount=100`);

	const quote = fixtureQuote({ chain: "solana", token: BONK_MINT, side: "sell", amountUsd: "100" });
	const model = previewCost({
		notional_usd: quote.amount_out,
		venue_fee_usd: quote.fee_usd,
		kind: quote.kind,
		amount_out: quote.amount_out,
	});
	const worst = model.worst_case_out;
	if (!worst) throw new Error("fixture Jupiter sell must carry a worst case");

	const sheet = page.locator("#quote [data-testid='cost-sheet']");
	await expect(sheet).toBeVisible();
	await expect(sheet).toContainText("Receiver's worst case");
	await expect(sheet).toContainText(formatUsd(worst));
});
