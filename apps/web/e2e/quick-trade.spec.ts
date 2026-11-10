import { expect, test, type Page } from "./test";

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const SCAM_MINT = "Scam1111111111111111111111111111111111111111";

const noExternal = (page: Page) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});
	return external;
};

test("a preset re-quotes on the server and shows the landed cost sheet before submit", async ({
	page,
}) => {
	const external = noExternal(page);
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const bar = page.getByTestId("quick-trade-bar");
	await expect(bar).toContainText("idem ");

	await expect(bar.getByTestId("quick-trade-submit")).toBeEnabled();
	await bar.getByTestId("quick-trade-preset-250").click();

	await expect(page).toHaveURL(/[?&]amount=250(&|$)/, { timeout: 60_000 });
	await expect(page.locator("#quote")).toContainText("$250.00");
	await expect(bar.getByTestId("cost-sheet")).toBeVisible();
	await expect(bar.getByTestId("cost-sheet")).toContainText("Platform fee");
	await expect(bar.getByTestId("cost-sheet")).toContainText("Venue fee");
	await expect(bar.getByTestId("cost-sheet")).toContainText("Estimated impact");
	await expect(bar.getByTestId("cost-sheet")).toContainText("Receiver's worst case");
	await expect(bar).toContainText("Jupiter v6 → Raydium");
	await expect(bar.getByTestId("quick-trade-submit")).toBeEnabled();

	// The ticket below follows the same server selection instead of keeping a stale amount.
	await expect(page.locator("#ticket").getByLabel("Paper amount in USD")).toHaveValue("250");
	await expect(
		page.locator("#ticket").getByRole("button", { name: "Confirm paper order" }),
	).toBeEnabled();

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("a custom amount previews, is remembered as a preset, and submits once", async ({
	page,
}) => {
	const posted: { body: Record<string, unknown> | null; count: number } = {
		body: null,
		count: 0,
	};
	await page.route("**/api/v1/paper/orders", async (route) => {
		posted.count += 1;
		posted.body = route.request().postDataJSON() as Record<string, unknown>;
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "ord_quick_01",
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: "175.5",
				status: "filled",
				reason: null,
				quote_kind: "jupiter",
				created_at: "2026-11-09T10:00:00Z",
				filled_at: "2026-11-09T10:00:01Z",
			}),
		});
	});

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const bar = page.getByTestId("quick-trade-bar");
	await expect(bar).toContainText("idem ");
	await bar.getByTestId("quick-trade-preset-custom").click();

	const amount = bar.getByLabel("Quick trade amount in USD");
	await amount.fill("175.5");
	await amount.press("Enter");

	await expect(page).toHaveURL(/[?&]amount=175\.5(&|$)/, { timeout: 60_000 });
	await expect(bar.getByTestId("cost-sheet")).toBeVisible();
	await expect(bar.getByTestId("quick-trade-preset-175.5")).toBeVisible();

	// A reload keeps the custom notional as a preset; the server quote still matches.
	await page.reload();
	await expect(
		page.getByTestId("quick-trade-bar").getByTestId("quick-trade-preset-175.5"),
	).toBeVisible();
	await expect(page.getByTestId("quick-trade-bar").getByTestId("quick-trade-submit")).toBeEnabled();

	await expect(page.getByTestId("quick-trade-bar")).toContainText("idem ");
	await page.getByTestId("quick-trade-bar").getByTestId("quick-trade-submit").click();
	await expect(page.getByTestId("quick-trade-bar").getByText("PAPER FILL")).toBeVisible();
	await expect(page.getByTestId("quick-trade-bar")).toContainText("ord_quick_01");

	expect(posted.count, "the bar must POST exactly once").toBe(1);
	if (!posted.body) throw new Error("expected a POST body");
	expect(posted.body).toMatchObject({
		chain: "solana",
		token_address: BONK_MINT,
		side: "buy",
		amount_usd: "175.5",
	});
	expect(String(posted.body.idempotency_key)).toMatch(/^[0-9a-f-]{16,}$/i);
});

test("a filled buy offers protection as an explicit click, never automatically", async ({
	page,
}) => {
	await page.route("**/api/v1/paper/orders", async (route) => {
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "ord_quick_02",
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: "100",
				status: "filled",
				reason: null,
				quote_kind: "jupiter",
				created_at: "2026-11-09T10:00:00Z",
				filled_at: "2026-11-09T10:00:01Z",
			}),
		});
	});

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const bar = page.getByTestId("quick-trade-bar");
	await expect(bar).toContainText("idem ");

	await expect(bar.getByTestId("quick-trade-protect-form")).toHaveCount(0);
	await bar.getByTestId("quick-trade-submit").click();
	await expect(bar.getByText("PAPER FILL")).toBeVisible();
	await expect(bar.getByTestId("quick-trade-protect-form")).toHaveCount(0);

	await bar.getByTestId("quick-trade-protect").click();
	const form = bar.getByTestId("quick-trade-protect-form");
	await expect(form).toBeVisible();
	await expect(form.getByLabel("Loss (%)")).toHaveValue("8");

	await form.getByLabel("Loss (%)").fill("0");
	await form.getByTestId("quick-trade-arm").click();
	await expect(form.getByRole("status")).toContainText("Enter a positive stop-loss percent.");
	await expect(bar.getByText(/Armed stop-loss/)).toHaveCount(0);

	await form.getByLabel("Loss (%)").fill("5");
	await form.getByTestId("quick-trade-arm").click();
	await expect(form.getByRole("status")).toContainText("Armed stop-loss for BONK.");

	// A new preview is a new trade. The previous fill, protect form and armed notice
	// must not survive onto a quote that has not filled.
	await bar.getByTestId("quick-trade-preset-250").click();
	await expect(page).toHaveURL(/[?&]amount=250(&|$)/, { timeout: 60_000 });
	await expect(bar.getByTestId("quick-trade-protect-form")).toHaveCount(0);
	await expect(bar.getByText(/Armed stop-loss/)).toHaveCount(0);
	await expect(bar.getByText("PAPER FILL")).toHaveCount(0);
	await expect(bar.getByTestId("quick-trade-protect")).toHaveCount(0);
	await expect(bar.getByTestId("quick-trade-submit")).toContainText("Confirm paper order");
	await expect(bar.getByTestId("quick-trade-submit")).toBeEnabled();
});

test("the quick-trade bar fails closed when no route or price exists", async ({ page }) => {
	await page.goto(`/tokens/solana/${SCAM_MINT}`);
	const bar = page.getByTestId("quick-trade-bar");
	await expect(bar).toContainText("idem ");

	await expect(bar.getByText("NO_SELL_ROUTE", { exact: true })).toBeVisible();
	await expect(bar.getByTestId("quick-trade-submit")).toBeDisabled();
	await expect(bar.getByTestId("cost-sheet")).toHaveCount(0);
	await expect(bar).not.toContainText("Jupiter v6");
	await expect(bar).not.toContainText("3,182,686");

	await bar.getByRole("button", { name: "Sell" }).click();
	await expect(page).toHaveURL(/[?&]side=sell(&|$)/, { timeout: 60_000 });
	await expect(bar.getByText("NO_SELL_ROUTE", { exact: true })).toBeVisible();
	await expect(bar.getByTestId("quick-trade-submit")).toBeDisabled();
	await expect(bar.getByTestId("cost-sheet")).toHaveCount(0);
});
