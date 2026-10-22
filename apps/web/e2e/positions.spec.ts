import { expect, test } from "@playwright/test";

test("positions page renders the four tile states from fixtures", async ({ page }) => {
	await page.goto("/positions");
	await expect(page.getByRole("heading", { name: "Positions", level: 1 })).toBeVisible();
	await expect(page.locator(".tile")).toHaveCount(4);
	await expect(page.locator(".tile .tile-name")).toHaveText(["Bonk", "Aerodrome", "Mochi", "Scamcoin"]);
	await expect(page.locator(".tile .badge", { hasText: "UNSELLABLE" })).toBeVisible();
	await expect(page.locator(".tile .badge", { hasText: "RIDING" })).toBeVisible();
	await expect(page.locator(".tile .badge", { hasText: "ENTERING" })).toBeVisible();
	await expect(page.locator("#aerodrome .badge", { hasText: "PRE-BANK" })).toBeVisible();
	await expect(page.getByText("Sell is unavailable.")).toBeVisible();
	await page.screenshot({ path: ".shots/positions-1440.png", fullPage: true });
});

test("positions page has no external requests", async ({ page }) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});
	await page.goto("/positions");
	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});
