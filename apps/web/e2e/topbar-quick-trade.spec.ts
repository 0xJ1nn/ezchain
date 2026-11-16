import { expect, test } from "./test";

// The topbar entry point supplies only a token context and a server quote. The ticket,
// presets, protection and cost sheet are the shared QuickTradeBar, and the mode chip and
// kill switch stay visible while the panel is open.

test("from any page, a token search opens the same paper ticket with presets", async ({
	page,
}) => {
	await page.goto("/");
	const trigger = page.getByRole("button", { name: /Quick trade/ });
	await expect(trigger).toBeVisible();
	await trigger.click();

	const panel = page.getByTestId("topbar-quick-trade-panel");
	await expect(panel).toBeVisible();
	// The global controls are not covered by the panel.
	const topbar = page.locator(".topbar");
	await expect(topbar.getByRole("button", { name: /Kill switch:/ })).toBeVisible();
	await expect(topbar.locator(".mode")).toBeVisible();

	await panel.getByLabel("Symbol or address").fill("BONK");
	await panel.getByRole("button", { name: /BONK · solana ·/ }).click();

	const bar = panel.getByTestId("quick-trade-bar");
	await expect(bar).toBeVisible();
	await expect(bar.getByTestId("quick-trade-preset-50")).toBeVisible();
	await expect(bar.getByTestId("quick-trade-preset-100")).toBeVisible();
	await expect(bar.getByTestId("quick-trade-preset-250")).toBeVisible();
	await expect(bar.getByTestId("cost-sheet")).toBeVisible();
	await expect(bar.getByTestId("quick-trade-submit")).toBeEnabled();

	// A preset changes the amount in the URL. The panel's quote is the one the server
	// supplied for this selection, so the shared bar fails closed instead of trading a
	// stale preview.
	await bar.getByTestId("quick-trade-preset-250").click();
	await expect(page).toHaveURL(/[?&]amount=250(&|$)/, { timeout: 60_000 });
});

test("an unmatched search adds no token and no invented quote", async ({ page }) => {
	await page.goto("/markets");
	await page.getByRole("button", { name: /Quick trade/ }).click();
	const panel = page.getByTestId("topbar-quick-trade-panel");
	await panel.getByLabel("Symbol or address").fill("NOTALISTEDTOKEN");
	await expect(panel.getByText("No market row matched. Nothing was selected.")).toBeVisible();
	await expect(panel.getByTestId("quick-trade-bar")).toHaveCount(0);
	// The shared component's no-route failure path is pinned in quick-trade.spec.ts.
});

test("the panel opens and closes from the keyboard", async ({ page }) => {
	await page.goto("/positions");
	const trigger = page.getByRole("button", { name: /Quick trade/ });
	await trigger.focus();
	await expect(trigger).toBeFocused();
	await page.keyboard.press("Enter");
	await expect(page.getByTestId("topbar-quick-trade-panel")).toBeVisible();
	await expect(trigger).toHaveAttribute("aria-expanded", "true");
	await page.keyboard.press("Enter");
	await expect(trigger).toHaveAttribute("aria-expanded", "false");
});
