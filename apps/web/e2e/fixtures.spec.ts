import { expect, test } from "@playwright/test";
import { fixtureCandles } from "../lib/candle-fixtures";
import { activity, attribution, totals } from "../lib/dashboard-fixtures";
import { marketRows } from "../lib/markets-fixtures";
import {
	safetyChecks,
	safetyStatusLabel,
	safetyTone,
} from "../lib/safety-view";
import { strategyParamFields } from "../lib/strategy-fixtures";
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

test("DCA form vocabulary matches the server validator", () => {
	const dcaFields = strategyParamFields.filter((field) => field.kinds.includes("dca"));
	expect(dcaFields.map((field) => field.name).sort()).toEqual([
		"interval_minutes",
		"max_position_usd",
		"order_usd",
	]);
	expect(dcaFields.some((field) => field.name === "take_profit_pct")).toBe(false);
	expect(dcaFields.some((field) => field.name === "stop_loss_pct")).toBe(false);

	const dipFields = strategyParamFields
		.filter((field) => field.kinds.includes("dip"))
		.map((field) => field.name)
		.sort();
	expect(dipFields).toEqual([
		"entry_change_pct",
		"interval_minutes",
		"lookback",
		"max_position_usd",
		"order_usd",
		"stop_loss_pct",
		"take_profit_pct",
	]);
});

test("fixture candles are closed bars; unknown volume is null, never zero", () => {
	const BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
	const now = Date.now();
	const series = fixtureCandles("solana", BONK, "1h", 24);
	expect(series.candles).toHaveLength(24);
	expect(series.reason).toBeNull();
	expect(series.stale).toBe(false);
	for (const candle of series.candles) {
		expect(Date.parse(candle.close_time)).toBeLessThanOrEqual(now);
		expect(Number(candle.close)).toBeGreaterThan(0);
		expect(candle.volume === null || Number(candle.volume) > 0).toBe(true);
	}
	expect(series.candles.some((candle) => candle.volume === null)).toBe(true);

	const noPool = fixtureCandles(
		"solana",
		"Scam1111111111111111111111111111111111111111",
		"1h",
		24,
	);
	expect(noPool.candles).toEqual([]);
	expect(noPool.reason).toBe("NO_POOL");

	expect(() => fixtureCandles("solana", BONK, "1h", 24, "unsupported")).toThrow(
		/CANDLES_UNSUPPORTED_CHAIN/,
	);
});

test("safety view keeps absent fields unknown and maps reported pass and warn", () => {
	expect(safetyStatusLabel(undefined)).toBe("unknown");
	expect(safetyTone(undefined)).toBe("warn");
	expect(safetyStatusLabel({ status: "pass" })).toBe("pass");
	expect(safetyTone("pass")).toBe("ok");
	expect(safetyTone("warn")).toBe("warn");
	expect(safetyTone("fail")).toBe("err");
	expect(safetyChecks(undefined)).toEqual([]);

	const checks = safetyChecks({
		checks: [
			{ id: "mint", name: "Mint authority", status: "pass", reason: "renounced" },
			{ check: "owner", result: "warn" },
		],
	});
	expect(checks).toEqual([
		{ key: "mint", label: "Mint authority", status: "pass", reason: "renounced" },
		{ key: "owner", label: "owner", status: "warn", reason: null },
	]);

	const keyed = safetyChecks({
		checks: { freeze: { status: "warn", detail: "review" }, custody: "unknown" },
	});
	expect(keyed).toEqual([
		{ key: "freeze", label: "freeze", status: "warn", reason: "review" },
		{ key: "custody", label: "custody", status: "unknown", reason: null },
	]);
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
