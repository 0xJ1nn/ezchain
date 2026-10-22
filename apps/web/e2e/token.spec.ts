import { expect, test, type Page } from "@playwright/test";

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const SCAM_MINT = "Scam1111111111111111111111111111111111111111";
const AERO_MINT = "0x940181a94A35A4569E4529A3CDfB74e38FD98631";

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

test("token page renders live-shaped fixtures with source, age and a paper quote", async ({
	page,
}) => {
	const external = noExternal(page);
	await page.goto(`/tokens/solana/${BONK_MINT}`);

	await expect(page.getByRole("heading", { name: /Bonk/, level: 1 })).toBeVisible();
	await expect(page.getByText("FIXTURE", { exact: true })).toBeVisible();
	await expect(page.getByText("LIVE ·")).toHaveCount(0);
	await expect(page.locator(".page-actions .mono").first()).toHaveText("$0.00003142");

	// Source and age are printed next to the price, and the chain registry supplies tier.
	await expect(page.getByText(/Fixture · \d+s old/).first()).toBeVisible();
	await expect(page.locator(".page-actions")).toContainText("Solana · T1");

	// Pairs come from the token snapshot with the same source/age.
	await expect(page.locator("#pairs tbody tr")).toHaveCount(3);
	await expect(page.locator("#pairs tbody tr").first()).toContainText("raydium");
	await expect(page.locator("#pairs tbody tr").first()).toContainText("$0.00003142");
	await expect(page.locator("#pairs tbody tr").first()).toContainText(/Fixture · \d+s/);

	// Safety is unknown everywhere. The page never promotes a miss to a pass.
	await expect(page.locator("#safety .panel-head .badge")).toHaveText("unknown");
	await expect(page.locator("#safety tbody tr")).toHaveCount(5);
	await expect(
		page.locator("#safety tbody tr td:nth-child(2) .badge", { hasText: "unknown" }),
	).toHaveCount(5);
	await expect(page.getByText("SAFETY_NOT_IMPLEMENTED").first()).toBeVisible();

	// Quote panel: server-rendered Jupiter paper quote with PAPER on every number.
	const quotePanel = page.locator("#quote");
	await expect(quotePanel.getByText("PAPER · JUPITER").first()).toBeVisible();
	await expect(quotePanel).toContainText("Jupiter v6 → Raydium");
	await expect(quotePanel).toContainText("3,182,686.19 BONK");
	await expect(quotePanel).toContainText("$0.00003142");
	await expect(quotePanel).toContainText(/Fixture · \d+s/);
	const paperBadges = await quotePanel.locator(".badge", { hasText: "PAPER" }).count();
	expect(paperBadges).toBeGreaterThan(5);

	// Quote side switch goes through the server (URL) and the paper ticket preview matches.
	await expect(page.locator("#ticket")).toContainText("3,182,686.19 BONK");
	await expect(page.getByRole("button", { name: "Confirm paper order" })).toBeEnabled();

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);

	await page.screenshot({ path: ".shots/token-1440.png", fullPage: true });
});

test("paper buy reaches a success state with a client idempotency key", async ({ page }) => {
	const posted: { body: Record<string, unknown> | null } = { body: null };
	await page.route("**/api/v1/paper/orders", async (route) => {
		posted.body = route.request().postDataJSON() as Record<string, unknown>;
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "ord_paper_01",
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: "100",
				status: "filled",
				reason: null,
				quote_kind: "jupiter",
				created_at: "2026-10-22T10:00:00Z",
				filled_at: "2026-10-22T10:00:01Z",
			}),
		});
	});

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const confirm = page.getByRole("button", { name: "Confirm paper order" });
	await expect(confirm).toBeEnabled();
	await confirm.click();

	await expect(page.getByText("PAPER FILL")).toBeVisible();
	await expect(page.locator("#ticket")).toContainText("ord_paper_01");
	await expect(page.locator("#ticket")).toContainText("BUY $100.00");
	await expect(page.locator("#ticket")).toContainText("No funds moved and no transaction was signed");
	await expect(page.getByRole("button", { name: "Order submitted" })).toBeDisabled();

	if (!posted.body) throw new Error("expected a POST body");
	const body = posted.body;
	expect(body).toMatchObject({
		chain: "solana",
		token_address: BONK_MINT,
		side: "buy",
		amount_usd: "100",
	});
	expect(String(body.idempotency_key)).toMatch(/^[0-9a-f-]{16,}$/i);
	expect(body).not.toHaveProperty("private_key");
	expect(body).not.toHaveProperty("secret");
	expect(body).not.toHaveProperty("api_key");
});

