import { expect, test } from "./test";

// GH-123: token and position pages render candle charts from the server series.
// Fixture mode is the deterministic series labelled FIXTURE. These tests assert
// the chart exists with axes in the accessibility tree, that a missing series is
// an explicit empty state (never a fabricated chart), that the safety object is
// rendered straight from the data layer with "—" for every unknown field, and
// that fixture mode is labelled FIXTURE everywhere the chart appears.

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const SCAM_MINT = "Scam1111111111111111111111111111111111111111";

test("token page renders the fixture candle chart with axes and a FIXTURE badge", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);

	const chart = page.locator("#chart");
	await expect(chart.getByRole("heading", { name: /candle/i })).toBeVisible();
	// The SVG carries a text alternative; the axes are text, so they are reachable.
	await expect(
		chart.getByRole("img", { name: /OHLC candles.*BONK/i }),
	).toBeVisible();
	await expect(chart.locator("svg [class~='axis-text']")).not.toHaveCount(0);
	// OHLC candles and a volume pane are both drawn.
	await expect(chart.locator("svg .candle-up")).not.toHaveCount(0);
	await expect(chart.locator("svg .vol")).not.toHaveCount(0);
	// Source and age of the series are shown; fixture mode is labelled FIXTURE.
	await expect(chart).toContainText("FIXTURE");
	await expect(chart).toContainText(/Fixture · observed \d+s ago/);
	await expect(chart).toContainText("5m");
	await expect(chart).not.toContainText("No candle series");
});

test("token page timeframe control re-requests the series and preserves the quote query", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?side=sell&amount=250`);

	const chart = page.locator("#chart");
	const nav = chart.getByRole("navigation", { name: "Candle timeframe" });
	await expect(nav).toBeVisible();

	// The panel offers four timeframes and names the active one without colour alone.
	await expect(nav.getByRole("link")).toHaveText(["5m", "1h", "4h", "1d"]);
	await expect(nav.getByRole("link", { name: "5m" })).toHaveAttribute("aria-current", "page");
	await expect(chart.locator(".panel-head .sub")).toContainText("closed OHLCV · 5m");
	await expect(chart.getByRole("img", { name: /OHLC candles.*5m/i })).toBeVisible();
	// 5m is the default, so its link omits the parameter but keeps the quote query.
	await expect(nav.getByRole("link", { name: "5m" })).toHaveAttribute(
		"href",
		`/tokens/solana/${BONK_MINT}?side=sell&amount=250`,
	);

	// A switch is a server round trip: the URL carries the timeframe and the series follows.
	await nav.getByRole("link", { name: "4h" }).click();
	await expect(page).toHaveURL(/timeframe=4h/);
	await expect(page).toHaveURL(/side=sell/);
	await expect(page).toHaveURL(/amount=250/);
	await expect(chart.locator(".panel-head .sub")).toContainText("closed OHLCV · 4h");
	await expect(chart.getByRole("img", { name: /OHLC candles.*4h/i })).toBeVisible();
	await expect(nav.getByRole("link", { name: "4h" })).toHaveAttribute("aria-current", "page");
	await expect(nav.getByRole("link", { name: "5m" })).not.toHaveAttribute(
		"aria-current",
		"page",
	);
});

test("an unknown timeframe falls back to the 5m series", async ({ page }) => {
	// 1m and 15m are valid API timeframes but are not offered on this panel, so they fall
	// back with every other unknown value rather than leaving the control without an active
	// item.
	for (const query of ["timeframe=2h", "timeframe=nonsense", "timeframe=1m"]) {
		await page.goto(`/tokens/solana/${BONK_MINT}?${query}`);
		const chart = page.locator("#chart");
		await expect(chart.locator(".panel-head .sub"), query).toContainText(
			"closed OHLCV · 5m",
		);
		await expect(
			chart.getByRole("link", { name: "5m" }),
			query,
		).toHaveAttribute("aria-current", "page");
		await expect(
			chart.getByRole("img", { name: /OHLC candles.*5m/i }),
			query,
		).toBeVisible();
	}
});

test("the chosen timeframe survives the quote side switch and the amount form", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?timeframe=4h`);

	await page
		.getByRole("group", { name: "Quote side" })
		.getByRole("link", { name: "Sell" })
		.click();
	await expect(page).toHaveURL(/side=sell/);
	await expect(page).toHaveURL(/timeframe=4h/);
	await expect(page.locator("#chart .panel-head .sub")).toContainText("closed OHLCV · 4h");

	await expect(page.locator('#quote form input[name="timeframe"]')).toHaveValue("4h");
});

test("a token with no candle series shows the explicit empty state", async ({ page }) => {
	await page.goto(`/tokens/solana/${SCAM_MINT}`);

	const chart = page.locator("#chart");
	await expect(chart).toContainText("No candle series");
	await expect(chart).toContainText("CANDLES_EMPTY");
	await expect(chart.locator("svg")).toHaveCount(0);
});

