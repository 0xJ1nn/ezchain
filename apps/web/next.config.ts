import type { NextConfig } from "next";

const apiUrl = process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
	reactStrictMode: true,
	transpilePackages: ["@ezchain/ui"],
	// No telemetry, no dev badge overlapping the product UI.
	devIndicators: false,
	allowedDevOrigins: ["127.0.0.1", "localhost"],
	async rewrites() {
		return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
	},
};

export default nextConfig;
