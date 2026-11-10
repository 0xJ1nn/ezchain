import { expect, test } from "./test";

const CASES = [
	{ route: "/ledger", label: "Orders table", caption: "Orders", headers: 8 },
	{ route: "/ledger", label: "Fills table", caption: "Fills", headers: 10 },
	{ route: "/markets", label: "Markets table", caption: "Markets", headers: 10 },
	{ route: "/positions", label: "Server positions table", caption: "Server positions", headers: 11 },
];

for (const c of CASES) {
	test(`${c.label} is keyboard focusable with a name and scoped headers`, async ({ page }) => {
		await page.goto(c.route);
		const wrapper = page.getByRole("region", { name: c.label });
		await expect(wrapper).toBeVisible();
		await wrapper.focus();
		await expect(wrapper).toBeFocused();
		await expect(wrapper).toHaveAttribute("tabindex", "0");
		await expect(wrapper.locator("caption")).toHaveText(c.caption);
		await expect(wrapper.locator("thead th")).toHaveCount(c.headers);
		await expect(wrapper.locator('thead th[scope="col"]')).toHaveCount(c.headers);
		await page.keyboard.press("Tab");
		await page.keyboard.press("Shift+Tab");
		await expect(wrapper).toBeFocused();
	});
}
