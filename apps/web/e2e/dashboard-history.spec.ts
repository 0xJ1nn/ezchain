import { expect, test } from "./test";

// The dashboard history and its position tiles are projections of server data. Every
// point and tile value must come from a returned field; an unread series stays typed and
// an unreturned chart or ladder zone stays empty rather than being drawn or guessed.

test("the dashboard plots the supplied equity series and labels its source", async ({ page }) => {
	await page.goto("/");
	const history = page.locator("section.panel").filter({ hasText: "Account history" });
	await expect(history).toContainText("FIXTURE SAMPLE");
	await expect(history.getByRole("img", { name: /Equity history/ })).toBeVisible();
	const points = history.getByRole("table", { name: "Equity history points" });
	await expect(points.locator("tbody tr")).toHaveCount(6);
	await expect(history.getByText("No equity snapshots are stored yet")).toHaveCount(0);
	await expect(history.getByText(/Sample points for the fixture build only/)).toBeVisible();

	await page.screenshot({ path: ".shots/dashboard-history-1440.png", fullPage: true });
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/");
	await expect(page.locator("section.panel").filter({ hasText: "Account history" }).getByRole("img", { name: /Equity history/ })).toBeVisible();
	await page.screenshot({ path: ".shots/dashboard-history-390.png", fullPage: true });
});

test("open-position tiles use only server fields for chart and ladder zones", async ({ page }) => {
	await page.goto("/");
	const tiles = page.getByTestId("dashboard-position-tiles");
	await expect(tiles).toBeVisible();
	const count = await tiles.locator("article.tile").count();
	expect(count).toBeGreaterThan(0);

	const first = tiles.locator("article.tile").first();
	await expect(first).toContainText("No chart history returned");
	await expect(first).toContainText("NOT REPORTED");
	await expect(first).toContainText("NO HOLD BUDGET RETURNED");
	// The server does not return a percentage, so the tile prints — rather than 0%.
	const pnl = first.locator(".tile-pnl .pct");
	await expect(pnl).toHaveText("—");
	// Marks keep their server verdict as text, never a colour-only dot.
	await expect(first.locator(".tile-price")).toContainText("jupiter ·");
	await expect(first.locator(".tile-prov")).toContainText(/mark (fresh|stale|unknown|future|dead)/);
});

test("an empty account has no tiles and a failed series shows its code", async ({ page }) => {
	await page.goto("/?state=flat");
	await expect(page.getByTestId("dashboard-position-tiles")).toHaveCount(0);
	await expect(page.getByText("The paper account is flat.").first()).toBeVisible();

	await page.goto("/?state=series-unavailable");
	const history = page.locator("section.panel").filter({ hasText: "Account history" });
	await expect(history).toContainText("FIXTURE_DEMO_SERIES_ERROR");
	await expect(history).toContainText("No equity point is substituted for a failed read.");
	await expect(history.getByRole("img", { name: /Equity history/ })).toHaveCount(0);
	await expect(history.getByRole("table", { name: "Equity history points" })).toHaveCount(0);
});
