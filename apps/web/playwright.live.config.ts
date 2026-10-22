import { defineConfig } from "@playwright/test";
import { browserChannel } from "./e2e/browser-channel";

const API_URL = process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
	testDir: "./e2e",
	testMatch: /live-paper\.spec\.ts/,
	timeout: 120_000,
	expect: { timeout: 20_000 },
	fullyParallel: false,
	workers: 1,
	reporter: [["list"]],
	use: {
		baseURL: "http://127.0.0.1:3200",
		channel: browserChannel(),
		viewport: { width: 1440, height: 900 },
		trace: "retain-on-failure",
	},
	webServer: {
		command: "NEXT_TELEMETRY_DISABLED=1 pnpm dev --port 3200",
		url: "http://127.0.0.1:3200",
		reuseExistingServer: false,
		timeout: 120_000,
		env: {
			EZCHAIN_WEB_MODE: "live",
			EZCHAIN_API_URL: API_URL,
			EZCHAIN_API_TOKEN: process.env.EZCHAIN_API_TOKEN ?? "",
			EZCHAIN_PUBLIC_URL: "http://127.0.0.1:3200",
		},
	},
});