test("the safety object renders every unknown field as an em dash", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);

	const safety = page.locator("#safety");
	await expect(safety.getByRole("row", { name: /Mint authority/ })).toContainText("—");
	await expect(safety.getByRole("row", { name: /Freeze authority/ })).toContainText("—");
	await expect(safety.getByRole("row", { name: /LP custody/ })).toContainText("—");
	await expect(safety.getByRole("row", { name: /Creator history/ })).toContainText("—");
	// The data layer's reason is still printed; unknown is never promoted to a pass.
	await expect(safety).toContainText("SAFETY_NOT_IMPLEMENTED");
	await expect(safety).not.toContainText("revoked");
});

// The Trade screen is one chart-and-ticket surface. The chart carries every
// overlay the earlier token panel split apart, plus draggable exit lines.
const TRADE_URL = `/trade?chain=solana&token=${BONK_MINT}`;

test("trade screen chart draws candles, volume, both EMAs, the ATH and trade markers", async ({
	page,
}) => {
	await page.route("**/api/v1/paper/orders", async (route) => {
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "ord_chart_buy",
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: "100",
				status: "filled",
				reason: null,
				quote_kind: "jupiter",
				created_at: "2026-11-09T10:00:00Z",
				filled_at: "2026-11-09T10:00:01Z",
			}),
		});
	});
	await page.goto(TRADE_URL);

	const screen = page.getByTestId("trade-screen");
	await expect(screen).toBeVisible();
	const chart = page.getByTestId("trade-chart");
	await expect(chart).toBeVisible();
	await expect(chart).toHaveAttribute("role", "img");

	// Candles and the volume pane are drawn from the server series.
	await expect(chart.locator(".candle-up")).not.toHaveCount(0);
	await expect(chart.locator(".vol")).not.toHaveCount(0);

	// The four indicators are on by default and named, not colour-coded alone.
	for (const testid of ["chart-ema20", "chart-ema50", "chart-ath"]) {
		await expect(page.getByTestId(testid)).toBeVisible();
	}
	await expect(page.getByTestId("chart-ohlc")).toContainText("O");
	await expect(page.getByTestId("chart-ohlc")).toContainText("H");
	await expect(page.getByTestId("chart-ohlc")).toContainText("L");
	await expect(page.getByTestId("chart-ohlc")).toContainText("C");

	// Markers are real paper fills, not decoration: one appears only after the hold lands.
	await expect(page.getByTestId("chart-marker-buy")).toHaveCount(0);
	const hold = page.getByTestId("hold-submit");
	const box = (await hold.boundingBox())!;
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(750);
	await page.mouse.up();
	await expect(page.getByTestId("chart-marker-buy")).not.toHaveCount(0);
});

test("trade chart switches between candles, line and area and hides indicators", async ({
	page,
}) => {
	await page.goto(TRADE_URL);

	const chart = page.getByTestId("trade-chart");
	await expect(chart.locator(".candle-up")).not.toHaveCount(0);

	await page.getByTestId("chart-type-line").click();
	await expect(page.getByTestId("chart-line")).toBeVisible();
	await expect(chart.locator(".candle-up")).toHaveCount(0);

	await page.getByTestId("chart-type-area").click();
	await expect(page.getByTestId("chart-area")).toBeVisible();

	await page.getByTestId("chart-toggle-ema20").click();
	await expect(page.getByTestId("chart-ema20")).toHaveCount(0);
	await page.getByTestId("chart-type-candles").click();
	await expect(chart.locator(".candle-up")).not.toHaveCount(0);
});

test("trade chart exit lines can be dragged and moved with the keyboard", async ({ page }) => {
	await page.goto(TRADE_URL);

	const stop = page.getByTestId("exit-line-stop");
	await expect(stop).toHaveAttribute("role", "slider");
	const before = await page.getByTestId("exit-stop-input").inputValue();

	// Dragging the grip up raises the stop and is reflected in the panel.
	const grip = page.getByTestId("exit-grip-stop");
	const box = (await grip.boundingBox())!;
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 24, { steps: 5 });
	await page.mouse.up();
	const afterDrag = await page.getByTestId("exit-stop-input").inputValue();
	expect(Number(afterDrag)).toBeGreaterThan(Number(before));

	// Keyboard adjustment keeps the line usable without a pointer. The stop is capped
	// just below entry, so nudge it down after the drag raised it to the cap.
	await stop.focus();
	await stop.press("ArrowDown");
	const afterKey = await page.getByTestId("exit-stop-input").inputValue();
	expect(Number(afterKey)).toBeLessThan(Number(afterDrag));
});

test("positions page renders a candle chart per position with an empty state for SCAM", async ({
	page,
}) => {
	await page.goto("/positions");

	const candles = page.locator("#candles");
	await expect(candles).toContainText("Candle history");
	await expect(candles.getByRole("img", { name: /OHLC candles.*BONK/i })).toBeVisible();
	await expect(candles).toContainText("FIXTURE");
	// The SCAM position has no series: its block is the explicit empty state.
	const scam = candles.locator("[data-candle-block]").filter({ hasText: "SCAM" });
	await expect(scam).toContainText("No candle series");
	await expect(scam.locator("svg")).toHaveCount(0);
});
