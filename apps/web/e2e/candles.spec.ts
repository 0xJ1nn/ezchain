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
