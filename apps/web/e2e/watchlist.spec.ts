import { expect, test } from "@playwright/test";
import {
	WATCHLIST_STORAGE_KEY,
	isWatchlisted,
	parseWatchlist,
	readWatchlist,
	toggleWatchlistEntry,
	watchlistKey,
	writeWatchlist,
	type WatchlistStorage,
} from "../lib/watchlist";

const BONK = {
	chain: "solana",
	base_token: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
	symbol: "BONK",
	name: "Bonk",
};

interface FakeStorage extends WatchlistStorage {
	value: () => string | null;
}

function fakeStorage(initial: string | null = null, failure?: "read" | "write"): FakeStorage {
	let stored = initial;
	return {
		getItem(key) {
			if (failure === "read") throw new Error("storage is blocked");
			return key === WATCHLIST_STORAGE_KEY ? stored : null;
		},
		setItem(key, value) {
			if (failure === "write") throw new Error("storage is blocked");
			if (key === WATCHLIST_STORAGE_KEY) stored = value;
		},
		value: () => stored,
	};
}

// ---------------------------------------------------------------------------
// Store contract. These run in Node; localStorage is a fake so corrupt or blocked
// storage is exercised without a browser.
// ---------------------------------------------------------------------------

test("watchlist store toggles one namespaced entry and persists it", () => {
	const storage = fakeStorage();
	const added = toggleWatchlistEntry([], BONK);

	expect(added).toHaveLength(1);
	expect(added[0]).toMatchObject({
		chain: "solana",
		base_token: BONK.base_token,
		symbol: "BONK",
		name: "Bonk",
	});
	expect(watchlistKey(added[0].chain, added[0].base_token)).toBe(
		`solana:${BONK.base_token}`,
	);
	expect(isWatchlisted(added, "solana", BONK.base_token)).toBe(true);

	expect(writeWatchlist(added, storage)).toBe(true);
	expect(storage.value()).toContain(BONK.base_token);
	expect(readWatchlist(storage)).toEqual(added);

	const removed = toggleWatchlistEntry(added, BONK);
	expect(removed).toEqual([]);
	expect(writeWatchlist(removed, storage)).toBe(true);
	expect(readWatchlist(storage)).toEqual([]);
	expect(WATCHLIST_STORAGE_KEY).toBe("ezchain.watchlist.v1");
});

test("watchlist keys normalise EVM address case but preserve base58 case", () => {
	const checksummed = "0x940181a94A35A4569E4529A3CDfB74e38FD98631";
	const evm = toggleWatchlistEntry([], {
		chain: "Base",
		base_token: checksummed,
		symbol: "AERO",
	});
	expect(evm[0].chain).toBe("base");
	expect(evm[0].base_token).toBe(checksummed.toLowerCase());
	expect(isWatchlisted(evm, "base", checksummed)).toBe(true);

	const svm = toggleWatchlistEntry([], BONK);
	expect(svm[0].base_token).toBe(BONK.base_token);
	expect(isWatchlisted(svm, "solana", BONK.base_token.toLowerCase())).toBe(false);
});

test("watchlist store rejects corrupt JSON and malformed entries", () => {
	expect(parseWatchlist(null)).toEqual([]);
	expect(parseWatchlist("{not json")).toEqual([]);
	expect(parseWatchlist("null")).toEqual([]);
	expect(parseWatchlist('{"chain":"solana"}')).toEqual([]);
	expect(parseWatchlist(JSON.stringify([null, 42, {}, { chain: "solana" }]))).toEqual([]);

	const entry = {
		...BONK,
		added_at: "2026-01-01T00:00:00.000Z",
	};
	const parsed = parseWatchlist(JSON.stringify([entry, entry]));
	expect(parsed).toHaveLength(1);
	expect(parsed[0].symbol).toBe("BONK");

	// A blocked read is an empty watchlist, never a render failure.
	expect(readWatchlist(fakeStorage(JSON.stringify([entry]), "read"))).toEqual([]);
	// A blocked write reports failure instead of throwing through the click handler.
	expect(writeWatchlist(parsed, fakeStorage(null, "write"))).toBe(false);
});

