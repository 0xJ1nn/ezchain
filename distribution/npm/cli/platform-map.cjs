"use strict";

// The supported host matrix, in one place. The launcher picks exactly one
// optional binary package from it; the tests use the same table so a new
// platform cannot be added to one side alone.

const SUPPORTED = [
	["darwin", "arm64", "@ezchain/cli-darwin-arm64"],
	["darwin", "x64", "@ezchain/cli-darwin-x64"],
	["linux", "x64", "@ezchain/cli-linux-x64"],
	["linux", "arm64", "@ezchain/cli-linux-arm64"],
	["win32", "x64", "@ezchain/cli-win32-x64"],
];

function platformKey(platform, arch) {
	return SUPPORTED.some(([os, cpu]) => os === platform && cpu === arch) ? `${platform}-${arch}` : null;
}

function platformPackage(platform, arch) {
	return SUPPORTED.find(([os, cpu]) => os === platform && cpu === arch)?.[2] ?? null;
}

module.exports = { SUPPORTED, platformKey, platformPackage };
