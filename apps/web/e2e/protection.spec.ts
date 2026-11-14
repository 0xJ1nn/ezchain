import { expect, test } from "./test";

// Position protection: arm a stop from the positions page, edit it, and see
// the armed arm reflected on the strategy surface's automations view. Fixture
// mode only; the fixture never pretends to write to the API.

test("arm a stop-loss from the positions page, edit it, and list it in automations", async ({
	page,
}) => {
	await page.goto("/positions");
	await expect(page.getByRole("heading", { name: "Positions", level: 1 })).toBeVisible();
	await expect(page.locator(".page-actions .badge", { hasText: "FIXTURE" })).toBeVisible();

	const form = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Position protection" }) });
	await expect(form).toBeVisible();

	await form.getByLabel("Position").selectOption({ index: 0 });
	await form.getByLabel("Kind").selectOption("stop_loss");
	await form.getByLabel("Trigger price (USD)").fill("0.00002");
	await form.getByRole("button", { name: "Arm protection" }).click();

	await expect(form.getByRole("status")).toContainText("Armed stop-loss");
	const armed = form.locator("tbody tr").filter({ hasText: "stop-loss" }).first();
	await expect(armed).toContainText("armed");
	await expect(armed).toContainText("0.00002");

	// Immediate edit: the same row updates without a reload.
	await armed.getByRole("button", { name: "Edit" }).click();
	await armed.getByLabel("Trigger price (USD)").fill("0.000018");
	await armed.getByRole("button", { name: "Save" }).click();
	await expect(armed).toContainText("0.000018");

	await page.goto("/strategies?view=automations");
	await expect(page.getByRole("heading", { name: "Automations", level: 1 })).toBeVisible();
	const arms = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Armed protection" }) });
	await expect(arms).toContainText("stop-loss");
	await expect(arms).toContainText("armed");
	await expect(arms).toContainText("BONK");
});