test("watchlist storage only reads and writes its own namespaced key", () => {
	const reads: string[] = [];
	const writes: string[] = [];
	const storage: WatchlistStorage = {
		getItem(key) {
			reads.push(key);
			return null;
		},
		setItem(key, value) {
			writes.push(`${key}=${value}`);
		},
	};

	readWatchlist(storage);
	writeWatchlist(toggleWatchlistEntry([], BONK), storage);

	expect(reads).toEqual([WATCHLIST_STORAGE_KEY]);
	expect(writes).toHaveLength(1);
	expect(writes[0].startsWith(`${WATCHLIST_STORAGE_KEY}=[`)).toBe(true);
});

// ---------------------------------------------------------------------------
// Browser flow: star on markets, reload, watchlist, remove.
// ---------------------------------------------------------------------------

test("starring a markets row persists and appears on the watchlist", async ({ page }) => {
	await page.goto("/markets");
	const marketTable = page.locator("table").first();
	const bonkRow = marketTable.locator("tbody tr").filter({ hasText: "BONK" });
	const star = bonkRow.getByRole("button");

	await expect(star).toHaveAttribute("aria-pressed", "false");
	await expect(star).toHaveAttribute("aria-label", "Add BONK to watchlist");
	await star.click();
	await expect(star).toHaveAttribute("aria-pressed", "true");
	await expect(star).toHaveAttribute("aria-label", "Remove BONK from watchlist");
	expect(
		await page.evaluate(() => localStorage.getItem("ezchain.watchlist.v1")),
	).toContain("BONK");

	// The star reads localStorage on mount, so a reload keeps the pressed state.
	await page.reload();
	await expect(marketTable.locator("tbody tr").filter({ hasText: "BONK" }).getByRole("button"))
		.toHaveAttribute("aria-pressed", "true");

	await page.getByRole("link", { name: "Watchlist", exact: true }).click();
	await expect(page).toHaveURL(/\/watchlist$/);
	await expect(page.getByRole("heading", { name: "Watchlist", level: 1 })).toBeVisible();
	await expect(page.getByText("BROWSER-LOCAL")).toBeVisible();

	const watchTable = page.locator("table").first();
	const watchRow = watchTable.locator("tbody tr").filter({ hasText: "BONK" });
	await expect(watchRow).toBeVisible();
	await expect(watchRow.getByRole("button")).toHaveAttribute("aria-pressed", "true");
	await expect(watchRow).toContainText("Fixture");

	// The pressed star is the remove action.
	await watchRow.getByRole("button").click();
	await expect(page.getByText("No starred tokens yet")).toBeVisible();
	await expect(page.locator("table")).toHaveCount(0);
	expect(
		JSON.parse(
			(await page.evaluate(() => localStorage.getItem("ezchain.watchlist.v1"))) ?? "[]",
		),
	).toEqual([]);
});

test("starred only narrows the server page and keeps filters in the pager", async ({ page }) => {
	await page.goto("/markets?chain=solana&limit=2");
	const marketTable = page.locator("table").first();
	const wifRow = marketTable.locator("tbody tr").filter({ hasText: "WIF" });
	const wifStar = wifRow.getByRole("button");
	await wifStar.click();
	await expect(wifStar).toHaveAttribute("aria-pressed", "true");

	await page.getByRole("link", { name: "Show starred markets only" }).click();
	await expect(page).toHaveURL(/starred=1/);
	await expect(page).toHaveURL(/chain=solana/);
	await expect(page).toHaveURL(/limit=2/);
	await expect(marketTable.locator("tbody tr")).toHaveCount(1);
	await expect(marketTable.locator("tbody tr")).toContainText("WIF");

	const summary = page.getByTestId("starred-only-summary");
	await expect(summary).toContainText("Showing 1 starred row on this server page");

	// The pager keeps the local filter, so page 2 is still starred-only.
	const next = page.getByRole("link", { name: /Next/ });
	await expect(next).toHaveAttribute("href", /starred=1/);
	await next.click();
	await expect(page).toHaveURL(/offset=2/);
	await expect(summary).toContainText("not on this page");
	await expect(summary).toContainText("WIF");

	await page.getByRole("link", { name: "Open watchlist" }).click();
	await expect(page).toHaveURL(/\/watchlist$/);
	await expect(
		page.locator("table").first().locator("tbody tr").filter({ hasText: "WIF" }),
	).toBeVisible();
});

