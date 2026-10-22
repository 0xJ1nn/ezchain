import { expect, test } from "@playwright/test";
import { attribution, totals } from "../lib/dashboard-fixtures";

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
