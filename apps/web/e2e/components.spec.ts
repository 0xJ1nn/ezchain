import { expect, test } from "./test";

// The brand primitives are pinned against the board's own values. Dark is the
// default; light is the board's paper skin. Fixture strings only, no live data.

type Skin = {
	name: "dark" | "light";
	canvas: string;
	buy: string;
	sell: string;
	disabled: string;
	paper: string;
	up: string;
};

const skins: Skin[] = [
	{
		name: "dark",
		canvas: "rgb(8, 9, 11)",
		buy: "rgb(31, 216, 151)",
		sell: "rgb(242, 73, 92)",
		disabled: "rgb(25, 29, 34)",
		paper: "rgb(76, 157, 255)",
		up: "rgb(31, 216, 151)",
	},
	{
		name: "light",
		canvas: "rgb(243, 244, 246)",
		buy: "rgb(10, 159, 108)",
		sell: "rgb(217, 45, 67)",
		disabled: "rgb(245, 246, 248)",
		paper: "rgb(31, 111, 224)",
		up: "rgb(10, 159, 108)",
	},
];

for (const skin of skins) {
	test(`brand controls use the board's ${skin.name} values`, async ({ page }) => {
		await page.addInitScript((theme) => window.localStorage.setItem("ezchain.theme", theme), skin.name);
		await page.goto("/components");
		const controls = page.getByTestId("brand-controls");
		await expect(controls).toBeVisible();
		await expect(page.locator("body")).toHaveCSS("background-color", skin.canvas);

		const buttons = controls.getByTestId("brand-buttons").locator("button");
		await expect(buttons).toHaveCount(8);
		await expect(buttons.nth(2)).toHaveCSS("background-color", skin.buy);
		await expect(buttons.nth(3)).toHaveCSS("background-color", skin.sell);
		await expect(buttons.nth(7)).toBeDisabled();
		await expect(buttons.nth(7)).toHaveCSS("background-color", skin.disabled);

		const sizes = controls.getByTestId("brand-button-sizes").locator("button");
		await expect(sizes).toHaveCount(4);
		await expect(sizes.nth(0)).toHaveCSS("height", "32px");
		await expect(sizes.nth(2)).toHaveCSS("height", "44px");
		await expect(sizes.nth(3)).toHaveCSS("height", "52px");

		await expect(controls.getByTestId("brand-badges").locator(".badge")).toHaveCount(6);
		await expect(controls.getByTestId("brand-modes").locator(".mode")).toHaveCount(4);
		await expect(controls.getByTestId("brand-modes").locator(".mode.paper")).toHaveCSS("color", skin.paper);
		await expect(controls.getByTestId("brand-kill").locator(".kill")).toHaveCount(4);
		await expect(controls.getByTestId("brand-deltas").locator(".delta.up").first()).toHaveCSS("color", skin.up);
		await expect(controls.getByTestId("brand-fields").locator(".field input")).toBeVisible();
		await expect(controls.getByTestId("brand-segmented").locator(".seg")).toHaveCount(2);
		await expect(controls.getByTestId("brand-chips").locator(".chips button")).toHaveCount(4);
		await expect(controls.getByTestId("brand-toggles").locator("button.toggle")).toHaveCount(3);
		await expect(controls.getByTestId("brand-toggles").locator('button[aria-checked="true"]')).toHaveCount(1);
		await expect(controls.getByTestId("brand-toasts").locator(".toast")).toBeVisible();
	});
}

test("the hold key fills for 650 ms and only confirms on a complete hold", async ({ page }) => {
	await page.goto("/components");
	const hold = page.getByTestId("brand-controls").getByTestId("brand-hold").locator("button");
	const count = page.getByTestId("brand-hold-count");
	await expect(hold).toHaveText("Hold to confirm");
	await expect(hold).toHaveCSS("--hold", "650ms");

	// Release before the fill completes: nothing is confirmed and the band drains.
	await hold.hover();
	await page.mouse.down();
	await expect(hold).toHaveClass(/holding/);
	await page.waitForTimeout(120);
	await page.mouse.up();
	await expect(hold).not.toHaveClass(/done/);
	await expect(count).toHaveText("Confirmed 0");

	// Hold past 650 ms: the key turns green, draws its check and confirms once.
	await hold.hover();
	await page.mouse.down();
	await expect(hold).toHaveClass(/holding/);
	await expect(hold).toHaveText(/Filled/, { timeout: 5_000 });
	await page.mouse.up();
	await expect(hold).toHaveClass(/done/);
	await expect(count).toHaveText("Confirmed 1");
});
