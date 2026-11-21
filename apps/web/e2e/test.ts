import { expect, test as base } from "@playwright/test";

/**
 * Browser tests wait for hydration before they click.
 *
 * The page is server-rendered, so links and buttons are visible before React attaches
 * their handlers. On a busy machine a click in that window can be ignored by the
 * router. Wrapping `page.goto` (and `page.reload`, which does not go through `goto`)
 * gives every spec one place to wait for the marker the application sets after mount,
 * instead of racing the compiler in each test.
 *
 * The marker covers the initial tree only. A control that arrives in its own streamed
 * Suspense boundary, such as the top-bar kill switch, attaches its handlers later still;
 * a test that needs one of those waits for that control or for the flag it publishes.
 */
const waitForHydration = (page: import("@playwright/test").Page) =>
	page
		.waitForFunction(
			() => document.documentElement.dataset.hydrated === "true",
			undefined,
			{ timeout: 30_000 },
		)
		.catch(() => {
			// A page that intentionally fails to boot still has server HTML to assert on.
		});

export const test = base.extend({
	page: async ({ page }, provide) => {
		const goto = page.goto.bind(page);
		page.goto = async (url, options) => {
			const response = await goto(url, options);
			await waitForHydration(page);
			return response;
		};
		const reload = page.reload.bind(page);
		page.reload = async (options) => {
			const response = await reload(options);
			await waitForHydration(page);
			return response;
		};
		await provide(page);
	},
});

export { expect };
