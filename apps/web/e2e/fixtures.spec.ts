import { expect, test } from "@playwright/test";
import { activity, attribution, totals } from "../lib/dashboard-fixtures";
import { marketRows } from "../lib/markets-fixtures";
import { tokenPages } from "../lib/token-fixtures";

const num = (value: string) => Number(value.replace("\u2212", "-").replace(/[$,%+]/g, ""));

test("dashboard fixtures reconcile before they reach the UI", () => {
	// The four open-position PnLs must sum to the KPI. The wireframe sample numbers did not;
	// this is the regression guard for CONTRACT I10.
	expect(totals.unrealizedUsd).toBeCloseTo(-129.2, 2);

	// Attribution net PnL must equal realized PnL, and shares must sum to 100%.
	const net = attribution.reduce((sum, row) => sum + num(row.netPnl), 0);
	expect(net).toBeCloseTo(totals.realizedUsd, 2);
	const shares = attribution.reduce((sum, row) => sum + num(row.share), 0);
	expect(shares).toBeCloseTo(100, 1);

	// Exposure is a risk-limit number that includes wallet balances; it must not be
	// presented as the sum of the four open positions.
	expect(Math.round((totals.exposureUsd / totals.exposureCapUsd) * 100)).toBe(38);
	expect(totals.openPositionValueUsd).toBeCloseTo(220.5041, 4);
});

test("cross-page BONK constants agree", () => {
	const market = marketRows.find((row) => row.id === "bonk");
	const token = tokenPages["solana/bonk"];

	expect(market?.price).toBe("0.00003142");
	expect(token?.mark).toBe("$0.00003142");
	expect(token?.myPosition.cells.find((cell) => cell.k === "PnL")?.v).toBe("+42.6%");
	expect(token?.myPosition.cells.find((cell) => cell.k === "Equity")?.v).toBe("$127.60");
	expect(token?.myPosition.cells.find((cell) => cell.k === "Equity")?.sub).toBe(
		"banked $46.20",
	);
	expect(activity.find((row) => row.action === "SELL")?.title).toBe(
		"1,137,000 BONK → 46.20 USDC",
	);
});
