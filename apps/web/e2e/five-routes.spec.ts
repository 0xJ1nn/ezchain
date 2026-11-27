import { expect, test } from "./test";

// The five partial routes. Each assertion follows one real read model or one typed
// failure: no alert, provider, key or report row is substituted for a read the app could
// not make. Fixture mode is the e2e default, so the states checked here are the honest
// labels and empty states it produces, not live account values.

test("alerts shows the typed unread input, an honest empty feed and no invented rows", async ({
	page,
}) => {
	await page.goto("/alerts");
	await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();

	const incomplete = page.locator("section.panel").filter({ hasText: "Alert inputs incomplete" });
	await expect(incomplete).toContainText("FIXTURE_UNAVAILABLE");
	await expect(incomplete).toContainText("Nothing is guessed");
	await expect(incomplete.getByRole("link", { name: "Retry →" })).toHaveAttribute(
		"href",
		"/alerts",
	);

	await expect(page.getByText("No derived alerts")).toBeVisible();
	await expect(page.getByText("Derived alerts", { exact: true })).toHaveCount(0);

	const band = page.locator('section.kpi-band[aria-label="Alert summary"]');
	for (const label of ["Critical", "Warnings", "Info"]) {
		await expect(band.locator(".stat").filter({ hasText: label }).locator(".value")).toHaveText(
			"0",
		);
	}
	await expect(band.locator(".stat").filter({ hasText: "Delivery" }).locator(".value")).toHaveText(
		"—",
	);

	const delivery = page.locator("section.panel").filter({ hasText: "Delivery and rules" });
	await expect(delivery).toContainText("no alert rule object");
	await expect(delivery).toContainText("delivery log");
});

test("watchlist empty state keeps the stars browser-local and offers one action", async ({
	page,
}) => {
	await page.goto("/watchlist");
	await expect(page.getByRole("heading", { name: "Watchlist", level: 1 })).toBeVisible();
	await expect(page.getByText("No starred tokens yet")).toBeVisible();
	await expect(page.getByText("BROWSER-LOCAL")).toBeVisible();
	await expect(page.getByRole("link", { name: "Browse markets →" })).toBeVisible();
	await expect(page.locator("table")).toHaveCount(0);
});

test("watchlist uses the market lookup and types a failed read instead of dropping rows", async ({
	page,
}) => {
	await page.goto("/markets");
	const star = page
		.locator("table")
		.first()
		.locator("tbody tr")
		.filter({ hasText: "BONK" })
		.getByRole("button");
	await star.click();

	await page.goto("/watchlist");
	const row = page.locator("table").first().locator("tbody tr").filter({ hasText: "BONK" });
	await expect(row).toBeVisible();
	await expect(page.getByText("BROWSER-LOCAL")).toBeVisible();
	await expect(page.getByText("FIXTURE DATA", { exact: true })).toBeVisible();

	let failLookup = true;
	await page.route("**/watchlist/rows*", (route) => {
		if (!failLookup) return route.continue();
		return route.fulfill({
			status: 500,
			contentType: "application/json",
			body: JSON.stringify({
				detail: { code: "MARKET_FEED_DOWN", message: "the market feed is unreachable" },
			}),
		});
	});

	const refresh = page.getByRole("button", { name: /Refresh/ });
	await expect(refresh).toBeEnabled();
	await refresh.click();

	const failed = page.locator("section.panel").filter({ hasText: "Watchlist lookup failed" });
	await expect(failed).toContainText("MARKET_FEED_DOWN");
	await expect(failed).toContainText("the market feed is unreachable");
	await expect(failed).toContainText("No rows are substituted for a failed read");

	// The retry is a real handler: it reissues the same lookup and restores the row.
	failLookup = false;
	await failed.getByRole("button", { name: "Retry" }).click();
	await expect(row).toBeVisible();
	await expect(failed).toHaveCount(0);
});

test("reports exports the rows it read and labels the browser-side population", async ({
	page,
}) => {
	await page.goto("/reports");
	await expect(page.getByRole("heading", { name: "Reports", level: 1 })).toBeVisible();

	for (const dataset of ["orders", "fills", "positions"]) {
		await expect(page.getByText(`/api/v1/paper/${dataset}`, { exact: false }).first()).toBeVisible();
	}

	const downloadPromise = page.waitForEvent("download");
	await page.getByRole("button", { name: "Export orders CSV" }).click();
	const download = await downloadPromise;
	expect(download.suggestedFilename()).toMatch(/^ezchain-paper-orders-.*\.csv$/);
	await expect(page.getByRole("status")).toContainText("Exported");

	await expect(page.getByText("no API was called")).toBeVisible();
	const inside = page.locator("section.panel").filter({ hasText: "What is inside" });
	await expect(inside.getByRole("table")).toBeVisible();
	await expect(inside.getByText("unavailable")).toHaveCount(0);
});

test("integrations and security name real capabilities without drawing absent controls", async ({
	page,
}) => {
	await page.goto("/integrations");
	await expect(page.getByRole("heading", { name: "Integrations", level: 1 })).toBeVisible();
	const ladders = page.locator("section.panel").filter({ hasText: "Quote ladders" });
	await expect(ladders.getByRole("table").locator("tbody tr").first()).toBeVisible();
	await expect(page.getByText("NO TOGGLES", { exact: true })).toBeVisible();
	await expect(page.getByText("NO WEBHOOKS", { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Add integration" })).toHaveCount(0);

	await page.goto("/security");
	await expect(page.getByRole("heading", { name: "Security", level: 1 })).toBeVisible();
	await expect(page.getByText("Authentication posture")).toBeVisible();
	await expect(page.getByText("NO KEYS", { exact: true })).toBeVisible();
	const execution = page.locator("section.panel").filter({ hasText: "Live execution" });
	await expect(execution.getByText("PAPER", { exact: true })).toBeVisible();
	await expect(page.getByText("Lock vault now")).toHaveCount(0);
});
