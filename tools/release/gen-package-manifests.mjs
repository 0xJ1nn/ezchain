// Generate every package-manager manifest from one release manifest and the
// SHA-256 files computed from its artifacts. There is no second upload path and
// no hand-maintained checksum: Homebrew, winget and Scoop all read the same
// digests the installer verifies. A missing artifact fails the whole run.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(scriptDir, "..", "..");
const defaults = {
	manifest: path.join(repo, "distribution", "package-managers", "artifacts.json"),
};

function usage() {
	console.error("usage: gen-package-manifests.mjs --artifacts <dir> --out <dir> [--manifest <file>]");
	process.exit(2);
}

function parse(argv) {
	const result = {};
	for (let i = 0; i < argv.length; i += 1) {
		const key = argv[i];
		if (key === "--help" || key === "-h") usage();
		if (!key.startsWith("--") || i + 1 >= argv.length) usage();
		result[key.slice(2)] = argv[++i];
	}
	if (!result.artifacts || !result.out) usage();
	return result;
}

const args = parse(process.argv.slice(2));
const manifest = JSON.parse(fs.readFileSync(args.manifest ?? defaults.manifest, "utf8"));
const artifactDir = path.resolve(args.artifacts);
const outDir = path.resolve(args.out);
const templateDir = path.join(repo, "distribution", "package-managers", "templates");

function replace(template, values) {
	let output = template;
	for (const [key, value] of Object.entries(values)) {
		output = output.split(`{{${key}}}`).join(String(value));
	}
	if (/{{[A-Z0-9_]+}}/.test(output)) {
		throw new Error(`unresolved placeholder in a generated manifest: ${output.match(/{{[A-Z0-9_]+}}/)[0]}`);
	}
	return output;
}

function write(file, body) {
	const target = path.join(outDir, file);
	fs.mkdirSync(path.dirname(target), { recursive: true });
	fs.writeFileSync(target, body);
	console.log(`wrote ${file}`);
}

const digests = {};
for (const [key, name] of Object.entries(manifest.artifacts)) {
	if (name !== path.basename(name) || name.includes("..")) {
		throw new Error(`invalid artifact name for ${key}: ${name}`);
	}
	const file = path.join(artifactDir, name);
	if (!fs.existsSync(file)) throw new Error(`missing artifact: ${name}`);
	const digest = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
	digests[key] = digest;
	write(`${name}.sha256`, `${digest}  ${name}\n`);
}

const values = {
	VERSION: manifest.version,
	REPOSITORY: manifest.repository,
	RELEASE_URL_BASE: manifest.releaseUrlBase,
};
for (const [key, name] of Object.entries(manifest.artifacts)) {
	const prefix = key.toUpperCase().replace(/-/g, "_");
	values[`${prefix}_ARTIFACT`] = name;
	values[`${prefix}_URL`] = `${manifest.releaseUrlBase}/${name}`;
	values[`${prefix}_SHA`] = digests[key];
}

const templates = [
	["homebrew.cask.tmpl", "homebrew/ezchain.rb"],
	["winget.yaml.tmpl", "winget/0xJ1nn.ezchain.yaml"],
	["scoop.json.tmpl", "scoop/ezchain.json"],
];
for (const [template, target] of templates) {
	write(target, replace(fs.readFileSync(path.join(templateDir, template), "utf8"), values));
}

const summary = {
	tag: manifest.tag,
	version: manifest.version,
	releaseUrlBase: manifest.releaseUrlBase,
	artifacts: Object.fromEntries(
		Object.entries(manifest.artifacts).map(([key, name]) => [
			key,
			{
				file: name,
				url: `${manifest.releaseUrlBase}/${name}`,
				sha256: digests[key],
				checksumFile: `${name}.sha256`,
			},
		]),
	),
};
write("manifest.json", `${JSON.stringify(summary, null, 2)}\n`);
