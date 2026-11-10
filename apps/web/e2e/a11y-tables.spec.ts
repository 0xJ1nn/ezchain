import { expect, test, type Page } from "./test";

const DOCUMENT = {
	schema: "ezchain.graph/1",
	nodes: [
		{ id: "tick", type: "event.tick", params: {}, ui: { x: 0, y: 0 } },
		{ id: "c1", type: "data.candles", params: { timeframe: "5m" }, ui: { x: 0, y: 160 } },
		{ id: "fast", type: "ind.ema", params: { period: 9 }, ui: { x: 280, y: 120 } },
		{ id: "slow", type: "ind.ema", params: { period: 21 }, ui: { x: 280, y: 300 } },
		{ id: "x", type: "logic.cross_above", params: {}, ui: { x: 560, y: 200 } },
		{ id: "br", type: "flow.branch", params: {}, ui: { x: 840, y: 60 } },
		{ id: "buy", type: "act.buy", params: { amount_usd: "100" }, ui: { x: 1120, y: 40 } },
	],
	edges: [
		{ from: { node: "tick", port: "out" }, to: { node: "br", port: "in" } },
		{ from: { node: "c1", port: "close" }, to: { node: "fast", port: "series" } },
		{ from: { node: "c1", port: "close" }, to: { node: "slow", port: "series" } },
		{ from: { node: "fast", port: "series" }, to: { node: "x", port: "a" } },
		{ from: { node: "slow", port: "series" }, to: { node: "x", port: "b" } },
		{ from: { node: "x", port: "out" }, to: { node: "br", port: "cond" } },
		{ from: { node: "br", port: "true" }, to: { node: "buy", port: "in" } },
	],
	comments: [{ id: "k1", text: "Entry", ui: { x: -40, y: -80, w: 400, h: 300 } }],
};

const TOKEN = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";

async function pasteDocument(page: Page) {
	await page.getByTestId("paste-json").click();
	await page.getByTestId("json-text").fill(JSON.stringify(DOCUMENT, null, 2));
	await page.getByTestId("apply-paste").click();
	await expect(page.getByTestId("json-text")).toHaveCount(0);
}

const CASES = [
	{ route: "/ledger", label: "Orders table", caption: "Orders", headers: 8 },
	{ route: "/ledger", label: "Fills table", caption: "Fills", headers: 10 },
	{ route: "/markets", label: "Markets table", caption: "Markets", headers: 10 },
	{ route: "/positions", label: "Server positions table", caption: "Server positions", headers: 11 },
];

for (const c of CASES) {
	test(`${c.label} is keyboard focusable with a name and scoped headers`, async ({ page }) => {
		await page.goto(c.route);
		const wrapper = page.getByRole("region", { name: c.label });
		await expect(wrapper).toBeVisible();
		await wrapper.focus();
		await expect(wrapper).toBeFocused();
		await expect(wrapper).toHaveAttribute("tabindex", "0");
		await expect(wrapper.locator("caption")).toHaveText(c.caption);
		await expect(wrapper.locator("thead th")).toHaveCount(c.headers);
		await expect(wrapper.locator('thead th[scope="col"]')).toHaveCount(c.headers);
		await page.keyboard.press("Tab");
		await page.keyboard.press("Shift+Tab");
		await expect(wrapper).toBeFocused();
	});
}

const STRATEGY_CASES = [
	{ route: "/strategies", label: "Decision log table", caption: "Decision log", headers: 5 },
	{ route: "/automations", label: "Scheduled strategies table", caption: "Scheduled strategies", headers: 7 },
	{ route: "/automations", label: "Automation vs strategy table", caption: "Automation vs strategy", headers: 2 },
	{ route: "/strategies/str-bonk-momentum", label: "Runs table", caption: "Runs", headers: 4 },
];

for (const c of STRATEGY_CASES) {
	test(`${c.label} is keyboard focusable with a name and scoped headers`, async ({ page }) => {
		await page.goto(c.route);
		const wrapper = page.getByRole("region", { name: c.label });
		await expect(wrapper).toBeVisible();
		await wrapper.focus();
		await expect(wrapper).toBeFocused();
		await expect(wrapper).toHaveAttribute("tabindex", "0");
		await expect(wrapper.locator("caption")).toHaveText(c.caption);
		await expect(wrapper.locator("thead th")).toHaveCount(c.headers);
		await expect(wrapper.locator('thead th[scope="col"]')).toHaveCount(c.headers);
	});
}

const EXTRA_CASES = [
	{ route: "/strategies/str-bonk-momentum", label: "Parameters table", caption: "Parameters", headers: 0 },
];

for (const c of EXTRA_CASES) {
	test(`${c.label} is keyboard focusable with a name and scoped headers`, async ({ page }) => {
		await page.goto(c.route);
		const wrapper = page.getByRole("region", { name: c.label });
		await expect(wrapper).toBeVisible();
		await wrapper.focus();
		await expect(wrapper).toBeFocused();
		await expect(wrapper).toHaveAttribute("tabindex", "0");
		await expect(wrapper.locator("caption")).toHaveText(c.caption);
		if (c.headers > 0) {
			await expect(wrapper.locator("thead th")).toHaveCount(c.headers);
			await expect(wrapper.locator('thead th[scope="col"]')).toHaveCount(c.headers);
		}
	});
}

test("Effects and Trace tables are keyboard focusable with a name and scoped headers", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page);
	await page.locator("#graph-token").fill(TOKEN);
	await page.getByTestId("dry-run").click();
	await expect(page.getByTestId("dry-run-result")).toBeVisible();

	for (const c of [
		{ label: "Effects table", caption: "Effects", headers: 4 },
		{ label: "Trace table", caption: "Trace", headers: 3 },
	]) {
		const wrapper = page.getByRole("region", { name: c.label });
		await expect(wrapper).toBeVisible();
		await wrapper.focus();
		await expect(wrapper).toBeFocused();
		await expect(wrapper).toHaveAttribute("tabindex", "0");
		await expect(wrapper.locator("caption")).toHaveText(c.caption);
		await expect(wrapper.locator("thead th")).toHaveCount(c.headers);
		await expect(wrapper.locator('thead th[scope="col"]')).toHaveCount(c.headers);
	}
});

test("Trades table is keyboard focusable with a name and scoped headers", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page);
	await page.locator("#graph-token").fill(TOKEN);
	await page.getByTestId("backtest").click();
	await expect(page.getByTestId("backtest-result")).toBeVisible();

	const wrapper = page.getByRole("region", { name: "Trades table" });
	await expect(wrapper).toBeVisible();
	await wrapper.focus();
	await expect(wrapper).toBeFocused();
	await expect(wrapper).toHaveAttribute("tabindex", "0");
	await expect(wrapper.locator("caption")).toHaveText("Trades");
	await expect(wrapper.locator("thead th")).toHaveCount(7);
	await expect(wrapper.locator('thead th[scope="col"]')).toHaveCount(7);
});
