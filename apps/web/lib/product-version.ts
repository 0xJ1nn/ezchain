// The product version is injected at build time by next.config.ts, which reads the
// repository-root VERSION file — the same source the API probes use. There is no
// fallback literal: a bundle that cannot say which product it is must not guess.

export const PRODUCT_VERSION = process.env.EZCHAIN_PRODUCT_VERSION;

if (!PRODUCT_VERSION) {
	throw new Error(
		"ezCHAIN product version is unavailable: EZCHAIN_PRODUCT_VERSION was not injected " +
			"from the VERSION file at build time",
	);
}
