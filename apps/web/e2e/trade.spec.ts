import path from "node:path";
import { expect, test } from "./test";

// The Trade screen is one surface: chart lines and the exits panel edit the same
// numbers, the ticket sends only after a full hold, a paper fill lands a stamp,
// and exits arm through the protection API in fixture mode.

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const SHOT_DIR = process.env.EZCHAIN_SHOT_DIR ?? ".shots";

async function priceOf(page: import("@playwright/test").Page, key: string): Promise<number> {
	const raw = await page.getByTestId(`order-line-${key}`).getAttribute("data-price");
	if (raw === null) throw new Error(`missing data-price on ${key}`);
	return Number(raw);
}

async function drag(page: import("@playwright/test").Page, key: string, dy: number, hold = false) {
	const hit = page.getByTestId(`order-line-${key}`).locator("line").nth(1);
	const box = await hit.boundingBox();
	if (!box) throw new Error(`no hit line for ${key}`);
	const x = box.x + box.width * 0.55;
	const y = box.y + box.height / 2;
	await page.mouse.move(x, y);
	await page.mouse.down();
	await page.mouse.move(x, y + dy, { steps: 10 });
	if (hold) return;
	await page.mouse.up();
}

test("dragging the stop keeps it below entry and targets above entry", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.getByTestId("trade-workspace")).toBeVisible();

	const entry = await priceOf(page, "entry");
	const before = await priceOf(page, "sl");
	expect(before).toBeLessThan(entry);

	// A long upward drag must clamp just under entry, never cross it.
	await drag(page, "sl", -260);
	const after = await priceOf(page, "sl");
	expect(after).toBeLessThan(entry);
	expect(after).toBeGreaterThanOrEqual(entry * 0.999 - 1e-9);
	const input = await page.getByTestId("stop-input").inputValue();
	expect(Number(input.replace(/,/g, ""))).toBeLessThan(entry);

	// A target dragged down must clamp just above entry.
	await drag(page, "tp0", 260);
	expect(await priceOf(page, "tp0")).toBeGreaterThan(entry);
	expect(await priceOf(page, "tp1")).toBeGreaterThan(entry);
	expect(await priceOf(page, "tp2")).toBeGreaterThan(entry);
});

test("a short hold sends nothing; a full 650 ms hold sends exactly one paper order", async ({ page }) => {
	let posts = 0;
	await page.route("**/api/v1/paper/orders", async (route) => {
		posts += 1;
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "ord_hold_01",
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: "100",
				status: "filled",
				reason: null,
				quote_kind: "jupiter",
				created_at: "2026-11-17T10:00:00Z",
				filled_at: "2026-11-17T10:00:01Z",
			}),
		});
	});
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const submit = page.getByTestId("trade-submit");
	await expect(submit).toBeEnabled();
	await submit.scrollIntoViewIfNeeded();
	await expect(submit).toHaveAttribute("data-hold-ms", "650");
	const box = await submit.boundingBox();
	if (!box) throw new Error("no submit box");

	// Release early: the fill drains and nothing is sent.
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(250);
	await page.mouse.up();
	await page.waitForTimeout(120);
	expect(posts, "a release before 650 ms must not send").toBe(0);

	// Hold past the threshold: one order reaches the API.
	await page.mouse.down();
	await page.waitForTimeout(780);
	expect(posts, "one completed hold sends one order").toBe(1);
	await page.mouse.up();
});

test("a paper fill lands the PAPER FILLED stamp and a receipt", async ({ page }) => {
	await page.route("**/api/v1/paper/orders", async (route) => {
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "ord_stamp_01",
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: "100",
				status: "filled",
				reason: null,
				quote_kind: "jupiter",
				created_at: "2026-11-17T10:00:00Z",
				filled_at: "2026-11-17T10:00:01Z",
			}),
		});
	});
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const submit = page.getByTestId("trade-submit");
	await submit.scrollIntoViewIfNeeded();
	const box = await submit.boundingBox();
	if (!box) throw new Error("no submit box");
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(780);
	await page.mouse.up();

	await expect(page.getByTestId("fill-stamp")).toContainText("PAPER FILLED");
	await page.evaluate(() => document.querySelector("#ticket")?.scrollIntoView({ block: "center" }));
	await page.screenshot({ path: path.join(SHOT_DIR, "trade-stamp-1440.png") });
	await expect(page.getByTestId("fill-receipt")).toContainText("Paper-filled");
	await expect(page.getByTestId("fill-receipt")).toContainText("ord_stamp_01");
	await expect(page.locator("#ticket")).not.toContainText("Executed");
});

