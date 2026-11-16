import { expect, test } from "./test";

// The star is one control reused on market rows, token pages and position rows. These
// cases pin that a star from a position row or token page persists into the single
// workspace watchlist and that the markets filter sees the same entry.

test("a star on a position row persists and appears on the watchlist", async ({ page }) => {
	await page.goto("/positions");
	const row = page.locator('tr[data-position]').first();
	const symbol = (await row.locator(".strong").first().innerText()).trim();
	const star = row.getByRole("button", { name: new RegExp(`Add ${symbol} to watchlist`) });
	await expect(star).toBeVisible();
	await star.click();
	await expect(
		row.getByRole("button", { name: new RegExp(`Remove ${symbol} from watchlist`) }),
	).toHaveAttribute("aria-pressed", "true");

	await page.goto("/watchlist");
	const watchlist = page.locator("table").first();
	await expect(watchlist.locator("tbody tr").filter({ hasText: symbol })).toBeVisible();
});

test("a token page star shares the same entry as the market row", async ({ page }) => {
	await page.goto("/tokens/solana/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263");
	const star = page.getByRole("button", { name: /Add BONK to watchlist/ });
	await expect(star).toBeVisible();
	await star.click();
	await expect(
		page.getByRole("button", { name: /Remove BONK from watchlist/ }),
	).toHaveAttribute("aria-pressed", "true");

	await page.goto("/markets?starred=1");
	await expect(page.getByTestId("starred-only-summary")).toBeVisible();
	await expect(
		page.locator("table").first().locator("tbody tr").filter({ hasText: "BONK" }),
	).toBeVisible();
	// The starred filter is a real URL state, not a client-only hide.
	await expect(page.getByRole("link", { name: "Show all markets" }).first()).toBeVisible();
});
