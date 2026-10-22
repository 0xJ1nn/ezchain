import { expect } from "@playwright/test";

/**
 * The Playwright gate runs its own `next dev` on :3100 from apps/web. A developer's
 * `next dev` on :3000 from the same directory shares `.next`, which corrupts the JSON
 * manifest and produces random 500s. Fail fast with an actionable message instead of
 * letting that surface as a flaky test.
 */
export default async function globalSetup(): Promise<void> {
	let running = false;
	try {
		const response = await fetch("http://127.0.0.1:3000/", {
			signal: AbortSignal.timeout(1_000),
		});
		running = response.status < 500;
	} catch {
		running = false;
	}
	expect(
		running,
		"stop the `next dev` server on :3000 before running Playwright; it shares apps/web/.next with the gate on :3100",
	).toBe(false);
}
