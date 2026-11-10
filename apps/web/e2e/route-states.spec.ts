import { expect, test } from "./test";

const ROUTES = ["/strategies", "/ledger", "/operations", "/security"];

for (const route of ROUTES) {
	test(`${route} renders without an error boundary`, async ({ page }) => {
		await page.goto(route);
		await expect(page.locator("h1")).toBeVisible();
		await expect(page.locator(".route-error")).toHaveCount(0);
	});
}
