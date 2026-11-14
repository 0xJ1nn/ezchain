import { expect, test } from "./test";
import { PROXY_TEST_TOKEN } from "./proxy-env";

async function csvText(downloadPath: string): Promise<string> {
	const fs = await import("node:fs/promises");
	return fs.readFile(downloadPath, "utf8");
}

test("reports export the fetched rows as CSV in the browser", async ({ page }) => {
	await page.goto("/reports");
	await expect(page.getByRole("heading", { name: "Reports", level: 1 })).toBeVisible();

	const orders = page.waitForEvent("download");
	await page.getByRole("button", { name: "Export orders CSV" }).click();
	const ordersDownload = await orders;
	expect(ordersDownload.suggestedFilename()).toMatch(
		/^ezchain-paper-orders-\d{4}-\d{2}-\d{2}T.*\.csv$/,
	);
	const ordersPath = await ordersDownload.path();
	expect(ordersPath).not.toBeNull();
	const ordersCsv = await csvText(ordersPath as string);
	expect(ordersCsv.split("\r\n")[0]).toBe(
		"order_id,created_at,filled_at,chain,token_address,side,amount_usd,status,reason,quote_kind",
	);
	expect(ordersCsv).toContain("paper-ord-1041");
	expect(ordersCsv).toContain("NO_SELL_ROUTE");
	await expect(page.getByRole("status")).toContainText("Exported");

	const positions = page.waitForEvent("download");
	await page.getByRole("button", { name: "Export positions CSV" }).click();
	const positionsDownload = await positions;
	const positionsPath = await positionsDownload.path();
	expect(positionsPath).not.toBeNull();
	const positionsCsv = await csvText(positionsPath as string);
	expect(positionsCsv.split("\r\n")[0]).toBe(
		"chain,token_address,symbol,quantity,avg_cost_usd,mark_usd,market_value_usd,unrealized_usd,realized_usd,mark_source,mark_observed_at",
	);
	expect(positionsCsv).toContain("BONK");
	expect(positionsCsv).toContain("MOCHI");
});

test("the kill switch arms with a reason and shows the persisted response", async ({ page }) => {
	let posted: { active?: boolean; reason?: string } | null = null;
	await page.route("**/api/v1/system/kill-switch", async (route) => {
		const request = route.request();
		if (request.method() !== "POST") {
			await route.continue();
			return;
		}
		posted = request.postDataJSON() as { active?: boolean; reason?: string };
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				active: true,
				reason: posted.reason,
				updated_at: "2026-10-23T12:00:00Z",
				scope: "global",
				scopes: ["global", "strategy"],
			}),
		});
	});

	await page.goto("/risk");
	await expect(page.getByRole("heading", { name: "Risk", level: 1 })).toBeVisible();
	await expect(page.locator("#main").getByText("RELEASED", { exact: true })).toBeVisible();

	await page.getByRole("button", { name: "Arm kill switch" }).click();
	await page.getByLabel("Reason for the operator log").fill("e2e arm: verify control");
	await page.getByRole("button", { name: "Confirm arm" }).click();

	await expect(page.locator("#main").getByText("ARMED", { exact: true })).toBeVisible();
	await expect(page.getByRole("status")).toContainText("Armed");
	expect(posted).toEqual({ active: true, reason: "e2e arm: verify control" });
});

// The dashboard card and this page describe one flag. Both must name the levels the API
// reported and say what each gates, and both must separate arming from the exits that
// still settle — otherwise the two surfaces tell a reader two different stories.
test("the risk page states the same switch scope wording as the dashboard", async ({ page }) => {
	await page.goto("/risk");

	const panel = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Kill switch" }) });
	await expect(panel).toBeVisible();
	await expect(panel).toContainText("global and strategy");
	await expect(panel).toContainText("every new entry");
	await expect(panel).toContainText("strategy-attributed intents");
	await expect(panel).toContainText("Manual paper orders are entries");
	await expect(panel).toContainText("KILL_SWITCH_ARMED");
	await expect(panel).toContainText("KILL_SWITCH_ACTIVE");
	await expect(panel).toContainText("reduce-only sells still settle");
	// The old page named exposure and drawdown under the same heading. It does not claim a
	// deployment-wide risk verdict, so "Risk OPEN/HALTED" has no home here either.
	await expect(panel.getByText("HALTED", { exact: true })).toHaveCount(0);

	const dashboard = await page.context().newPage();
	await dashboard.goto("/");
	const card = dashboard.getByTestId("dashboard-kill-switch");
	await expect(card).toContainText("global and strategy levels");
	await expect(card).toContainText("every new entry");
	await expect(card).toContainText("strategy-attributed intents");
	await expect(card).toContainText("Manual paper orders are entries");
	await dashboard.close();
});

test("a rejected kill-switch mutation keeps the previous state and shows the API reason", async ({
	page,
}) => {
	await page.route("**/api/v1/system/kill-switch", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		await route.fulfill({
			status: 401,
			contentType: "application/json",
			body: JSON.stringify({
				detail: {
					code: "MUTATION_FORBIDDEN",
					message: "mutation token rejected",
					retryable: false,
				},
			}),
		});
	});

	await page.goto("/risk");
	await page.getByRole("button", { name: "Arm kill switch" }).click();
	await page.getByLabel("Reason for the operator log").fill("e2e error state");
	await page.getByRole("button", { name: "Confirm arm" }).click();

	await expect(page.getByRole("alert").filter({ hasText: "MUTATION_FORBIDDEN" })).toContainText(
		"MUTATION_FORBIDDEN: mutation token rejected",
	);
	await expect(page.locator("#main").getByText("RELEASED", { exact: true })).toBeVisible();
	await expect(page.locator("#main").getByText("ARMED", { exact: true })).toHaveCount(0);
});

