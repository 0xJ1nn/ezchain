import { expect, test } from "./test";

test("performance page renders the server series without inventing points", async ({ page }) => {
	await page.route("**/api/v1/paper/performance*", async (route) => {
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				mode: "paper",
				as_of: "2026-10-22T12:00:00Z",
				cash_usd: "100",
				equity_usd: "110",
				realized_usd: "10",
				unrealized_usd: "0",
				fees_usd: "1",
				trades: 2,
				wins: 1,
				losses: 1,
				win_rate_pct: "50",
				open_positions: 0,
				complete: true,
				marks_unknown: false,
				by_token: [],
				daily_realized: [],
				window: "all",
				source: "ledger",
				reason: null,
				equity_series: [
					{ t: "2026-10-22T12:00:00Z", timestamp: "2026-10-22T12:00:00Z", value: "100", equity_usd: "100" },
					{ t: "2026-10-22T12:05:00Z", timestamp: "2026-10-22T12:05:00Z", value: "110", equity_usd: "110" },
				],
				realized_pnl_series: [
					{ t: "2026-10-22T12:00:00Z", timestamp: "2026-10-22T12:00:00Z", value: "0", realized_pnl_usd: "0" },
					{ t: "2026-10-22T12:05:00Z", timestamp: "2026-10-22T12:05:00Z", value: "10", realized_pnl_usd: "10" },
				],
				drawdown_series: [
					{ t: "2026-10-22T12:00:00Z", timestamp: "2026-10-22T12:00:00Z", value: "0", drawdown_pct: "0" },
					{ t: "2026-10-22T12:05:00Z", timestamp: "2026-10-22T12:05:00Z", value: "0", drawdown_pct: "0" },
				],
				series: { equity: [], realized_pnl: [], drawdown: [] },
			}),
		});
	});
	await page.goto("/performance");
	await expect(page.getByRole("heading", { name: "Performance", level: 1 })).toBeVisible();
});
