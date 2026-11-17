import { expect, test } from "./test";

// Trading preferences are a local browser choice. They shape the ticket controls,
// never an amount sent to the API, a quote or a risk decision.

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";

test("quick-buy presets default to the board values and set a fixed USD amount", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const ticket = page.getByTestId("trade-ticket");
	await expect(ticket).toBeVisible();
	await expect(ticket.getByTestId("quick-buy-label")).toContainText("Quick buy");
	await expect(ticket.getByTestId("quick-buy-label")).toContainText("Edit");

	for (const value of [25, 100, 250, 500]) {
		await expect(ticket.getByTestId(`ticket-preset-${value}`)).toBeVisible();
	}
	await ticket.getByTestId("ticket-preset-250").click();
	await expect(page).toHaveURL(/[?&]amount=250/, { timeout: 60_000 });
	await expect(ticket.getByTestId("ticket-amount")).toHaveValue("250.00");
});

test("slippage and priority fee selectors are shown and persist from settings", async ({ page }) => {
	await page.goto("/settings/trading");
	const form = page.getByTestId("trading-form");
	await expect(form).toBeVisible();

	await form.getByTestId("preset-input-0").fill("40");
	await form.getByTestId("settings-slippage").selectOption("2.0");
	await form.getByTestId("settings-priority").selectOption("turbo");
	await expect(form.getByTestId("trading-saved")).toContainText("Saved in this browser");

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const ticket = page.getByTestId("trade-ticket");
	await expect(ticket.getByTestId("ticket-preset-40")).toBeVisible();
	await expect(ticket.getByTestId("ticket-slippage")).toHaveValue("2.0");
	await expect(ticket.getByTestId("ticket-priority")).toHaveValue("turbo");
	await expect(ticket.getByTestId("ticket-trade-settings")).toContainText("2.0%");
	await expect(ticket.getByTestId("ticket-trade-settings")).toContainText("Turbo");

	// The ticket can change the two selectors in place, and the choice survives a reload.
	await ticket.getByTestId("ticket-slippage").selectOption("0.5");
	await page.reload();
	await expect(ticket.getByTestId("ticket-slippage")).toHaveValue("0.5");
});

test("the quote names price impact and what the user gets; the profile line stays honest", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const quote = page.getByTestId("ticket-quote");
	await expect(quote).toContainText("You get");
	await expect(quote).toContainText("≈");
	await expect(quote).toContainText("Price impact");

	const impact = quote.getByText("Price impact").locator("..").locator("b");
	await expect(impact).toHaveAttribute(
		"title",
		"The venue did not report a price impact",
	);

	const profile = page.getByTestId("ticket-profile");
	await expect(profile).toContainText("Profile:");
	await expect(profile).toContainText("—");
	await expect(profile).toHaveAttribute(
		"title",
		"The API does not publish an auto profile for this token, so no exit profile is assumed.",
	);
});

test("a paper fill says it filled at the mark, not that it executed", async ({ page }) => {
	await page.route("**/api/v1/paper/orders", async (route) => {
		const body = route.request().postDataJSON() as Record<string, unknown>;
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "ord_ticket_mark",
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: body.amount_usd ?? "100",
				status: "filled",
				reason: null,
				quote_kind: "paper_mark",
				created_at: "2026-11-17T10:00:00Z",
				filled_at: "2026-11-17T10:00:01Z",
			}),
		});
	});
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const ticket = page.getByTestId("trade-ticket");
	await expect(ticket).toContainText("Paper-filled at the mark");
	const submit = page.getByTestId("trade-submit");
	await submit.scrollIntoViewIfNeeded();
	const box = await submit.boundingBox();
	if (!box) throw new Error("no submit box");
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(800);
	await page.mouse.up();
	await expect(page.getByTestId("fill-stamp")).toContainText("PAPER FILLED");
	await expect(ticket).not.toContainText("Executed");
	await expect(ticket.getByTestId("fill-receipt")).toContainText("Paper-filled");
});
