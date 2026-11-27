"use strict";

// Homebrew tap, winget and Scoop all consume one release manifest and the
// checksum files derived from it. A hand-maintained digest or a second upload
// path is a defect: it lets one channel drift away from the artifact another
// channel installed.

const assert = require("node:assert/strict");
const test = require("node:test");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const repo = path.resolve(__dirname, "..", "..", "..");
const sourceDir = path.resolve(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(sourceDir, "artifacts.json"), "utf8"));
const generator = path.join(repo, "tools", "release", "gen-package-manifests.mjs");

function digestOf(text) {
	return crypto.createHash("sha256").update(text).digest("hex");
}

function generate(artifactDir, outDir) {
	return execFileSync(process.execPath, [generator, "--artifacts", artifactDir, "--out", outDir], {
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	});
}

function fixture(tmp) {
	const artifacts = path.join(tmp, "artifacts");
	const out = path.join(tmp, "out");
	fs.mkdirSync(artifacts, { recursive: true });
	fs.mkdirSync(out, { recursive: true });
	const digests = {};
	for (const name of Object.values(manifest.artifacts)) {
		const body = `fixture payload for ${name}\n`;
		fs.writeFileSync(path.join(artifacts, name), body);
		digests[name] = digestOf(body);
	}
	return { artifacts, out, digests };
}

test("one generator writes checksum sidecars and all three package-manager manifests", () => {
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ezchain-manifests-"));
	try {
		const { artifacts, out, digests } = fixture(tmp);
		generate(artifacts, out);

		for (const [key, name] of Object.entries(manifest.artifacts)) {
			const sidecar = fs.readFileSync(path.join(out, `${name}.sha256`), "utf8");
			assert.equal(sidecar, `${digests[name]}  ${name}\n`, `${key}: checksum sidecar names its artifact`);
		}

		const cask = fs.readFileSync(path.join(out, "homebrew", "ezchain.rb"), "utf8");
		assert.match(cask, /cask "ezchain"/);
		assert.ok(cask.includes(`version "${manifest.version}"`));
		for (const arch of ["darwin-arm64", "darwin-x64"]) {
			const name = manifest.artifacts[arch];
			assert.ok(cask.includes(digests[name]), `homebrew uses the ${arch} computed digest`);
			assert.ok(cask.includes(`${manifest.releaseUrlBase}/${name}`), `homebrew uses the ${arch} release URL`);
		}

		const winget = fs.readFileSync(path.join(out, "winget", "0xJ1nn.ezchain.yaml"), "utf8");
		assert.ok(winget.includes("PackageIdentifier: 0xJ1nn.ezchain"));
		assert.ok(winget.includes(`PackageVersion: ${manifest.version}`));
		assert.ok(winget.includes(digests[manifest.artifacts["win32-x64"]]));
		assert.ok(winget.includes(`${manifest.releaseUrlBase}/${manifest.artifacts["win32-x64"]}`));

		const scoop = JSON.parse(fs.readFileSync(path.join(out, "scoop", "ezchain.json"), "utf8"));
		assert.equal(scoop.version, manifest.version);
		assert.equal(scoop.hash, digests[manifest.artifacts["win32-x64"]]);
		assert.equal(scoop.url, `${manifest.releaseUrlBase}/${manifest.artifacts["win32-x64"]}`);

		const summary = JSON.parse(fs.readFileSync(path.join(out, "manifest.json"), "utf8"));
		for (const [key, name] of Object.entries(manifest.artifacts)) {
			assert.equal(summary.artifacts[key].sha256, digests[name]);
			assert.equal(summary.artifacts[key].url, `${manifest.releaseUrlBase}/${name}`);
		}
	} finally {
		fs.rmSync(tmp, { recursive: true, force: true });
	}
});

test("the generator refuses a missing artifact instead of emitting a manifest", () => {
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ezchain-manifests-missing-"));
	try {
		const artifacts = path.join(tmp, "artifacts");
		const out = path.join(tmp, "out");
		fs.mkdirSync(artifacts, { recursive: true });
		fs.mkdirSync(out, { recursive: true });
		const names = Object.values(manifest.artifacts);
		const keeper = names.slice(0, -1);
		for (const name of keeper) fs.writeFileSync(path.join(artifacts, name), "partial\n");
		assert.throws(() => generate(artifacts, out), (error) => String(error.stderr).includes("missing"));
		assert.equal(fs.existsSync(path.join(out, "manifest.json")), false, "nothing is published on a missing artifact");
	} finally {
		fs.rmSync(tmp, { recursive: true, force: true });
	}
});

test("committed templates carry placeholders, never a literal checksum", () => {
	const templateDir = path.join(sourceDir, "templates");
	const files = fs.readdirSync(templateDir).filter((name) => name.endsWith(".tmpl"));
	assert.deepEqual(files.sort(), ["homebrew.cask.tmpl", "scoop.json.tmpl", "winget.yaml.tmpl"]);
	for (const name of files) {
		const text = fs.readFileSync(path.join(templateDir, name), "utf8");
		assert.equal(/\b[0-9a-f]{64}\b/.test(text), false, `${name}: no hand-maintained digest`);
		assert.match(text, /\{\{(?:[A-Z0-9_]+_)?SHA\}\}/, `${name}: digest is rendered from the computed sidecar`);
	}
});
