import { expect, test } from "./test";

// The strategy surface must show the declarative manifest, params, grants and paper-gate
// evidence without presenting a recorded refusal as a profitability promise or inventing
// a permission the API does not expose.

test("strategy detail shows manifest, params and recorded gate evidence", async ({ page }) => {
	await page.goto("/strategies/str-bonk-momentum");
	await expect(page.getByRole("heading", { name: "BONK momentum", level: 1 })).toBeVisible();

	const manifest = page.getByRole("region", { name: "Manifest table" });
	await expect(manifest).toContainText("built-in: momentum");
	await expect(manifest).toContainText("inherits deployment default");
	await expect(manifest).toContainText("Not exposed by this API");
	await expect(manifest).toContainText("None bundled");

	await expect(page.getByRole("region", { name: "Parameters table" })).toContainText("Max position (USD)");

	const evidence = page
		.getByRole("heading", { name: "Paper gate evidence" })
		.locator("xpath=../..");
	await expect(evidence).toContainText("not a promise");
	await expect(evidence).toContainText("below entry 5.0%");
	await expect(evidence.getByText("recorded refusals and holds", { exact: false })).toBeVisible();
	// Evidence is a recorded decision with its reason; no return figure is computed here.
	await expect(evidence).toContainText("No return is computed here");
});

test("a strategy with no refusal has an empty evidence state, not a fabricated one", async ({
	page,
}) => {
	await page.goto("/strategies/str-sol-dca");
	const evidence = page
		.getByRole("heading", { name: "Paper gate evidence" })
		.locator("xpath=../..");
	await expect(evidence).toContainText("No refusal or hold has been recorded");
	await expect(evidence).toContainText("has not been proven safe");
});

test("the list shows allocation and permission without bundling strategy code", async ({ page }) => {
	await page.goto("/strategies");
	const card = page.locator("article").filter({ hasText: "BONK momentum" }).first();
	await expect(card).toContainText("Allocation");
	await expect(card).toContainText("$500");
	await expect(card).toContainText("Risk profile");
	await expect(card).toContainText("PAPER");
});

test("a failed strategy read renders its code and no detail", async ({ page }) => {
	await page.goto("/strategies/str-bonk-momentum?state=error");
	await expect(page.getByRole("heading", { name: "Strategy unavailable", level: 1 })).toBeVisible();
	await expect(page.getByText("FIXTURE_DEMO_STRATEGY_ERROR")).toBeVisible();
	await expect(page.getByRole("region", { name: "Manifest table" })).toHaveCount(0);
	await expect(page.getByRole("heading", { name: "BONK momentum", level: 1 })).toHaveCount(0);
});
