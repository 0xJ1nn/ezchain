import { expect, test } from "@playwright/test";

const SYMBOLS = ["BONK", "AERO", "MOCHI", "SCAM"];

test("list, grid and detailed render the same server rows behind a URL view switch", async ({
	page,
}) => {
	await page.goto("/positions");
	await expect(page.getByRole("heading", { name: "Positions", level: 1 })).toBeVisible();

	const switcher = page.getByRole("navigation", { name: "Position view" });
	await expect(switcher.getByRole("link", { name: "List" })).toHaveAttribute(
		"aria-current",
		"page",
	);

	const list = page.locator('[data-view="list"]');
	await expect(list.locator("tbody tr")).toHaveCount(4);
	await expect(list.locator("tbody tr td:first-child .strong")).toHaveText(SYMBOLS);
	const listKeys = await list
		.locator("[data-position]")
		.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-position")));
	await expect(list.locator("tbody tr").filter({ hasText: "BONK" })).toContainText(
		"$0.00003142",
	);
	await expect(list.locator("tbody tr").filter({ hasText: "MOCHI" })).toContainText("—");

	// Grid is a real mode over the same rows: every card carries quantity, mark, value,
	// unrealized, source/age and a link to the position route.
	await switcher.getByRole("link", { name: "Grid" }).click();
	await expect(page).toHaveURL(/view=grid/);
	await expect(switcher.getByRole("link", { name: "Grid" })).toHaveAttribute(
		"aria-current",
		"page",
	);
	const grid = page.locator('[data-view="grid"]');
	await expect(grid.locator("article")).toHaveCount(4);
	expect(
		await grid
			.locator("[data-position]")
			.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-position"))),
	).toEqual(listKeys);
	await expect(grid.locator("article").filter({ hasText: "BONK" })).toContainText("$0.00003142");
	await expect(grid.locator("article").filter({ hasText: "BONK" })).toContainText("$62.84");
	await expect(grid.locator("article").filter({ hasText: "BONK" })).toContainText("+$19.22");
	await expect(grid.locator("article").filter({ hasText: "SCAM" })).toContainText("stale");
	await expect(grid.locator("article").filter({ hasText: "MOCHI" })).toContainText(
		"unknown mark",
	);
	await expect(grid.getByRole("link", { name: /Open position/ })).toHaveCount(4);
	await page.screenshot({ path: ".shots/positions-grid-1440.png", fullPage: true });

	// Detailed adds the inline chart and the read-only protection summary.
	await switcher.getByRole("link", { name: "Detailed" }).click();
	await expect(page).toHaveURL(/view=detailed/);
	const detailed = page.locator('[data-view="detailed"]');
	await expect(detailed.locator("article")).toHaveCount(4);
	expect(
		await detailed
			.locator("[data-position]")
			.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-position"))),
	).toEqual(listKeys);
	await expect(detailed.locator("article").filter({ hasText: "BONK" }).getByRole("img")).toBeVisible();
	await expect(detailed.locator("article").filter({ hasText: "BONK" })).toContainText(
		"Protection status not reported by the positions API",
	);
	// Missing history is a reason code, never a flat line. Both fixture rows use addresses
	// that have no fixture candle series.
	for (const symbol of ["SCAM", "MOCHI"]) {
		await expect(
			detailed.locator("article").filter({ hasText: symbol }),
		).toContainText("CANDLES_NOT_FOUND");
	}
	await expect(detailed.getByRole("link", { name: /Open full position/ })).toHaveCount(4);
	await page.screenshot({ path: ".shots/positions-detailed-1440.png", fullPage: true });

	// The detail link carries the current view so back navigation preserves it.
	await detailed
		.locator("article")
		.filter({ hasText: "BONK" })
		.getByRole("link", { name: /Open full position/ })
		.click();
	await expect(page).toHaveURL(/\/positions\/solana\/DezXAZ.*\?view=detailed$/);
	await expect(page.getByRole("link", { name: "Back to positions" }).first()).toHaveAttribute(
		"href",
		"/positions?view=detailed",
	);
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
	// MOCHI has no mark, so the server withholds equity and unrealized. Neither is a
	// partial sum and neither ever becomes zero.
	await expect(stat("Equity").locator(".value")).toHaveText("—");
	await expect(stat("Equity")).toContainText("equity unavailable — 1 open mark unknown");
	await expect(stat("Realized")).toContainText("+$12.10");
	await expect(stat("Unrealized").locator(".value")).toHaveText("—");
	await expect(stat("Unrealized")).toContainText("unrealized unavailable — 1 open mark unknown");
	await expect(page.getByText(/unknown mark excluded/i)).toHaveCount(0);
	await expect(account.locator(".badge", { hasText: "PAPER" })).toHaveCount(6);
	await expect(stat("Open positions")).toContainText("4");
	await expect(stat("Needs attention")).toContainText("2");

	// The positions table is the server PositionView, one row per open position.
	const table = page.locator('[data-view="list"]');
	await expect(table.locator("tbody tr")).toHaveCount(4);
	await expect(table.locator('thead th[scope="col"]')).toHaveCount(11);

	const bonk = table.locator("tbody tr").filter({ hasText: "BONK" });
	await expect(bonk).toContainText("solana");
	await expect(bonk.locator("td").nth(4)).toHaveText("$0.00003142");
	await expect(bonk.locator("td").nth(6)).toHaveText("+$19.22");
	await expect(bonk.locator("td").nth(7)).toHaveText("+$18.90");
	await expect(bonk.locator(".badge", { hasText: "jupiter" })).toBeVisible();
	await expect(bonk.locator(".badge", { hasText: "paper" })).toBeVisible();
	await expect(bonk.getByRole("link", { name: "Open BONK position" })).toHaveAttribute(
		"href",
		/^\/positions\/solana\/DezXAZ/,
	);

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

test("chain filter and sort stay in the URL and preserve the view", async ({ page }) => {
	await page.goto("/positions?view=grid");
	await page.locator('select[name="chain"]').selectOption("solana");
	await page.locator('select[name="sort"]').selectOption("value");
	await page.getByRole("button", { name: "Apply" }).click();

	await expect(page).toHaveURL(/view=grid/);
	await expect(page).toHaveURL(/chain=solana/);
	await expect(page).toHaveURL(/sort=value/);
	const grid = page.locator('[data-view="grid"]');
	await expect(grid.locator("article")).toHaveCount(3);
	await expect(grid.locator("article").filter({ hasText: "AERO" })).toHaveCount(0);

	// View switching keeps the filter and sort.
	await page.getByRole("navigation", { name: "Position view" }).getByRole("link", { name: "List" }).click();
	await expect(page).toHaveURL(/view=list/);
	await expect(page).toHaveURL(/chain=solana/);
	await expect(page).toHaveURL(/sort=value/);
	await expect(page.locator('[data-view="list"] tbody tr')).toHaveCount(3);

	// A filter with no match is a clear state with a reset, not an empty table.
	await page.goto("/positions?view=list&chain=base&sort=symbol");
	await page.locator('select[name="chain"]').selectOption("all");
	await page.getByRole("button", { name: "Apply" }).click();
	await expect(page.locator('[data-view="list"] tbody tr')).toHaveCount(4);
});

test("positions page renders empty, error and stale states without fabricating numbers", async ({
	page,
}) => {
	await page.goto("/positions?state=empty");
	await expect(page.getByText("No open positions")).toBeVisible();
	await expect(page.locator("[data-view]")).toHaveCount(0);
	await expect(page.locator('section.kpi-band[aria-label="Paper account"]')).toContainText(
		"$10,000.00",
	);

	const account = page.locator('section.kpi-band[aria-label="Paper account"]');
	const stat = (label: string) =>
		account.locator(".stat").filter({ hasText: new RegExp(`^${label}`) });
	const panelSub = page
		.locator("section.panel")
		.filter({ hasText: "Server positions" })
		.locator(".panel-head .sub");

	await page.goto("/positions?state=error");
	const alert = page.locator('section[role="alert"]');
	await expect(alert).toContainText("Paper data unavailable");
	await expect(alert).toContainText("FIXTURE_DEMO_ERROR");
	await expect(page.locator("[data-view]")).toHaveCount(0);
	for (const label of ["Cash", "Equity", "Realized", "Unrealized"]) {
		await expect(stat(label).locator(".value")).toHaveText("—");
	}
	// An unavailable account is not green, even though its value is an em dash.
	for (const label of ["Realized", "Unrealized"]) {
		await expect(stat(label).locator(".value")).not.toHaveClass(/\bok\b/);
	}
	// Failed counts are unknown, not zero, and the panel says so.
	await expect(stat("Open positions").locator(".value")).toHaveText("—");
	await expect(stat("Open positions").locator(".meta")).toHaveText("positions unavailable");
	await expect(stat("Needs attention").locator(".value")).toHaveText("—");
	await expect(stat("Needs attention").locator(".meta")).toHaveText("count not read");
	await expect(panelSub).toHaveText("positions unavailable · count not read");
	await expect(page.getByText(/0 from \/api\/v1\/paper\/positions/)).toHaveCount(0);
	await expect(page.getByRole("link", { name: "Retry" })).toHaveAttribute("href", "/positions");

	// A positions-only failure keeps the real account values and still reports the
	// position count as unknown rather than zero.
	await page.goto("/positions?state=positions-error");
	await expect(alert).toContainText("Paper data unavailable");
	await expect(alert).toContainText("FIXTURE_DEMO_ERROR");
	await expect(stat("Cash")).toContainText("$9,709.36");
	await expect(stat("Equity")).toContainText("$9,870.80");
	await expect(stat("Open positions").locator(".value")).toHaveText("—");
	await expect(stat("Open positions").locator(".meta")).toHaveText("positions unavailable");
	await expect(stat("Needs attention").locator(".value")).toHaveText("—");
	await expect(stat("Needs attention").locator(".meta")).toHaveText("count not read");
	await expect(panelSub).toHaveText("positions unavailable · count not read");
	await expect(
		page.getByText("The positions request failed. Nothing is rendered as a position"),
	).toBeVisible();
	await expect(page.locator("[data-view]")).toHaveCount(0);

	await page.goto("/positions?state=stale");
	await expect(page.getByText(/3 stale marks/)).toBeVisible();
	await expect(
		account.locator(".stat").filter({ hasText: /^Needs attention/ }),
	).toContainText("4");
});

test("positions modes do not overflow at 390, 768 and 1440", async ({ page }) => {
	// Nine cold-compiled routes plus three viewport changes: give the navigation loop its
	// own budget instead of borrowing the default per-test timeout.
	test.setTimeout(240_000);
	for (const width of [390, 768, 1440]) {
		await page.setViewportSize({ width, height: 900 });
		for (const view of ["list", "grid", "detailed"]) {
			await page.goto(`/positions?view=${view}`);
			await expect(page.locator(`[data-view="${view}"]`)).toBeVisible();
			const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
			expect(
				scrollWidth,
				`positions ${view} at ${width}px must not scroll sideways`,
			).toBeLessThanOrEqual(width);
		}
	}
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/positions?view=grid");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await page.screenshot({ path: ".shots/positions-grid-390.png", fullPage: true });
});
