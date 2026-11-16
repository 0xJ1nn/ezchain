import { expect, test } from "./test";

// Automations are the scheduler view of stored strategies. These cases pin the three
// states: populated with the controls the API supports, empty, and a failed read with its
// typed reason. Pause, resume and a single cycle never claim a generic automation object.

test("the scheduler table shows server status, last run and its run log", async ({ page }) => {
	await page.goto("/automations");
	await expect(page.getByRole("heading", { name: "Automations", level: 1 })).toBeVisible();

	const table = page.getByTestId("automations-table");
	await expect(table).toBeVisible();
	const bonk = table.locator('tr[data-strategy="str-bonk-momentum"]');
	await expect(bonk).toContainText("BONK momentum");
	await expect(bonk).toContainText("active");
	await expect(bonk).toContainText("2026-10-22");
	await expect(bonk.getByRole("button", { name: "Pause" })).toBeVisible();

	await bonk.getByRole("button", { name: /decision/ }).click();
	const log = table.locator('tr[data-log="str-bonk-momentum"]');
	await expect(log).toBeVisible();
	await expect(log).toContainText("2026");

	await bonk.getByRole("button", { name: "Run once" }).click();
	await expect(page.getByRole("status")).toContainText("BONK momentum:");
	await expect(log).toBeVisible();
});

test("pause and resume change the persisted status without inventing a run", async ({ page }) => {
	await page.goto("/automations");
	const table = page.getByTestId("automations-table");
	const bonk = table.locator('tr[data-strategy="str-bonk-momentum"]');
	const aero = table.locator('tr[data-strategy="str-aero-dip"]');

	await bonk.getByRole("button", { name: "Pause" }).click();
	await expect(page.getByRole("status")).toContainText("BONK momentum is now paused");
	await expect(bonk).toContainText("paused");
	await expect(bonk.getByRole("button", { name: "Resume" })).toBeVisible();

	await aero.getByRole("button", { name: "Resume" }).click();
	await expect(page.getByRole("status")).toContainText("AERO dip is now active");
	await expect(aero).toContainText("active");

	// The builder is the declared trigger/conditions/actions surface; the copy says what
	// its dry run is rather than claiming a 30-day simulation the API does not produce.
	await expect(page.getByText(/no simulated 30-day window is claimed/i)).toBeVisible();
});

test("empty and failed scheduler reads render their own states", async ({ page }) => {
	await page.goto("/automations?state=empty");
	await expect(page.getByText("No strategies installed")).toBeVisible();
	await expect(page.getByTestId("automations-table")).toHaveCount(0);

	await page.goto("/automations?state=error");
	await expect(page.getByText("Automations unavailable")).toBeVisible();
	await expect(page.getByText("FIXTURE_DEMO_AUTOMATION_ERROR")).toBeVisible();
	await expect(page.getByTestId("automations-table")).toHaveCount(0);
	const band = page.locator('section.kpi-band[aria-label="Automation summary"]');
	await expect(band.locator(".stat").filter({ hasText: "Installed" }).locator(".value")).toHaveText(
		"—",
	);
	await expect(page.getByRole("link", { name: "Retry →" })).toHaveAttribute(
		"href",
		"/strategies?view=automations",
	);
});
