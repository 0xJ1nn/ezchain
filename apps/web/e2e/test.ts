import { expect, test as base } from "@playwright/test";

/**
 * Browser tests wait for hydration before they click or press a shortcut.
 *
 * The page is server-rendered, so links and buttons are visible before React attaches
 * their handlers. On a busy machine a click in that window can be ignored by the
 * router, and a document shortcut such as the top bar's k is lost until the listener
 * behind its Suspense boundary binds. Wrapping `page.goto` gives every spec one place
 * to wait for the root marker and the shortcut signal instead of racing the compiler
 * in each test.
 */
export const test = base.extend({
	page: async ({ page }, provide) => {
		const goto = page.goto.bind(page);
		page.goto = async (url, options) => {
			const response = await goto(url, options);
			await page
				.waitForFunction(
					() => {
						if (document.documentElement.dataset.hydrated !== "true") return false;
						// The root marker can precede a listener behind a Suspense boundary. Where
						// the shell's kill switch is present, wait for the signal its keydown
						// effect publishes so a following key press finds the handler attached.
						const kill = document.querySelector(".kill-wrap");
						return !kill || document.documentElement.dataset.killShortcutReady === "true";
					},
					undefined,
					{ timeout: 30_000 },
				)
				.catch(() => {
					// A page that intentionally fails to boot still has server HTML to assert on.
				});
			return response;
		};
		await provide(page);
	},
});

export { expect };
