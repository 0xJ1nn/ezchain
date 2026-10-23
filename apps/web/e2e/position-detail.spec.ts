import { expect, test } from "@playwright/test";

const BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const BONK_PATH = `/positions/solana/${BONK}`;
const AERO = "0x940181a94A35A4569E4529A3CDfB74e38FD98631";
const MOCHI = "7atgF8KQo4wJrD5ATGX7t1V2zVvykPJbFfNeVf1icFv1";
const SCAM = "5z3EqYQo9HiCEs3R84RCDMu2n7anpDMxR3T7dRntZ0bV";

test("a position route renders one position, its facts, chart and timeframe control", async ({
	page,
}) => {
	await page.goto(`${BONK_PATH}?view=grid&chain=solana&sort=value`);

	await expect(page.getByRole("heading", { name: /BONK/, level: 1 })).toBeVisible();
	await expect(page.locator("h1")).toContainText("solana");
	const facts = page.locator('section.kpi-band[aria-label="Position facts"]');
	await expect(facts).toContainText("2,000,000");
	await expect(facts).toContainText("$0.00002181");
	await expect(facts).toContainText("$0.00003142");
	await expect(facts).toContainText("$62.84");
	await expect(facts).toContainText("+$19.22");

	// Source and age are explicit; the full address is not truncated.
	await expect(page.getByText("Mark source")).toBeVisible();
	await expect(page.getByText("jupiter", { exact: true }).first()).toBeVisible();
	await expect(page.locator(`text=${BONK}`).first()).toBeVisible();

	// The chart comes from the candle endpoint and shows closed bars only.
	const chart = page.locator("section.panel").filter({ hasText: "Price history" }).first();
	await expect(chart.locator("svg[role='img']")).toBeVisible();
	await expect(chart).toContainText("closed 1h bars");
	await expect(chart.locator("summary")).toContainText("closed bars as a table");
	await expect(chart.getByRole("navigation", { name: "Candle timeframe" })).toBeVisible();

	// Timeframe control re-requests the series and preserves the back-view query.
	await chart.getByRole("link", { name: "4h" }).click();
	await expect(page).toHaveURL(/timeframe=4h/);
	await expect(page).toHaveURL(/view=grid/);
	await expect(chart.locator(".panel-head .sub")).toContainText("closed 4h bars");
	await expect(page.getByRole("link", { name: "4h" })).toHaveAttribute(
		"aria-current",
		"page",
	);

	// Back navigation preserves the list view and filters.
	await expect(page.getByRole("link", { name: "Back to positions" }).first()).toHaveAttribute(
		"href",
		"/positions?view=grid&chain=solana&sort=value",
	);

	// Protection is a reported gap: the positions API has no protection field, so the page
	// never claims a rule exists or is absent.
	await expect(
		page.getByText("Protection status not reported by the positions API"),
	).toBeVisible();
	await expect(page.getByRole("button", { name: /set|stop|protect/i })).toHaveCount(0);

	// A manual exit is a real link to the paper sell ticket, and the ticket resolves.
	await expect(page.getByRole("link", { name: "Paper sell ticket" })).toHaveAttribute(
		"href",
		`/tokens/solana/${BONK}?side=sell`,
	);
	const ticket = await page.request.get(`/tokens/solana/${BONK}?side=sell`);
	expect(ticket.status()).toBe(200);

	await page.screenshot({ path: ".shots/position-detail-1440.png", fullPage: true });
});

test("an unknown position is a 404", async ({ page }) => {
	for (const route of [
		"/positions/solana/nope",
		`/positions/ethereum/${BONK}`,
		"/positions/solana",
	]) {
		const response = await page.goto(route);
		expect(response?.status(), route).toBe(404);
	}
	await expect(page.getByRole("heading", { name: "Not found", level: 1 })).toBeVisible();
});

