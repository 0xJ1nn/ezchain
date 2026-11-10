import { expect, test } from "./test";

test("mobile surface renders server rows at 390px with no write control", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/m");
	await expect(page.getByRole("heading", { name: "Mobile workspace", level: 1 })).toBeVisible();

	await expect(page.getByText("BONK")).toBeVisible();
	await expect(page.getByText("unknown mark", { exact: true })).toBeVisible();

	await expect(page.getByText(/paper-fill|buy/i).first()).toBeVisible();

	const writeControls = page.locator("main button, main input, main textarea, main select, main form");
	await expect(writeControls).toHaveCount(0);
});