import { expect, test } from "@playwright/test";

const WIF = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm";

function card(page: import("@playwright/test").Page, name: string) {
	return page.locator("article").filter({ hasText: name });
}

test("create -> run -> pause a strategy in fixture mode", async ({ page }) => {
	await page.goto("/strategies");

	await expect(page.getByRole("heading", { name: "Strategies", level: 1 })).toBeVisible();
	await expect(page.locator(".page-actions .badge", { hasText: "FIXTURE" })).toBeVisible();
	await expect(page.getByText("4 total · 3 active · 1 paused")).toBeVisible();
	await expect(page.locator("article")).toHaveCount(4);

	const form = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Create strategy" }) });

	// Kind is a declarative choice; momentum is the default and exposes the momentum params.
	await expect(form.getByRole("button", { name: /Momentum/ })).toHaveAttribute(
		"aria-pressed",
		"true",
	);
	await form.getByLabel("Strategy name").fill("WIF momentum");
	await form.getByLabel("Chain").selectOption("solana");
	await form.getByLabel("Symbol").fill("WIF");
	await form.getByLabel("Token address").fill(WIF);
	await form.getByLabel("Order size (USD)").fill("125");
	await form.getByLabel("Take profit (%)").fill("18");
	await form.getByLabel("Stop loss (%)").fill("7");
	await form.getByLabel("Lookback").fill("2h");
	await form.getByLabel("Entry change (%)").fill("4.5");
	await form.getByLabel("Exit change (%)").fill("-2.5");
	await form.getByLabel("Interval (minutes)").fill("30");
	await form.getByLabel("Max position (USD)").fill("600");
	await form.getByRole("button", { name: "Create strategy" }).click();

	await expect(page.getByRole("status")).toContainText("Created “WIF momentum”");
	await expect(page.getByText("5 total · 4 active · 1 paused")).toBeVisible();

	const created = card(page, "WIF momentum");
	await expect(created).toBeVisible();
	await expect(created.locator(".badge", { hasText: "active" })).toBeVisible();
	await expect(created).toContainText("order $125.00");
	await expect(created).toContainText("lookback 2h");
	await expect(created).toContainText("every 30m");

	// Run-now records an engine decision with its reason and order id.
	await created.getByRole("button", { name: "Run now" }).click();
	await expect(created).toContainText("paper order accepted");
	await expect(created.locator(".badge", { hasText: "enter" })).toBeVisible();
	await expect(page.getByText("7 runs · newest first")).toBeVisible();

	const log = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Decision log" }) })
		.locator("table");
	const firstRun = log.locator("tbody tr").first();
	await expect(firstRun).toContainText("WIF momentum");
	await expect(firstRun).toContainText("paper order accepted");
	await expect(firstRun.locator("code")).toContainText("paper-");

	// Pausing is immediate and disables further runs.
	await created.getByRole("button", { name: "Pause" }).click();
	await expect(page.getByRole("status")).toContainText("WIF momentum is now paused");
	await expect(created.locator(".badge", { hasText: "paused" })).toBeVisible();
	await expect(created.getByRole("button", { name: "Run now" })).toBeDisabled();
	await expect(page.getByText("5 total · 3 active · 2 paused")).toBeVisible();

	await page.evaluate(() => window.scrollTo(0, 0));
	await page.screenshot({ path: ".shots/strategies-1440.png", fullPage: true });
});

test("DCA kind hides exit-only fields", async ({ page }) => {
	await page.goto("/strategies");
	const form = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Create strategy" }) });

	await form.getByRole("button", { name: /DCA/ }).click();
	await expect(form.getByLabel("Order size (USD)")).toBeVisible();
	await expect(form.getByLabel("Interval (minutes)")).toBeVisible();
	await expect(form.getByLabel("Max position (USD)")).toBeVisible();
	await expect(form.getByLabel("Take profit (%)")).toHaveCount(0);
	await expect(form.getByLabel("Stop loss (%)")).toHaveCount(0);

	await form.getByRole("button", { name: /Momentum/ }).click();
	await expect(form.getByLabel("Take profit (%)")).toBeVisible();
	await expect(form.getByLabel("Stop loss (%)")).toBeVisible();
	await expect(form.getByLabel("Exit change (%)")).toBeVisible();

	await form.getByRole("button", { name: /Dip/ }).click();
	await expect(form.getByLabel("Take profit (%)")).toBeVisible();
	await expect(form.getByLabel("Stop loss (%)")).toBeVisible();
	await expect(form.getByLabel("Exit change (%)")).toHaveCount(0);
});

test("strategy detail renders params and its run log", async ({ page }) => {
	await page.goto("/strategies/str-bonk-momentum");

	await expect(page.getByRole("heading", { name: "BONK momentum", level: 1 })).toBeVisible();
	await expect(page.locator(".page-actions .badge", { hasText: "active" })).toBeVisible();
	await expect(page.locator(".page-actions .badge", { hasText: "PAPER" })).toBeVisible();
	await expect(page.getByText("momentum:lookback +6.4%")).toBeVisible();

	const paramsTable = page.locator("table").filter({ hasText: "Order size (USD)" });
	await expect(paramsTable).toContainText("$100.00");
	await expect(paramsTable).toContainText("$500.00");
	await expect(paramsTable).toContainText("1h");

	const runsTable = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Runs" }) })
		.locator("table");
	await expect(runsTable.locator("tbody tr")).toHaveCount(2);
	await expect(runsTable).toContainText("paper-ord-1041");

	await page.screenshot({ path: ".shots/strategy-detail-1440.png", fullPage: true });
});

test("delete is a two-step confirmed action", async ({ page }) => {
	await page.goto("/strategies");
	const target = card(page, "BONK range");
	await expect(target).toBeVisible();

	await target.getByRole("button", { name: "Delete" }).click();
	await expect(target.getByRole("button", { name: "Confirm delete" })).toBeVisible();
	await target.getByRole("button", { name: "Confirm delete" }).click();

	await expect(page.getByRole("status")).toContainText("Deleted BONK range");
	await expect(card(page, "BONK range")).toHaveCount(0);
	await expect(page.getByText("3 total · 2 active · 1 paused")).toBeVisible();
});

test("unknown strategy ids return 404", async ({ page }) => {
	const response = await page.goto("/strategies/nope");
	expect(response?.status()).toBe(404);
	await expect(page.getByRole("heading", { name: "Not found", level: 1 })).toBeVisible();
});

test("strategies page has no external requests", async ({ page }) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});
	await page.goto("/strategies");
	await expect(page.locator("article")).toHaveCount(4);
	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("strategies page screenshot at mobile width", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/strategies");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
	expect(scrollWidth, "page must not scroll sideways on a 390px viewport").toBeLessThanOrEqual(
		390,
	);
	await page.screenshot({ path: ".shots/strategies-390.png", fullPage: true });
});
