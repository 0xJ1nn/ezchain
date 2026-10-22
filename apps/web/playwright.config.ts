import { defineConfig } from "@playwright/test";

export default defineConfig({
	testDir: "./e2e",
	timeout: 30_000,
	fullyParallel: true,
	reporter: [["list"]],
	use: {
		baseURL: "http://127.0.0.1:3100",
		channel: "chrome",
		viewport: { width: 1440, height: 900 },
		trace: "retain-on-failure",
	},
	webServer: {
		command: "NEXT_TELEMETRY_DISABLED=1 pnpm dev --port 3100",
		url: "http://127.0.0.1:3100",
		reuseExistingServer: false,
		timeout: 120_000,
		env: { EZCHAIN_WEB_MODE: "fixture" },
	},
});
