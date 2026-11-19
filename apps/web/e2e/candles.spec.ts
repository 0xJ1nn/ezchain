import { expect, test } from "./test";

// The Trade screen draws candles, volume, EMA 20 and 50 and the all-time high from
// the server series. Fixture mode is the deterministic series labelled FIXTURE.
// A missing series is an explicit empty state, never a fabricated chart. The
// safety object is rendered straight from the data layer with "—" for unknowns.

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const SCAM_MINT = "Scam1111111111111111111111111111111111111111";

test("the trade chart renders the fixture series with axes, volume and a FIXTURE badge", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);

	const chart = page.locator("#chart");
	await expect(
		chart.getByRole("img", { name: /OHLC candles.*BONK/i }),
	).toBeVisible();
	// The SVG carries a text alternative; the axes are text, so they are reachable.
	await expect(chart.locator("svg text")).not.toHaveCount(0);
	// OHLC candles and a volume pane are both drawn, with EMA 20 and EMA 50.
	await expect(chart.locator("svg [class*='candles'] rect")).not.toHaveCount(0);
	await expect(chart.locator("svg [class*='vol'] rect")).not.toHaveCount(0);
	await expect(chart.locator("svg [class*='ema']")).toHaveCount(2);
	// Source and age of the series are shown; fixture mode is labelled FIXTURE.
	await expect(page.locator("body")).toContainText("FIXTURE");
	await expect(page.locator("body")).toContainText(/Fixture · observed \d+s ago/);
	await expect(page.locator("[class*='ctool']")).toContainText("1h");
	await expect(chart).not.toContainText("No candle series");
});

test("the timeframe control re-requests the series and preserves the quote query", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?side=sell&amount=250`);

	const nav = page.getByRole("navigation", { name: "Candle timeframe" });
	await expect(nav).toBeVisible();
	await expect(nav.getByRole("link")).toHaveText(["1m", "5m", "15m", "1h", "4h", "1d"]);
	await expect(nav.getByRole("link", { name: "1h" })).toHaveAttribute("aria-current", "page");
	await expect(page.locator("#chart svg")).toHaveAttribute("aria-label", /1h/);

	// 1h is the default, so its link omits the parameter but keeps the quote query.
	await expect(nav.getByRole("link", { name: "1h" })).toHaveAttribute(
		"href",
		`/tokens/solana/${BONK_MINT}?side=sell&amount=250`,
	);

	// A switch is a server round trip: the URL carries the timeframe and the series follows.
	await nav.getByRole("link", { name: "4h" }).click();
	await expect(page).toHaveURL(/timeframe=4h/);
	await expect(page).toHaveURL(/side=sell/);
	await expect(page).toHaveURL(/amount=250/);
	await expect(page.locator("#chart svg")).toHaveAttribute("aria-label", /4h/);
	await expect(nav.getByRole("link", { name: "4h" })).toHaveAttribute("aria-current", "page");
	await expect(nav.getByRole("link", { name: "1h" })).not.toHaveAttribute("aria-current", "page");
});

test("an unknown timeframe falls back to the 1h series", async ({ page }) => {
	for (const query of ["timeframe=2h", "timeframe=nonsense", "timeframe=30s"]) {
		await page.goto(`/tokens/solana/${BONK_MINT}?${query}`);
		await expect(
			page.getByRole("navigation", { name: "Candle timeframe" }).getByRole("link", { name: "1h" }),
			query,
		).toHaveAttribute("aria-current", "page");
		await expect(page.locator("#chart svg"), query).toHaveAttribute("aria-label", /1h/);
	}
});

test("the chosen timeframe survives a side switch in the ticket", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?timeframe=4h`);
	await page.getByTestId("ticket-side-sell").click();
	await expect(page).toHaveURL(/side=sell/);
	await expect(page).toHaveURL(/timeframe=4h/);
	await expect(page.locator("#chart svg")).toHaveAttribute("aria-label", /4h/);
});

test("a token with no candle series shows the explicit empty state", async ({ page }) => {
	await page.goto(`/tokens/solana/${SCAM_MINT}`);
	const chart = page.locator("#chart");
	await expect(chart).toContainText("No candle series");
	await expect(chart).toContainText("nothing is plotted from a failed or empty read");
	await expect(chart.locator("svg")).toHaveCount(0);
});

test("the safety object renders every unknown field as an em dash", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);

	const safety = page.locator("#safety");
	await expect(safety.getByRole("row", { name: /Mint authority/ })).toContainText("—");
	await expect(safety.getByRole("row", { name: /Freeze authority/ })).toContainText("—");
	await expect(safety.getByRole("row", { name: /LP custody/ })).toContainText("—");
	await expect(safety.getByRole("row", { name: /Creator history/ })).toContainText("—");
	await expect(safety).toContainText("SAFETY_NOT_IMPLEMENTED");
	await expect(safety).not.toContainText("revoked");
});

test("positions page still renders a candle chart per position with an empty state for SCAM", async ({
	page,
}) => {
	await page.goto("/positions");

	const candles = page.locator("#candles");
	await expect(candles).toContainText("Candle history");
	await expect(candles.getByRole("img", { name: /OHLC candles.*BONK/i })).toBeVisible();
	await expect(candles).toContainText("FIXTURE");
	const scam = candles.locator("[data-candle-block]").filter({ hasText: "SCAM" });
	await expect(scam).toContainText("No candle series");
	await expect(scam.locator("svg")).toHaveCount(0);
});