test("a starred token with no market row is reported, not hidden", async ({ page }) => {
	await page.goto("/markets");
	await page.evaluate(() =>
		localStorage.setItem(
			"ezchain.watchlist.v1",
			JSON.stringify([
				{
					chain: "solana",
					base_token: "MissingToken111111111111111111111111111111111",
					symbol: "GHOST",
					name: "Ghost token",
					added_at: "2026-01-01T00:00:00.000Z",
				},
			]),
		),
	);

	await page.goto("/watchlist");
	const panel = page.getByTestId("watchlist-unavailable");
	await expect(page.getByText("No starred market rows available")).toBeVisible();
	await expect(panel).toBeVisible();
	await expect(panel).toContainText("GHOST");
	await expect(panel).toContainText("MARKET_ROW_MISSING");
	await expect(panel.getByRole("link", { name: "Search markets" })).toHaveAttribute(
		"href",
		/chain=solana/,
	);
	await expect(panel.getByRole("link", { name: "Search markets" })).toHaveAttribute(
		"href",
		/q=MissingToken111/,
	);
	await expect(
		panel.getByRole("button", { name: "Remove GHOST from watchlist" }),
	).toBeVisible();
});

test("malformed watchlist storage falls back to an empty watchlist", async ({ page }) => {
	const problems: string[] = [];
	page.on("pageerror", (error) => problems.push(error.message));

	await page.goto("/markets");
	await page.evaluate(() => localStorage.setItem("ezchain.watchlist.v1", '{"broken":'));
	await page.goto("/watchlist");
	await expect(page.getByText("No starred tokens yet")).toBeVisible();
	await expect(page.getByRole("link", { name: "Browse markets →" })).toBeVisible();

	// A star still writes a valid payload after corrupt input.
	await page.goto("/markets");
	const bonkRow = page.locator("table").first().locator("tbody tr").filter({ hasText: "BONK" });
	await bonkRow.getByRole("button").click();
	const stored = await page.evaluate(() => localStorage.getItem("ezchain.watchlist.v1"));
	expect(JSON.parse(stored ?? "[]")).toHaveLength(1);
	expect(problems).toEqual([]);
});

test("watchlist screenshot at desktop and mobile widths", async ({ page }) => {
	await page.goto("/markets");
	const rows = page.locator("table").first().locator("tbody tr");
	await rows.filter({ hasText: "BONK" }).getByRole("button").click();
	await rows.filter({ hasText: "WIF" }).getByRole("button").click();
	await page.screenshot({ path: ".shots/markets-starred-1440.png", fullPage: true });

	await page.getByRole("link", { name: "Show starred markets only" }).click();
	await expect(page.getByTestId("starred-only-summary")).toBeVisible();
	await page.screenshot({ path: ".shots/markets-starred-only-1440.png", fullPage: true });

	await page.getByRole("link", { name: "Watchlist", exact: true }).click();
	await expect(page).toHaveURL(/\/watchlist$/);
	await expect(page.getByRole("heading", { name: "Watchlist", level: 1 })).toBeVisible();
	await expect(page.locator("table").first().locator("tbody tr")).toHaveCount(2);
	await page.screenshot({ path: ".shots/watchlist-1440.png", fullPage: true });

	await page.setViewportSize({ width: 390, height: 844 });
	await page.reload();
	await expect(page.getByRole("heading", { name: "Watchlist", level: 1 })).toBeVisible();
	await expect(page.locator("table").first().locator("tbody tr")).toHaveCount(2);
	await page.screenshot({ path: ".shots/watchlist-390.png", fullPage: true });
});

test("watchlist lookup caps the requested key list", async ({ request }) => {
	const keys = Array.from({ length: 51 }, (_, i) => `BAD${i}:token`);
	const qs = keys.map((key) => `key=${encodeURIComponent(key)}`).join("&");
	const response = await request.get(`/watchlist/rows?${qs}`);
	expect(response.ok()).toBeTruthy();
	const body = await response.json();
	expect(body.rows.length + body.unavailable.length).toBe(50);
});

test("watchlist lookup reports an invalid key", async ({ request }) => {
	const response = await request.get("/watchlist/rows?key=not-a-key");
	expect(response.ok()).toBeTruthy();
	const body = await response.json();
	expect(body.unavailable).toHaveLength(1);
	expect(body.unavailable[0].code).toBe("WATCHLIST_KEY_INVALID");
});
