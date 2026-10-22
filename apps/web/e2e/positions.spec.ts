import { expect, test } from "@playwright/test";

test("positions page renders the four tile states from fixtures", async ({ page }) => {
	await page.goto("/positions");
	await expect(page.getByRole("heading", { name: "Positions", level: 1 })).toBeVisible();
	await expect(page.locator(".tile")).toHaveCount(4);
	await expect(page.locator(".tile .tile-name")).toHaveText([
		"Bonk",
		"Aerodrome",
		"Mochi",
		"Scamcoin",
	]);
	await expect(page.locator(".tile .badge", { hasText: "UNSELLABLE" })).toBeVisible();
	await expect(page.locator(".tile .badge", { hasText: "RIDING" })).toBeVisible();
	await expect(page.locator(".tile .badge", { hasText: "ENTERING" })).toBeVisible();
	await expect(page.locator("#aerodrome .badge", { hasText: "PRE-BANK" })).toBeVisible();
	await expect(page.getByText("Sell is unavailable.")).toBeVisible();
	await page.screenshot({ path: ".shots/positions-1440.png", fullPage: true });
});

test("positions reads the paper account and PositionView shape from the typed client", async ({
	page,
}) => {
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

	// The data source is explicit: fixture mode is labelled, never presented as live.
	await expect(page.locator(".page-actions .badge", { hasText: "FIXTURE" })).toBeVisible();

	// Cash/equity/realized/unrealized header comes from /paper/account. Every number is
	// PAPER-badged; the values are formatted, not recomputed.
	const account = page.locator('section.kpi-band[aria-label="Paper account"]');
	const stat = (label: string) =>
		account.locator(".stat").filter({ hasText: new RegExp(`^${label}`) });
	await expect(account.locator(".stat")).toHaveCount(6);
	await expect(stat("Cash")).toContainText("$9,709.36");
	await expect(stat("Equity")).toContainText("$9,870.80");
	await expect(stat("Realized")).toContainText("+$12.10");
	await expect(stat("Unrealized")).toContainText("−$129.20");
	await expect(account.locator(".badge", { hasText: "PAPER" })).toHaveCount(6);
	await expect(stat("Open positions")).toContainText("4");
	await expect(stat("Needs attention")).toContainText("2");

	// The positions table is the server PositionView, one row per open position.
	const table = page.locator("section.panel").filter({ hasText: "Server positions" }).locator("table");
	await expect(table.locator("tbody tr")).toHaveCount(4);
	await expect(table.locator('thead th[scope="col"]')).toHaveCount(10);

	const bonk = table.locator("tbody tr").filter({ hasText: "BONK" });
	await expect(bonk).toContainText("solana");
	await expect(bonk.locator("td").nth(4)).toHaveText("$0.00003142");
	await expect(bonk.locator("td").nth(6)).toHaveText("+$19.22");
	await expect(bonk.locator("td").nth(7)).toHaveText("+$18.90");
	await expect(bonk.locator(".badge", { hasText: "jupiter" })).toBeVisible();
	await expect(bonk.locator(".badge", { hasText: "paper" })).toBeVisible();

	// A stale mark is labelled stale; the value stays visible and is not executable.
	const scam = table.locator("tbody tr").filter({ hasText: "SCAM" });
	await expect(scam.locator(".badge", { hasText: "stale" })).toBeVisible();
	await expect(scam.locator("td").nth(4)).toHaveText("$0.00000041");
	await expect(scam.locator("td").nth(6)).toHaveText("−$142.30");

	// Unknown stays unknown: no mark, no value, no zero.
	const mochi = table.locator("tbody tr").filter({ hasText: "MOCHI" });
	await expect(mochi.locator("td").nth(4)).toHaveText("—");
	await expect(mochi.locator("td").nth(5)).toHaveText("—");
	await expect(mochi.locator("td").nth(6)).toHaveText("—");
	await expect(mochi.locator(".badge", { hasText: "unknown mark" })).toBeVisible();

	// Stale states are called out above the table and in the footer.
	await expect(page.getByText(/1 stale mark/)).toBeVisible();
	await expect(
		page.getByText("EZCHAIN_WEB_MODE=fixture · sample account, no API call"),
	).toBeVisible();

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("positions page renders empty, error and stale states without fabricating numbers", async ({
	page,
}) => {
	await page.goto("/positions?state=empty");
	await expect(page.getByText("No open positions")).toBeVisible();
	await expect(page.locator(".tile")).toHaveCount(0);
	await expect(page.locator('section.kpi-band[aria-label="Paper account"]')).toContainText(
		"$10,000.00",
	);

	await page.goto("/positions?state=error");
	const alert = page.locator('section[role="alert"]');
	await expect(alert).toContainText("Paper data unavailable");
	await expect(alert).toContainText("FIXTURE_DEMO_ERROR");
	await expect(page.locator(".tile")).toHaveCount(0);
	const account = page.locator('section.kpi-band[aria-label="Paper account"]');
	for (const label of ["Cash", "Equity", "Realized", "Unrealized"]) {
		const stat = account
			.locator(".stat")
			.filter({ hasText: new RegExp(`^${label}`) });
		await expect(stat.locator(".value")).toHaveText("—");
	}
	await expect(page.getByRole("link", { name: "Retry" })).toHaveAttribute("href", "/positions");

	await page.goto("/positions?state=stale");
	await expect(page.getByText(/3 stale marks/)).toBeVisible();
	await expect(
		account.locator(".stat").filter({ hasText: /^Needs attention/ }),
	).toContainText("4");
});

test("positions page screenshot at mobile width", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/positions");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
	expect(scrollWidth, "page must not scroll sideways on a 390px viewport").toBeLessThanOrEqual(
		390,
	);
	await page.screenshot({ path: ".shots/positions-390.png", fullPage: true });
});