test("the ledger shows truthful empty and error states", async ({ page }) => {
	await page.goto("/ledger?state=empty");
	await expect(page.getByRole("heading", { name: "Activity", level: 1 })).toBeVisible();
	await expect(page.getByText("Nothing has been ordered yet")).toBeVisible();
	await expect(page.getByText("?state=empty")).toBeVisible();

	await page.goto("/ledger?state=error");
	await expect(page.getByText("Activity unavailable")).toBeVisible();
	await expect(page.getByText("FIXTURE_DEMO_ERROR").first()).toBeVisible();
	await expect(page.getByText("No rows are substituted.").first()).toBeVisible();
});

test("the ledger pages its orders and fills from the URL", async ({ page }) => {
	await page.goto("/ledger");
	await expect(page.getByRole("navigation", { name: "Order pages" })).toContainText("Page 1");
	await expect(page.getByRole("navigation", { name: "Fill pages" })).toContainText("Page 1");

	// Page two is a real offset-100 read, labelled with the page it is, not a rerender of
	// page one under another number.
	await page.goto("/ledger?orders=2&fills=2");
	await expect(page.getByRole("navigation", { name: "Order pages" })).toContainText("Page 2");
	await expect(page.getByRole("navigation", { name: "Fill pages" })).toContainText("Page 2");
	await expect(page.getByText(/page 2 · \d+ rows · API/)).toHaveCount(2);
	await expect(
		page.getByRole("navigation", { name: "Order pages" }).getByRole("link", { name: "← Prev" }),
	).toHaveAttribute("href", "/ledger?fills=2");
});

test("the performance error state does not substitute fixture KPIs", async ({ page }) => {
	await page.goto("/performance?state=error");
	await expect(page.getByRole("heading", { name: "Performance", level: 1 })).toBeVisible();
	await expect(page.getByText("Performance data unavailable")).toBeVisible();
	await expect(page.getByText("FIXTURE_DEMO_ERROR").first()).toBeVisible();
});

test("wallets and performance withhold equity instead of excluding an unknown mark", async ({
	page,
}) => {
	await page.goto("/wallets");
	const balances = page.locator('section.kpi-band[aria-label="Paper balances"]');
	await expect(
		balances.locator(".stat").filter({ hasText: /^Paper equity/ }).locator(".value"),
	).toHaveText("—");
	await expect(page.getByText(/equity unavailable — 1 open mark unknown/i).first()).toBeVisible();
	await expect(page.getByText(/unknown mark excluded/i)).toHaveCount(0);

	await page.goto("/performance");
	const performance = page.locator('section.kpi-band[aria-label="Paper account performance"]');
	await expect(
		performance.locator(".stat").filter({ hasText: /^Equity/ }).locator(".value"),
	).toHaveText("—");
	await expect(page.getByText(/equity unavailable — 1 open mark unknown/i).first()).toBeVisible();
	await expect(page.getByText(/unknown mark excluded/i)).toHaveCount(0);
});

test("risk labels stale and fresh marks from the same server rows", async ({ page }) => {
	await page.goto("/risk");

	const table = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Mark source" }) });
	// SCAM was observed 45s ago and the server says stale: the configured mark budget,
	// not a client-side 60s rule, decides. A page that re-derived freshness from
	// `observed_at` would call this row fresh.
	const scam = table.locator("tbody tr").filter({ hasText: "SCAM" });
	await expect(scam.locator(".badge")).toHaveText("stale · dexscreener");
	const bonk = table.locator("tbody tr").filter({ hasText: "BONK" });
	await expect(bonk.locator(".badge")).toHaveText("fresh · jupiter");
	const mochi = table.locator("tbody tr").filter({ hasText: "MOCHI" });
	await expect(mochi.locator(".badge")).toHaveText("unknown");

	const risk = page.locator('section.kpi-band[aria-label="Paper risk inputs"]');
	await expect(
		risk.locator(".stat").filter({ hasText: /^Stale marks/ }).locator(".value"),
	).toHaveText("1");
	await expect(
		risk.locator(".stat").filter({ hasText: /^Unknown marks/ }).locator(".value"),
	).toHaveText("1");
});

test("risk and workspace label withheld equity with its reason", async ({ page }) => {
	await page.goto("/risk");
	const risk = page.locator('section.kpi-band[aria-label="Paper risk inputs"]');
	await expect(risk.locator(".stat").filter({ hasText: /^Equity/ }).locator(".value")).toHaveText(
		"—",
	);
	await expect(
		risk.locator(".stat").filter({ hasText: /^Equity/ }).locator(".meta"),
	).toHaveText(/equity unavailable — 1 open mark unknown/i);

	await page.goto("/workspace");
	const workspace = page.locator('section.kpi-band[aria-label="Workspace paper account"]');
	await expect(
		workspace.locator(".stat").filter({ hasText: /^Paper equity/ }).locator(".value"),
	).toHaveText("—");
	await expect(
		workspace.locator(".stat").filter({ hasText: /^Paper equity/ }).locator(".meta"),
	).toHaveText(/equity unavailable — 1 open mark unknown/i);
	await expect(page.getByText(/No partial total is substituted/i)).toBeVisible();
});


test("system pages never print the API token", async ({ page }) => {
	for (const route of ["/settings", "/security", "/developer", "/operations"]) {
		await page.goto(route);
		const html = await page.content();
		expect(html, `${route} leaked the deployment token`).not.toContain(PROXY_TEST_TOKEN);
	}
});
