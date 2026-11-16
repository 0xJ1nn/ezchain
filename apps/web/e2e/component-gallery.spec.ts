import { expect, test } from "./test";

// The gallery is design documentation: it shows the real primitives with fixture strings
// and never claims the sample numbers came from an API or that an overlay is live.

test("the gallery documents tile, chart frame, table, badges and overlays", async ({ page }) => {
	await page.goto("/components");
	await expect(page.getByRole("heading", { name: "Components", level: 1 })).toBeVisible();
	await expect(page.getByText("FIXTURE", { exact: true }).first()).toBeVisible();

	const tile = page.locator("section.panel").filter({ hasText: "the signature component" });
	await expect(tile.locator("article.tile")).toHaveCount(1);
	await expect(tile).toContainText("BO");

	await expect(page.getByRole("heading", { name: "Chart frame" })).toBeVisible();
	const table = page.locator("section.panel").filter({ hasText: "dense data display" });
	await expect(table.getByRole("table")).toBeVisible();
	const badges = page.locator("section.panel").filter({ hasText: "tone vocabulary" });
	await expect(badges.getByText("ok", { exact: true })).toBeVisible();
	await expect(badges.locator("span.badge.err", { hasText: "err" })).toBeVisible();

	await expect(page.getByTestId("gallery-drawer")).toBeVisible();
	await expect(page.getByTestId("gallery-modal")).toBeVisible();
	await expect(page.getByTestId("gallery-toast")).toBeVisible();
});

test("documented overlays are static and the gallery never claims live data", async ({ page }) => {
	await page.goto("/components");
	for (const id of ["gallery-drawer", "gallery-modal", "gallery-toast"]) {
		const overlay = page.getByTestId(id);
		await expect(overlay.getByRole("button")).toHaveCount(0);
		await expect(overlay.getByRole("link")).toHaveCount(0);
	}
	await expect(page.getByText("LIVE API", { exact: true })).toHaveCount(0);
	await expect(page.getByText(/A side surface/)).toBeVisible();
});
