import { expect, test } from "./test";

test.use({ colorScheme: "dark" });

test("default dark on load, toggle to light persists on reload", async ({ page }) => {
	await page.goto("/");
	await expect(page.getByText("Fixture preview")).toBeVisible();
	await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
	await expect(page.locator("body")).toHaveCSS("background-color", "rgb(18, 20, 23)");

	const toggle = page.locator("header.topbar").getByTestId("theme-toggle");
	await expect(toggle).toBeVisible();
	await expect(toggle).toHaveAttribute("aria-label", "Switch to light theme");
	await page.screenshot({ path: ".shots/theme-dark-1440.png" });

	await toggle.click();
	await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
	await expect(page.locator("body")).toHaveCSS("background-color", "rgb(242, 242, 239)");
	await expect(toggle).toHaveAttribute("aria-label", "Switch to dark theme");
	await expect
		.poll(() => page.evaluate(() => window.localStorage.getItem("ezchain.theme")))
		.toBe("light");

	await page.reload();
	await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
	await expect(page.locator("body")).toHaveCSS("background-color", "rgb(242, 242, 239)");
	await page.screenshot({ path: ".shots/theme-light-1440.png" });

	await page.getByTestId("theme-toggle").click();
	await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
	await page.reload();
	await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("with no stored choice a light system starts light", async ({ page }) => {
	await page.emulateMedia({ colorScheme: "light" });
	await page.goto("/");
	await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
