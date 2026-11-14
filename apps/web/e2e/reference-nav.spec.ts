import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "./test";
import { moreGroups } from "../lib/more-nav";

// The Playwright gate runs the development surface (`EZCHAIN_WEB_MODE=fixture`), so the
// reference galleries are reachable here and must be listed on /more. Production is the
// same app without them: `moreGroups(false)` is the production navigation, and the gallery
// routes call notFound() when `referenceSurfacesEnabled()` is false. Both are asserted
// below because one web server cannot serve both modes.

const app = join(__dirname, "..", "app");

const GALLERIES = [
	{ label: "State gallery", path: "/states", source: "states/page.tsx" },
	{ label: "Components", path: "/components", source: "components/page.tsx" },
	{ label: "Mobile", path: "/mobile", source: "mobile/page.tsx" },
];

test("the development build lists the reference galleries on More and reaches them", async ({
	page,
}) => {
	await page.goto("/more");
	await expect(page.getByRole("heading", { name: "More", level: 1 })).toBeVisible();
	await expect(page.getByRole("heading", { name: "Reference", level: 2 })).toBeVisible();

	for (const gallery of GALLERIES) {
		await expect(page.getByRole("link", { name: gallery.label, exact: true })).toHaveAttribute(
			"href",
			gallery.path,
		);
	}

	for (const gallery of GALLERIES) {
		await page.goto(gallery.path);
		await expect(page.getByRole("heading", { name: gallery.label, level: 1 })).toBeVisible();
	}
});

test("production navigation carries no reference group and no gallery link", () => {
	const production = moreGroups(false);
	const titles = production.map((group) => group.title);
	expect(titles).not.toContain("Reference");

	const hrefs = production.flatMap((group) => group.links.map(([href]) => href));
	for (const gallery of GALLERIES) {
		expect(hrefs, `production More still links ${gallery.path}`).not.toContain(gallery.path);
	}

	// The development list is the only place the group is added; the two never diverge.
	const development = moreGroups(true);
	expect(development.map((group) => group.title)).toContain("Reference");
	expect(development.flatMap((group) => group.links.map(([href]) => href))).toEqual(
		expect.arrayContaining(GALLERIES.map((gallery) => gallery.path)),
	);
});

test("the gallery routes are gated behind the development check, not deleted", () => {
	for (const gallery of GALLERIES) {
		const source = readFileSync(join(app, gallery.source), "utf8");
		expect(source, `${gallery.source} imports the gate`).toContain(
			'import { referenceSurfacesEnabled } from "@/lib/runtime";',
		);
		expect(source, `${gallery.source} renders 404 outside development`).toContain(
			"if (!referenceSurfacesEnabled()) notFound();",
		);
	}
});

test("/automations lands on the canonical strategy surface", async ({ page }) => {
	const response = await page.goto("/automations");

	// The old path is a redirect, not a 404, so bookmarks and prose links keep working.
	expect(response?.status()).toBe(200);
	await expect(page).toHaveURL(/\/strategies\?view=automations$/);
	await expect(page.getByRole("heading", { name: "Automations", level: 1 })).toBeVisible();
	await expect(page.getByRole("region", { name: "Scheduled strategies table" })).toBeVisible();
	await expect(
		page
			.getByRole("region", { name: "Scheduled strategies table" })
			.locator("tbody tr"),
	).toHaveCount(4);

	// The canonical surface owns the tab; the scheduler view is one of its views.
	await expect(
		page.getByRole("group", { name: "Strategy views" }).getByRole("link", { name: "Automations" }),
	).toHaveAttribute("aria-current", "page");
});

test("the canonical strategies page still renders its own content", async ({ page }) => {
	await page.goto("/strategies");
	await expect(page.getByRole("heading", { name: "Strategies", level: 1 })).toBeVisible();
	await expect(page.getByRole("region", { name: "Decision log table" })).toBeVisible();
	await expect(page.getByRole("heading", { name: "Create strategy" })).toBeVisible();
	await expect(page.getByText("4 total · 3 active · 1 paused")).toBeVisible();

	// The two views are tabs of one surface, and the tab link is real navigation.
	await page
		.getByRole("group", { name: "Strategy views" })
		.getByRole("link", { name: "Automations" })
		.click();
	await expect(page).toHaveURL(/\/strategies\?view=automations$/);
	await expect(page.getByRole("heading", { name: "Automations", level: 1 })).toBeVisible();
});

test("sidebar and More name the strategy job once", async ({ page }) => {
	await page.goto("/strategies");
	const sidebar = page.getByRole("navigation", { name: "Primary navigation" });
	await expect(sidebar.getByRole("link", { name: "Strategies" })).toHaveAttribute(
		"href",
		"/strategies",
	);
	await expect(sidebar.getByRole("link", { name: "Automations" })).toHaveCount(0);

	await page.goto("/more");
	const main = page.getByRole("main");
	await expect(main.getByRole("link", { name: "Strategies", exact: true })).toHaveAttribute(
		"href",
		"/strategies",
	);
	await expect(main.getByRole("link", { name: "Automations", exact: true })).toHaveCount(0);
});
