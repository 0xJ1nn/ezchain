import type { NextConfig } from "next";

const nextConfig: NextConfig = {
	reactStrictMode: true,
	transpilePackages: ["@ezchain/ui"],
	// Playwright uses a separate build directory so a developer's `next dev` on :3000
	// cannot corrupt the gate server on :3100 (both would otherwise share .next).
	distDir: process.env.EZCHAIN_NEXT_DIST_DIR ?? ".next",
	// No telemetry, no dev badge overlapping the product UI.
	devIndicators: false,
	allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
