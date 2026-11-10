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
