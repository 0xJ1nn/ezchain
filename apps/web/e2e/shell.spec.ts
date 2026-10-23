import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

const sidebar = (page: import("@playwright/test").Page) =>
	page.getByRole("navigation", { name: "Primary navigation" });

test("Discovery is the first sidebar group and holds Browse then Signals", async ({ page }) => {
	await page.goto("/");

	const groups = sidebar(page).locator(".nav-group");
	await expect(groups.first().locator(".nav-label")).toHaveText("Discovery");

	const discovery = groups.first();
	await expect(discovery.getByRole("link")).toHaveCount(2);
	await expect(discovery.getByRole("link", { name: "Browse" })).toHaveAttribute(
		"href",
		"/markets",
	);
	await expect(discovery.getByRole("link", { name: "Signals" })).toHaveAttribute(
		"href",
		"/signals",
	);

	// Discovery really is first: every other group follows it.
	await expect(groups.nth(1).locator(".nav-label")).toHaveText("Overview");

	// Reference galleries are documentation, not production navigation.
	await expect(sidebar(page).getByRole("link", { name: "State gallery" })).toHaveCount(0);
	await expect(sidebar(page).getByRole("link", { name: "Components" })).toHaveCount(0);
	await expect(sidebar(page).getByRole("link", { name: "Mobile" })).toHaveCount(0);
	await expect(sidebar(page).getByText("PAPER · no keys")).toBeVisible();
});