test("exits arm through the protection API in fixture mode, with the gaps named", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.getByTestId("exits-panel")).toBeVisible();
	await expect(page.getByTestId("exits-panel")).toContainText("not available");

	const arm = page.getByTestId("exits-save");
	await expect(arm).toHaveAttribute("data-hold-ms", "650");
	await arm.scrollIntoViewIfNeeded();
	const box = await arm.boundingBox();
	if (!box) throw new Error("no exits arm box");
	const cx = box.x + box.width / 2;
	const cy = box.y + box.height / 2;

	// A release before the threshold arms nothing.
	await page.mouse.move(cx, cy);
	await page.mouse.down();
	await page.waitForTimeout(250);
	await page.mouse.up();
	await page.waitForTimeout(100);
	await expect(page.getByTestId("exits-status")).toHaveCount(0);

	// A full hold arms the three exits the API can express.
	await page.mouse.down();
	await page.waitForTimeout(780);
	await page.mouse.up();
	await expect(page.getByTestId("exits-status")).toContainText("Armed 3 exits on paper");

	const arms = await page.evaluate(() =>
		JSON.parse(sessionStorage.getItem("ezchain-fixture-protection-arms") ?? "[]") as Array<{
			kind: string;
			params: Record<string, unknown>;
		}>,
	);
	expect(arms.map((arm) => arm.kind).sort()).toEqual([
		"stop_loss",
		"take_profit",
		"trailing_stop",
	]);
	const stop = arms.find((arm) => arm.kind === "stop_loss");
	const target = arms.find((arm) => arm.kind === "take_profit");
	const trail = arms.find((arm) => arm.kind === "trailing_stop");
	expect(typeof stop?.params.price_usd).toBe("string");
	expect(target?.params.levels).toMatchObject([
		{ fraction: "0.4" },
		{ fraction: "0.35" },
		{ fraction: "0.25" },
	]);
	expect(typeof trail?.params.trailing_pct).toBe("string");
});

test("the header carries the coin, chain, venue, contract copy and profile tags", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.getByTestId("trade-workspace")).toBeVisible();
	await expect(page.locator("h1")).toContainText("Bonk");
	await expect(page.locator("h1").locator("..")).toContainText("BONK · Solana");
	await expect(page.getByTestId("contract-copy")).toContainText("DezXAZ…B263");
	await expect(page.getByTestId("contract-copy")).toHaveAttribute(
		"title",
		"Copy the contract address",
	);
	await expect(page.locator("h1").locator("..").locator("..")).toContainText("Solana");
});

test("order pills use compact labels when the chart is under 680 px wide", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.getByTestId("trade-workspace")).toBeVisible();
	const wrap = page.locator("[data-compact]");
	await expect(wrap).toHaveAttribute("data-compact", "false");
	const wide = await page.getByTestId("order-line-tp0").locator("text").textContent();
	expect(wide, "a wide chart shows the change from entry and the USD value").toContain("$");

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(wrap).toHaveAttribute("data-compact", "true");
	const narrow = await page.getByTestId("order-line-tp0").locator("text").textContent();
	expect(narrow, "a narrow chart shows only the label and price").not.toContain("$");
	expect(narrow).toContain("TP1 · 40%");
});

test("screenshots: the Trade screen in dark and light, and a stop mid-drag", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.getByTestId("trade-workspace")).toBeVisible();

	async function setTheme(theme: "dark" | "light") {
		if ((await page.locator("html").getAttribute("data-theme")) !== theme) {
			await page.getByTestId("theme-toggle").click();
		}
		await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
		await page.waitForTimeout(250);
	}

	await setTheme("dark");
	await page.screenshot({ path: path.join(SHOT_DIR, "trade-dark-1440.png") });

	await setTheme("light");
	await page.screenshot({ path: path.join(SHOT_DIR, "trade-light-1440.png") });

	await drag(page, "sl", -40, true);
	await page.screenshot({ path: path.join(SHOT_DIR, "trade-stop-drag-1440.png") });
	await page.mouse.up();
});
