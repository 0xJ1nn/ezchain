// Verify relative links and images in tracked Markdown files.
import { readFileSync, existsSync, statSync, readdirSync } from "node:fs";
import { join, resolve, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const skip = new Set([".git", "node_modules", ".dart_tool", "dist", "build"]);
const errors = [];

function walk(d) {
	for (const entry of readdirSync(d, { withFileTypes: true })) {
		if (skip.has(entry.name)) continue;
		const p = join(d, entry.name);
		if (entry.isDirectory()) walk(p);
		else if (entry.name.endsWith(".md")) check(p);
	}
}

function check(file) {
	const text = readFileSync(file, "utf8");
	for (const m of text.matchAll(/!?\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g)) {
		const raw = m[1];
		if (/^(https?:|mailto:|#|data:)/i.test(raw)) continue;
		const clean = raw.split("#")[0].split("?")[0];
		if (!clean) continue;
		const target = resolve(dirname(file), clean);
		if (!existsSync(target)) {
			errors.push(`${relative(root, file)}: broken link ${raw}`);
		} else if (statSync(target).isDirectory() && !existsSync(join(target, "README.md"))) {
			// directories are fine as links; no-op
		}
	}
}

walk(root);
if (errors.length) {
	console.error(`links: ${errors.length} problem(s)`);
	for (const e of errors) console.error(`  - ${e}`);
	process.exit(1);
}
console.log("links: ok");
