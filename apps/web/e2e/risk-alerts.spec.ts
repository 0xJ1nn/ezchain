import { expect, test } from "./test";

// Risk and alerts must state the limits, exposure and breaker/alert states in words from
// the server, and must keep an unavailable series or feed as a typed reason. No colour or
// sample value is allowed to stand in for a read the app could not make.

test("risk shows server limits and exposure, and keeps an unread series unavailable", async ({
	page,
}) => {
	await page.goto("/risk");
	await expect(page.getByRole("heading", { name: "Risk", level: 1 })).toBeVisible();

	// Fixture mode cannot read the limits endpoint either; its typed code is shown instead
	// of invented numbers.
	const limits = page.locator("section.panel").filter({ hasText: "Server limits" });
	await expect(limits).toContainText("Limits unavailable");
	await expect(limits).toContainText("FIXTURE_UNAVAILABLE");
	await expect(limits).toContainText("This read is not part of the labelled fixture set.");

	// The exposure rows are the server's PositionView values with a text mark verdict.
	const exposure = page.locator("section.panel").filter({ hasText: "Exposure by position" });
	await expect(exposure.getByRole("table")).toBeVisible();
	await expect(exposure.getByText(/fresh ·/).first()).toBeVisible();

	// Fixture mode cannot read the performance series; the typed code is shown, not zero.
	const drawdown = page.locator("section.panel").filter({ hasText: "Drawdown" }).first();
	await expect(drawdown).toContainText("Drawdown series unavailable");
	await expect(drawdown).toContainText("FIXTURE_UNAVAILABLE");
	await expect(drawdown).toContainText("No drawdown value is substituted");
	await expect(drawdown.getByRole("table")).toHaveCount(0);

	// A breaker that is not exposed is named in words, never represented by a light alone.
	await expect(page.getByText("Circuit breakers")).toBeVisible();
	await expect(page.getByText("not exposed", { exact: true })).toBeVisible();

	// The dashboard and this page share the same switch scope wording.
	const note = page.locator("section.panel").filter({ hasText: "Kill switch" }).first();
	await expect(note).toContainText("global and strategy scope");
	await expect(note).toContainText("Manual paper orders are entries");
});

test("alerts keep an unread feed typed and name what is not built", async ({ page }) => {
	await page.goto("/alerts");
	await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();

	const incomplete = page.locator("section.panel").filter({ hasText: "Alert inputs incomplete" });
	await expect(incomplete).toContainText("FIXTURE_UNAVAILABLE");
	await expect(incomplete).toContainText("Nothing is guessed");
	await expect(page.getByText("No derived alerts")).toBeVisible();

	const band = page.locator('section.kpi-band[aria-label="Alert summary"]');
	await expect(band.locator(".stat").filter({ hasText: "Delivery" }).locator(".value")).toHaveText(
		"—",
	);

	const delivery = page.locator("section.panel").filter({ hasText: "Delivery and rules" });
	await expect(delivery).toContainText("no alert rule object");
	await expect(delivery).toContainText("quiet hours");
	await expect(delivery).toContainText("delivery log");
});
