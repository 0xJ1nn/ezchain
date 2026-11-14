import { expect, test } from "./test";

// The trade-policy surface is one face of the dual-surface requirement: it renders the
// fields the order gate reads with their range and description, and a strategy override
// carries the disclosure at the point of choice. Fixture mode serves the documented
// defaults; a write and a sizing verdict are real server answers and are refused.

test("the trade-policy page renders every profile field with its range", async ({ page }) => {
	await page.goto("/settings/trade-policy");

	await expect(page.getByRole("heading", { name: "Trade policy", level: 1 })).toBeVisible();
	for (const profile of ["low", "mid", "high"]) {
		await expect(page.locator(`[data-profile="${profile}"]`)).toBeVisible();
	}
	const mid = page.locator('[data-profile="mid"]');
	await expect(mid.getByText("Minimum order size")).toBeVisible();
	await expect(mid.getByText("Maximum order size")).toBeVisible();
	await expect(mid.getByText("Maximum share of equity")).toBeVisible();
	await expect(mid.getByText("Per-token exposure cap")).toBeVisible();
	await expect(mid.getByText("Maximum slippage")).toBeVisible();
	await expect(mid).toContainText("0.05");
	await expect(mid).toContainText("0–1000000 USD");

	// The active policy is the server's document, with a description on every row.
	const active = page.locator('[data-field="max_order_pct_of_equity"]');
	await expect(active).toContainText("Maximum share of equity");
	await expect(active).toContainText("EQUITY_UNKNOWN");
	await expect(active).toContainText("0.05");
});

test("the page names the server as the one store", async ({ page }) => {
	await page.goto("/settings/trade-policy");

	await expect(page.getByText("GET /api/v1/trade-policy").first()).toBeVisible();
	await expect(page.getByText("PUT /api/v1/trade-policy/strategies/<id>")).toBeVisible();
	await expect(page.getByText("GET /api/v1/trade-policy/preview")).toBeVisible();
	// A strategy without an override says so instead of showing a bare profile.
	const bonk = page.locator('[data-strategy="str-bonk-momentum"]');
	await expect(bonk).toContainText("deployment default");
	await expect(bonk).toContainText("effective profile mid");
});

test("a loosening shows one inline acknowledgement, not a modal", async ({ page }) => {
	await page.goto("/settings/trade-policy");
	const bonk = page.locator('[data-strategy="str-bonk-momentum"]');

	await bonk.getByRole("combobox", { name: "BONK momentum risk profile" }).selectOption("high");

	await expect(bonk.getByRole("checkbox")).toBeVisible();
	await expect(bonk).toContainText("You are loosening BONK momentum.");
	await expect(bonk).toContainText("more of your own money");
	await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("a stricter profile shows no acknowledgement", async ({ page }) => {
	await page.goto("/settings/trade-policy");
	const bonk = page.locator('[data-strategy="str-bonk-momentum"]');

	await bonk.getByRole("combobox", { name: "BONK momentum risk profile" }).selectOption("low");

	await expect(bonk.getByRole("checkbox")).toHaveCount(0);
	await expect(bonk.getByText("You are loosening")).toHaveCount(0);
});

test("saving in fixture mode is refused honestly and never fabricates a result", async ({
	page,
}) => {
	await page.goto("/settings/trade-policy");
	const bonk = page.locator('[data-strategy="str-bonk-momentum"]');

	await bonk.getByRole("combobox", { name: "BONK momentum risk profile" }).selectOption("high");
	await bonk.getByRole("checkbox").check();
	await bonk.getByRole("button", { name: "Save" }).click();

	await expect(page.getByText("not simulated in fixture mode")).toBeVisible();
	await expect(bonk).toContainText("deployment default");
});

test("the sizing preview stays disabled in fixture mode", async ({ page }) => {
	await page.goto("/settings/trade-policy");

	await expect(page.getByText("This fixture build cannot answer for it")).toBeVisible();
	await expect(page.getByRole("button", { name: "Preview" })).toBeDisabled();
});
