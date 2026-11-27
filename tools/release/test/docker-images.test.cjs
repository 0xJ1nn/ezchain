"use strict";

// The images are built from a release candidate, never from whatever happens
// to be checked out. A static check keeps the contract honest on a host with
// no Docker daemon; the real image build is a separate, serialized smoke test.

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");

const repo = path.resolve(__dirname, "..", "..", "..");
const apiDockerfile = fs.readFileSync(path.join(repo, "apps", "api", "Dockerfile"), "utf8");
const webDockerfile = fs.readFileSync(path.join(repo, "apps", "web", "Dockerfile"), "utf8");
const buildScript = fs.readFileSync(path.join(repo, "tools", "release", "build-release-images.sh"), "utf8");

test("the API image carries the native bridge it imports at runtime", () => {
	assert.ok(apiDockerfile.includes("libezchain_bridge.so"), "compiled bridge is copied into the runtime");
	assert.ok(apiDockerfile.includes("import ezchain_bridge"), "copy is proven importable before tagging");
	assert.ok(apiDockerfile.includes("quantize_money"), "the bridge's money entry point is checked");
	assert.ok(apiDockerfile.includes("HEALTHCHECK"), "the image answers its own health probe");
	assert.ok(apiDockerfile.includes("/healthz"));
	assert.ok(apiDockerfile.includes("COPY VERSION"), "the single version source is in the image");
	assert.match(apiDockerfile, /FROM python:3\.11-slim@sha256:[0-9a-f]{64}/, "base image is digest-pinned");
});

test("the web image carries the version and a health probe", () => {
	assert.ok(webDockerfile.includes("HEALTHCHECK"));
	assert.ok(webDockerfile.includes("COPY VERSION"));
	assert.match(webDockerfile, /FROM node:22-alpine@sha256:[0-9a-f]{64}/, "base image is digest-pinned");
});

test("the build script builds both images from the named source and never publishes", () => {
	assert.ok(buildScript.includes("--source"), "the release candidate source is explicit");
	assert.equal((buildScript.match(/docker build /g) ?? []).length, 2, "one build per shipped image");
	assert.ok(buildScript.includes("apps/api/Dockerfile"));
	assert.ok(buildScript.includes("apps/web/Dockerfile"));
	assert.ok(buildScript.includes("--smoke"), "a health smoke step exists");
	assert.ok(buildScript.includes("/healthz"));
	assert.equal(buildScript.includes("docker push"), false, "images are never pushed by this script");
});