test("candle reason states are named, not drawn as a flat line", async ({ page }) => {
	const cases = [
		{ query: "unsupported", code: "CANDLES_UNSUPPORTED_CHAIN" },
		{ query: "no_pool", code: "NO_POOL" },
		{ query: "empty", code: "CANDLES_EMPTY" },
		{ query: "error", code: "UPSTREAM_UNREACHABLE" },
	] as const;

	for (const entry of cases) {
		await page.goto(`${BONK_PATH}?candles=${entry.query}`);
		const panel = page
			.locator("section.panel")
			.filter({ hasText: "Price history" })
			.first();
		await expect(panel.locator(".badge", { hasText: entry.code })).toBeVisible();
		await expect(panel.locator("svg")).toHaveCount(0);
		await expect(panel).toContainText(/no chart|not a flat line|no closed bars/i);
	}

	await page.goto(`${BONK_PATH}?candles=stale`);
	const stalePanel = page
		.locator("section.panel")
		.filter({ hasText: "Price history" })
		.first();
	await expect(stalePanel.locator(".badge", { hasText: "STALE SERIES" })).toBeVisible();
	await expect(stalePanel.locator("svg[role='img']")).toBeVisible();
});

test("a position without candle history shows the API reason", async ({ page }) => {
	// MOCHI's fixture position address has no fixture token, so the typed client returns
	// CANDLES_NOT_FOUND. The page prints that reason; it does not invent bars.
	await page.goto("/positions/solana/7atgF8KQo4wJrD5ATGX7t1V2zVvykPJbFfNeVf1icFv1");
	await expect(page.getByRole("heading", { name: /MOCHI/, level: 1 })).toBeVisible();
	const panel = page.locator("section.panel").filter({ hasText: "Price history" }).first();
	await expect(panel.locator(".badge", { hasText: "CANDLES_NOT_FOUND" })).toBeVisible();
	await expect(panel.locator("svg")).toHaveCount(0);

	// No mark means no value and no PnL. Both stay unknown, never zero, and neither gets
	// the green "ok" tone that a known number would carry.
	const facts = page.locator('section.kpi-band[aria-label="Position facts"]');
	const fact = (label: string) =>
		facts.locator(".stat").filter({ hasText: new RegExp(`^${label}`) });
	for (const label of ["Mark", "Value", "Unrealized"]) {
		await expect(fact(label).locator(".value")).toHaveText("—");
		await expect(fact(label).locator(".meta")).toContainText("unknown · not zero");
		await expect(fact(label).locator(".value")).toHaveClass(/warn/);
		await expect(fact(label).locator(".value")).not.toHaveClass(/\bok\b/);
	}
});

test("every fixture position offers a resolving sell ticket or states why it cannot", async ({
	page,
}) => {
	test.setTimeout(180_000);
	const cases = [
		{
			symbol: "BONK",
			path: BONK_PATH,
			sell: `/tokens/solana/${BONK}?side=sell`,
		},
		{
			symbol: "AERO",
			path: `/positions/base/${AERO}`,
			sell: `/tokens/base/${AERO}?side=sell`,
		},
		{ symbol: "MOCHI", path: `/positions/solana/${MOCHI}`, sell: null },
		{ symbol: "SCAM", path: `/positions/solana/${SCAM}`, sell: null },
	];

	for (const entry of cases) {
		await page.goto(entry.path);
		await expect(
			page.getByRole("heading", { name: new RegExp(entry.symbol), level: 1 }),
		).toBeVisible();

		if (entry.sell) {
			const ticket = page.getByRole("link", { name: "Paper sell ticket" });
			await expect(ticket).toHaveAttribute("href", entry.sell);
			const response = await page.request.get(entry.sell);
			expect(response.status(), `${entry.symbol} sell ticket must resolve`).toBe(200);
		} else {
			// A token absent from the registry must not get a primary button that 404s.
			await expect(page.getByText(/Sell unavailable for this token/i)).toBeVisible();
			await expect(page.getByText(/not in the market registry/i)).toBeVisible();
			await expect(page.getByRole("link", { name: "Paper sell ticket" })).toHaveCount(0);
		}
	}
});

test("position detail does not overflow at 390, 768 and 1440", async ({ page }) => {
	for (const width of [390, 768, 1440]) {
		await page.setViewportSize({ width, height: 900 });
		await page.goto(BONK_PATH);
		await expect(page.getByRole("heading", { name: /BONK/, level: 1 })).toBeVisible();
		const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(scrollWidth, `detail at ${width}px must not scroll sideways`).toBeLessThanOrEqual(
			width,
		);
	}
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(BONK_PATH);
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await page.screenshot({ path: ".shots/position-detail-390.png", fullPage: true });
});
