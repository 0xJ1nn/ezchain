import type { NextConfig } from "next";

const nextConfig: NextConfig = {
	reactStrictMode: true,
	transpilePackages: ["@ezchain/ui"],
	// No telemetry, no dev badge overlapping the product UI.
	devIndicators: false,
	allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
