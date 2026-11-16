import { expect, test } from "./test";

// The signature tile is the one component every wireframe state renders through. These
// tests pin that the gallery has all eleven states from the single PositionTileView model,
// that unknown values stay —, and that the actions are reachable from the keyboard even
// though execution is not wired.
const STATES = [
	"Entering (intent, not fill)",
	"Pre-bank",
	"Trail armed",
	"Banked",
	"Riding",
	"Laddered",
	"Unsellable · no sell route",
	"Dust",
	"Stale mark",
	"Closed",
	"Quarantined",
];

test("every wireframe tile state renders from the one PositionTileView model", async ({ page }) => {
	await page.goto("/states");
	await expect(page.getByRole("heading", { name: "State gallery", level: 1 })).toBeVisible();

	const gallery = page.getByRole("region", { name: "Position tile states" });
	// Eleven fixtures, one article each, all rendered by the shared component.
	await expect(gallery.locator("article.tile")).toHaveCount(11);
	for (const id of [
		"bonk",
		"aerodrome",
		"mochi",
		"scamcoin",
		"trail-armed",
		"banked",
		"laddered",
		"dust",
		"stale",
		"closed",
		"quarantined",
	]) {
		await expect(gallery.locator(`article.tile#${id}`)).toBeAttached();
	}

	// Coverage is derived from the fixtures: a state is only "yes" when a tile renders it.
	const coverage = page.getByRole("table", { name: "State coverage" });
	for (const state of STATES) {
		const row = coverage.locator("tbody tr").filter({ hasText: state });
		await expect(row, `missing coverage row for ${state}`).toHaveCount(1);
		await expect(row.getByText("yes", { exact: true })).toBeVisible();
	}
	await expect(coverage.getByText("not yet", { exact: true })).toHaveCount(0);
});

test("unknown values are —, never a fabricated zero", async ({ page }) => {
	await page.goto("/states");
	const gallery = page.getByRole("region", { name: "Position tile states" });

	// The entering tile has no realized or unrealized value; the closed tile has no
	// unrealized remainder. Both keep the em-dash rather than printing $0.00.
	const entering = gallery.locator("article.tile#mochi");
	await expect(entering.locator(".tile-money .money-cell").filter({ hasText: "Unreal" }).locator(".v")).toHaveText("—");
	const closed = gallery.locator("article.tile#closed");
	await expect(closed.locator(".tile-money .money-cell").filter({ hasText: "Unreal" }).locator(".v")).toHaveText("—");

	// An unavailable exit says so; it never substitutes a price for the missing route.
	const unsellable = gallery.locator("article.tile#scamcoin");
	await expect(unsellable).toContainText("Sell is unavailable.");
	await expect(unsellable.locator(".ladder-top .mono")).toHaveText("$0.0041");
});

test("sell now, write off and the menu are present and keyboard reachable", async ({ page }) => {
	await page.goto("/states");
	const gallery = page.getByRole("region", { name: "Position tile states" });

	const sell = gallery.getByRole("button", { name: "Sell now", exact: true }).first();
	await expect(sell).toBeVisible();
	await expect(sell).toHaveAttribute("aria-disabled", "true");
	await sell.focus();
	await expect(sell).toBeFocused();

	const writeOff = gallery.getByRole("button", { name: "Write off Scamcoin, paper only" });
	await expect(writeOff).toBeVisible();
	await expect(writeOff).toHaveAttribute("aria-disabled", "true");
	await writeOff.focus();
	await expect(writeOff).toBeFocused();

	const more = gallery.getByRole("button", { name: "More actions for Bonk" });
	await expect(more).toBeVisible();
	await more.focus();
	await expect(more).toBeFocused();
});