test("sidebar toggle collapses, persists on reload and navigation, and shows rail labels", async ({
	page,
}) => {
	await page.goto("/");

	const collapse = page.getByRole("button", { name: "Collapse sidebar" });
	await expect(collapse).toBeVisible();
	await expect(collapse).toHaveAttribute("aria-expanded", "true");
	await expect(collapse).toHaveAttribute("aria-controls", "primary-navigation");

	await collapse.click();
	const expand = page.getByRole("button", { name: "Expand sidebar" });
	await expect(expand).toHaveAttribute("aria-expanded", "false");
	await expect(page.locator("body")).toHaveClass(/sidebar-collapsed/);
	await expect(sidebar(page)).toHaveCSS("width", "72px");

	// Labels are visually hidden, but every rail item still has an accessible name and a
	// readable tooltip on hover and keyboard focus.
	const browse = sidebar(page).getByRole("link", { name: "Browse" });
	await expect(browse).toHaveAttribute("title", "Browse");
	await browse.hover();
	await expect(page.getByTestId("nav-tooltip")).toHaveText("Browse");
	await page.mouse.move(0, 0);
	await expect(page.getByTestId("nav-tooltip")).toHaveCount(0);
	await browse.focus();
	await expect(page.getByTestId("nav-tooltip")).toHaveText("Browse");

	await page.reload();
	await expect(page.locator("body")).toHaveClass(/sidebar-collapsed/);
	await expect(page.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute(
		"aria-expanded",
		"false",
	);

	// The toggle is a real button, so Enter and Space work without a pointer.
	await page.getByRole("button", { name: "Expand sidebar" }).focus();
	await page.keyboard.press("Enter");
	await expect(page.locator("body")).not.toHaveClass(/sidebar-collapsed/);
	await expect(sidebar(page)).toHaveCSS("width", "240px");
	await page.getByRole("button", { name: "Collapse sidebar" }).focus();
	await page.keyboard.press("Space");
	await expect(page.locator("body")).toHaveClass(/sidebar-collapsed/);
	await expect(sidebar(page)).toHaveCSS("width", "72px");

	// The rail survives client-side navigation, not just reload.
	await sidebar(page).getByRole("link", { name: "Browse" }).click();
	await expect(page).toHaveURL(/\/markets$/);
	await expect(page.locator("body")).toHaveClass(/sidebar-collapsed/);
	await expect(sidebar(page)).toHaveCSS("width", "72px");

	await page.getByRole("button", { name: "Expand sidebar" }).click();
	await expect(page.locator("body")).not.toHaveClass(/sidebar-collapsed/);
	await expect(sidebar(page)).toHaveCSS("width", "240px");
	await expect(page.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute(
		"aria-expanded",
		"true",
	);
});

test("topbar search names markets and All chains opens the browser", async ({ page }) => {
	await page.goto("/");

	const search = page.getByRole("searchbox", {
		name: "Search markets (symbol, name or address)",
	});
	await expect(search).toBeVisible();
	await expect(search).toHaveAttribute(
		"placeholder",
		"Search markets (symbol, name or address)",
	);

	const chains = page.locator("header.topbar").getByRole("link", { name: "All chains" });
	await expect(chains).toHaveAttribute("href", "/markets");
	await expect(chains).toHaveAttribute("title", "Open the multi-chain market browser");
	await chains.click();
	await expect(page).toHaveURL(/\/markets$/);
});

test("mobile navigation puts Browse and Signals one tap away", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/markets");

	const mobile = page.getByRole("navigation", { name: "Primary mobile navigation" });
	await expect(mobile).toBeVisible();
	await expect(mobile.getByRole("link")).toHaveCount(5);
	await expect(mobile.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
	await expect(mobile.getByRole("link", { name: "Browse" })).toHaveAttribute(
		"aria-current",
		"page",
	);
	await expect(mobile.getByRole("link", { name: "Signals" })).toHaveAttribute(
		"href",
		"/signals",
	);
	await expect(mobile.getByRole("link", { name: "More" })).not.toHaveAttribute(
		"aria-current",
		"page",
	);

	await mobile.getByRole("link", { name: "Signals" }).click();
	await expect(page).toHaveURL(/\/signals$/);
	await expect(mobile.getByRole("link", { name: "Signals" })).toHaveAttribute(
		"aria-current",
		"page",
	);
	await expect(mobile.getByRole("link", { name: "More" })).not.toHaveAttribute(
		"aria-current",
		"page",
	);

	// Signals is not duplicated inside More.
	await page.goto("/more");
	await expect(mobile.getByRole("link", { name: "More" })).toHaveAttribute(
		"aria-current",
		"page",
	);
	await expect(mobile.getByRole("link", { name: "Signals" })).not.toHaveAttribute(
		"aria-current",
		"page",
	);
});

test("More keeps the reference galleries and reaches discovery first", async ({ page }) => {
	await page.goto("/more");
	await expect(page.getByRole("heading", { name: "More", level: 1 })).toBeVisible();
	await expect(
		page.getByText("Browse and Signals are the primary discovery surfaces"),
	).toBeVisible();
	await expect(page.getByRole("link", { name: "State gallery" })).toBeVisible();
	await expect(page.getByRole("link", { name: "Components" })).toBeVisible();
	await expect(page.getByRole("link", { name: "Mobile" })).toBeVisible();

	const chainLink = page
		.locator("main")
		.getByRole("link", { name: "All chains" });
	await expect(chainLink).toHaveAttribute("href", "/markets");
	await expect(chainLink).toHaveAttribute("title", "Open the multi-chain market browser");
});

test("heavy routes and the shell ship loading and error boundaries", async () => {
	const app = join(__dirname, "..", "app");

	// Loading UI is timing-dependent in dev (the fixture reads resolve before the browser
	// can paint the fallback), so assert the boundaries are wired rather than race them.
	for (const [route, active, skeleton] of [
		["(dashboard)", "dashboard", "GenericSkeleton"],
		["positions", "positions", "PositionsSkeleton"],
		["markets", "markets", "MarketsSkeleton"],
		["performance", "performance", "PerformanceSkeleton"],
	]) {
		const source = readFileSync(join(app, route, "loading.tsx"), "utf8");
		expect(source, `${route || "root"} loading boundary`).toContain(skeleton);
		expect(source, `${route || "root"} loading keeps the shell`).toContain("AppShell");
		expect(source, `${route || "root"} loading active state`).toContain(
			`active="${active}"`,
		);
	}

	const error = readFileSync(join(app, "error.tsx"), "utf8");
	expect(error).toContain('"use client"');
	expect(error).toContain("reset");
	expect(error).toContain("Try again");
	expect(error).toContain("RENDER_FAILED");

	const rows = readFileSync(join(app, "ui.css"), "utf8");
	for (const width of ["240px", "72px"]) {
		expect(rows, `sidebar ${width} rule`).toContain(width);
	}
});
