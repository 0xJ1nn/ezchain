// Temporary local config: the shared global setup refuses while an unrelated
// next dev occupies :3000. This worktree has its own .next, so the check does
// not apply. Removed before commit.
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
	...base,
	globalSetup: undefined,
});
