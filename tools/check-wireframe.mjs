// Structural integrity gate for design/wireframe.
// - every manifest page exists; every page on disk is in the manifest
// - every internal href/src resolves; no external CSS/JS/font/image requests
// - every page loads the shared shell + stylesheet and declares data-page
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { basename, join, resolve, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const dir = join(root, "design", "wireframe");
const errors = [];
const manifestPath = join(dir, "manifest.json");

if (!existsSync(manifestPath)) {
	console.error("missing design/wireframe/manifest.json");
	process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const pages = manifest.pages ?? [];
const listed = new Set(pages.map((p) => p.file));

if (listed.size !== pages.length) errors.push("manifest contains duplicate page files");
if (!listed.has("index.html")) errors.push("manifest must list index.html");

const onDisk = readdirSync(dir).filter((f) => f.endsWith(".html"));
for (const f of onDisk) if (!listed.has(f)) errors.push(`page not in manifest: ${f}`);
for (const f of listed) if (!existsSync(join(dir, f))) errors.push(`manifest page missing: ${f}`);

const ids = new Set(pages.map((p) => p.id));
for (const p of pages) {
	if (!p.id || !p.title || !p.group) errors.push(`manifest entry incomplete: ${p.file}`);
}

for (const file of onDisk) {
	const html = readFileSync(join(dir, file), "utf8");
	const rel = `design/wireframe/${file}`;

	if (!/<title>[^<]+<\/title>/.test(html)) errors.push(`${rel}: missing <title>`);
	if (!html.includes('data-page="')) errors.push(`${rel}: missing data-page attribute`);
	if (!html.includes("assets/wire.css")) errors.push(`${rel}: does not load assets/wire.css`);
	if (!html.includes("assets/shell.js")) errors.push(`${rel}: does not load assets/shell.js`);

	for (const m of html.matchAll(/\b(?:href|src)\s*=\s*"([^"]+)"/g)) {
		const raw = m[1].trim();
		if (!raw || raw.startsWith("#") || raw.startsWith("mailto:")) continue;
		if (/^(https?:)?\/\//i.test(raw)) {
			if (m[0].startsWith("src")) errors.push(`${rel}: external asset request ${raw}`);
			continue;
		}
		if (raw.startsWith("data:")) continue;
		const clean = raw.split("#")[0].split("?")[0];
		if (!clean) continue;
		const target = resolve(dirname(join(dir, file)), clean);
		if (!existsSync(target)) {
			errors.push(`${rel}: broken link ${raw}`);
			continue;
		}
		if (target.endsWith(".html")) {
			const t = relative(dir, target).replace(/\\/g, "/");
			if (!listed.has(t)) errors.push(`${rel}: links to unlisted page ${t}`);
		} else if (statSync(target).isDirectory()) {
			errors.push(`${rel}: link points at a directory ${raw}`);
		}
	}

	for (const m of html.matchAll(/<a\b[^>]*href="(https?:[^"]+)"[^>]*>/g)) {
		if (!/rel="[^"]*noopener/.test(m[0])) errors.push(`${rel}: external link without rel=noopener`);
	}
}

if (errors.length) {
	console.error(`wireframe: ${errors.length} problem(s)`);
	for (const e of errors) console.error(`  - ${e}`);
	process.exit(1);
}
console.log(`wireframe: ok (${pages.length} pages, ${onDisk.length} files)`);