test("paper buy shows the API reason on rejection", async ({ page }) => {
	await page.route("**/api/v1/paper/orders", async (route) => {
		await route.fulfill({
			status: 502,
			contentType: "application/json",
			body: JSON.stringify({
				detail: {
					code: "NO_SELL_ROUTE",
					message: "buy has no full-size sell route",
					retryable: false,
				},
			}),
		});
	});

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await page.getByRole("button", { name: "Confirm paper order" }).click();

	await expect(page.locator("#ticket .note.err")).toBeVisible();
	await expect(page.locator("#ticket")).toContainText("NO_SELL_ROUTE");
	await expect(page.locator("#ticket")).toContainText("buy has no full-size sell route");
	await expect(page.locator("#ticket")).toContainText("Nothing was filled");
	await expect(page.getByText("PAPER FILL")).toHaveCount(0);
});

test("scamcoin fails closed with no route and no confirmable order", async ({ page }) => {
	await page.goto(`/tokens/solana/${SCAM_MINT}`);

	await expect(page.getByRole("heading", { name: /Scamcoin/, level: 1 })).toBeVisible();
	await expect(page.locator(".page-actions .badge.err").first()).toHaveText("NO_SELL_ROUTE");

	// No fabricated route or out amount survives; the reason is visible text.
	await expect(page.locator("#quote")).toContainText("NO_SELL_ROUTE");
	await expect(page.locator("#quote")).toContainText("buy has no full-size sell route");
	await expect(page.locator("#quote")).toContainText("Reason from the API");
	await expect(page.locator("#quote")).not.toContainText("Jupiter v6");
	await expect(page.locator("#quote")).not.toContainText("3,182,686");

	const confirm = page.getByRole("button", { name: "Confirm paper order" });
	await expect(confirm).toBeDisabled();
	await expect(page.locator("#ticket")).toContainText("NO_SELL_ROUTE");

	await page.screenshot({ path: ".shots/token-scam-1440.png", fullPage: true });
});

test("a non-solana token is labelled PAPER · MARK, not an executable route", async ({ page }) => {
	await page.goto(`/tokens/base/${AERO_MINT}`);

	await expect(page.getByRole("heading", { name: /Aerodrome/, level: 1 })).toBeVisible();
	await expect(page.getByText("PAPER · MARK").first()).toBeVisible();
	await expect(page.locator("#quote")).toContainText("paper mark · no executable route");
	await expect(page.locator("#quote")).toContainText(
		"This chain has no executable venue in P0",
	);
	await expect(page.getByRole("button", { name: "Confirm paper order" })).toBeEnabled();
});

test("sell without a token quantity fails closed instead of guessing a size", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?side=sell&amount=100`);

	await expect(page.locator("#quote")).toContainText("NO_SELL_ROUTE");
	await expect(page.locator("#quote")).toContainText("sell requires a positive token quantity");
	await expect(page.getByRole("button", { name: "Confirm paper order" })).toBeDisabled();
});

test("unknown chain or token ids return 404", async ({ page }) => {
	for (const route of [
		`/tokens/solana/nope`,
		`/tokens/ethereum/${BONK_MINT}`,
		`/tokens/solana/${SCAM_MINT}extra`,
		"/tokens/bonk",
	]) {
		const response = await page.goto(route);
		expect(response?.status()).toBe(404);
	}
	await expect(page.getByRole("heading", { name: "Not found", level: 1 })).toBeVisible();
});

test("token page has no external requests", async ({ page }) => {
	const external = noExternal(page);
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.locator("#safety tbody tr")).toHaveCount(5);
	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("token page screenshot at mobile width", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await expect(page.getByText("FIXTURE", { exact: true })).toBeVisible();

	const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
	expect(scrollWidth, "page must not scroll sideways on a 390px viewport").toBeLessThanOrEqual(
		390,
	);
	await page.screenshot({ path: ".shots/token-390.png", fullPage: true });
});
