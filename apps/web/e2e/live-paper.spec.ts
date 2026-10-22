import { expect, test } from "@playwright/test";

// Live PAPER end-to-end. This spec is deliberately excluded from the fixture suite and
// run by `tools/verify.sh --live` against a disposable PAPER stack. It fails on a
// placeholder route, a dead navigation item or a paper flow that cannot complete.

const BASE_URL = process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000";
const CHAIN = process.env.EZCHAIN_LIVE_E2E_CHAIN ?? "base";
const TOKEN = process.env.EZCHAIN_LIVE_E2E_TOKEN ?? "0x4200000000000000000000000000000000000006";
const SYMBOL = process.env.EZCHAIN_LIVE_E2E_SYMBOL ?? "WETH";


test("every navigation route is a real surface", async ({ page }) => {
	await page.goto("/");
	const routes = await page
		.locator('nav.sidebar a[href^="/"]')
		.evaluateAll((links) => [...new Set(links.map((link) => link.getAttribute("href") ?? ""))]);
	expect(routes.length, "sidebar must expose real routes").toBeGreaterThan(10);

	for (const route of routes) {
		const response = await page.goto(route);
		expect(response?.status(), `${route} status`).toBeLessThan(400);
		await expect(page.locator("main h1").first(), `${route} has an h1`).toBeVisible();
		const body = (await page.locator("body").innerText()).toLowerCase();
		for (const forbidden of [
			"not implemented yet",
			"this route is part of the wireframe",
			"nothing is pretending to work here",
		]) {
			expect(body, `${route} must not contain "${forbidden}"`).not.toContain(forbidden);
		}
		expect(body, `${route} must not expose NaN`).not.toMatch(/\bnan\b/);
		expect(body, `${route} must not expose undefined`).not.toMatch(/\bundefined\b/);
	}
});

test("live markets and token page render real data", async ({ page }) => {
	await page.goto(`/markets?chain=${encodeURIComponent(CHAIN)}`);
	await expect(page.getByRole("heading", { name: "Markets", level: 1 })).toBeVisible();
	await expect(page.getByText("Fixture preview")).toHaveCount(0);

	await page.goto(`/tokens/${CHAIN}/${TOKEN}`);
	await expect(page.locator("main h1").first()).toBeVisible();
	await expect(page.getByText("FIXTURE", { exact: true })).toHaveCount(0);
	await expect(page.locator("#quote")).toBeVisible();
});

test("paper buy and sell complete through the UI", async ({ page }) => {
	await page.goto(`/tokens/${CHAIN}/${TOKEN}`);
	const ticket = page.locator("#ticket");
	const amount = ticket.getByLabel("Paper amount in USD");
	await expect(amount).toBeVisible();
	await amount.fill("1");
	await ticket.getByRole("button", { name: "Preview quote" }).click();

	const confirm = ticket.getByRole("button", { name: "Confirm paper order" });
	await expect(confirm).toBeEnabled({ timeout: 30_000 });
	await confirm.click();

	await expect(page.getByText("PAPER FILL")).toBeVisible({ timeout: 30_000 });
	await expect(page.locator("#ticket")).toContainText(SYMBOL);

	// The position is server-derived; the UI must show it without recomputing PnL.
	await page.goto("/positions");
	await expect(page.getByRole("heading", { name: "Positions", level: 1 })).toBeVisible();
	await expect(page.locator("main")).toContainText(SYMBOL);

	// Sell the full position by asking for more USD than the position is worth; the
	// server clamps a sell to the held quantity (never oversells).
	await page.goto(`/tokens/${CHAIN}/${TOKEN}?side=sell&amount=1000000`);
	const sellTicket = page.locator("#ticket");
	await expect(sellTicket.getByRole("button", { name: "Confirm paper order" })).toBeEnabled({
		timeout: 30_000,
	});
	await sellTicket.getByRole("button", { name: "Confirm paper order" }).click();
	await expect(page.getByText("PAPER FILL")).toBeVisible({ timeout: 30_000 });

	// Reconciliation is a first-class read, not a hidden script.
	const reconciliation = await page.request.get(`${BASE_URL}/api/v1/paper/reconciliation`);
	expect(reconciliation.status()).toBe(200);
	const body = await reconciliation.json();
	expect(body.ok).toBe(true);
});

test("live graph catalogue and builder load from the API", async ({ page, request }) => {
	const catalogue = await request.get(`${BASE_URL}/api/v1/graph/catalogue`);
	expect(catalogue.status()).toBe(200);
	const body = await catalogue.json();
	expect(body.schema).toBe("ezchain.graph/1");
	expect(Array.isArray(body.node_types)).toBe(true);
	expect(body.node_types.length).toBeGreaterThan(10);

	await page.goto("/strategies/builder");
	await expect(page.getByRole("heading", { name: "Visual strategy builder", level: 1 })).toBeVisible();
	await expect(page.getByText("Loading the catalogue…")).toHaveCount(0, { timeout: 30_000 });
	await expect(page.getByText("Every tick").first()).toBeVisible();
	await expect(page.getByText("EMA").first()).toBeVisible();
});
