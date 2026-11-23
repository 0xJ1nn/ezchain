import { expect, test } from "./test";

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const TRADE_URL = `/trade?chain=solana&token=${BONK_MINT}`;
const ARMS_KEY = "ezchain-fixture-protection-arms";

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

test("trade exits panel is linked both ways to the chart and saves through the protection client", async ({
	page,
}) => {
	await page.goto(TRADE_URL);

	const panel = page.getByTestId("exits-panel");
	await expect(panel).toBeVisible();

	// Three targets, each with its own partial size, and a live reward-to-risk.
	for (const index of [0, 1, 2]) {
		await expect(page.getByTestId(`exit-target-row-${index}`)).toBeVisible();
		await expect(page.getByTestId(`exit-tp-${index}-size`)).toHaveValue(
			["40", "35", "25"][index]!,
		);
	}
	await expect(page.getByTestId("reward-risk")).toContainText("1 :");

	// Move to breakeven writes the entry into the stop field.
	const entry = await page.getByTestId("trade-entry").textContent();
	await page.getByTestId("exit-breakeven").click();
	await expect(page.getByTestId("exit-stop-input")).toHaveValue(entry?.trim() ?? "");

	// Panel -> chart: typing a target price moves its line.
	await page.getByTestId("exit-tp-0-input").fill("0.00004200");
	const line = page.getByTestId("exit-line-tp-0");
	await expect.poll(async () => Number(await line.getAttribute("aria-valuenow"))).toBeCloseTo(
		0.000042,
		10,
	);

	await page.getByTestId("save-exits").click();
	await expect(page.getByTestId("exits-status")).toContainText("Exits armed");

	const stored = await page.evaluate((key) => sessionStorage.getItem(key), ARMS_KEY);
	if (!stored) throw new Error("expected the protection client to store the arms");
	const kinds = (JSON.parse(stored) as { kind: string }[]).map((arm) => arm.kind);
	expect(kinds).toEqual(expect.arrayContaining(["stop_loss", "take_profit", "trailing_stop"]));
});

test("trade exits fail closed on an invalid stop without storing an arm", async ({ page }) => {
	await page.goto(TRADE_URL);

	await page.getByTestId("exit-stop-input").fill("0");
	await page.getByTestId("save-exits").click();

	await expect(page.getByTestId("exits-status")).toContainText("positive stop price");
	const stored = await page.evaluate((key) => sessionStorage.getItem(key), ARMS_KEY);
	expect(stored).toBeNull();
});
