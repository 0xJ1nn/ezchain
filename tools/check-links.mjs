// Verify relative links and images in the public Markdown set.
//
// The public set is "tracked files plus untracked files that are not ignored". This is
// deliberate: internal maintainer material (gitignored) is not part of the repo, while a
// newly written public doc is still checked before it is committed. A name-based skip
// list previously hid force-added files; do not reintroduce one.
import { readFileSync, existsSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, resolve, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const errors = [];

function publicFiles() {
	try {
		const out = execFileSync(
			"git",
			["ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
			{ cwd: root, encoding: "utf8" },
		);
		return out
			.split("\n")
			.map((line) => line.trim())
			.filter(Boolean);
	} catch {
		console.error("links: git is required to enumerate the public file set");
		process.exit(2);
	}
}

function check(rel) {
	const file = join(root, rel);
	if (!existsSync(file)) return;
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

for (const rel of publicFiles()) check(rel);

if (errors.length) {
	console.error(`links: ${errors.length} problem(s)`);
	for (const e of errors) console.error(`  - ${e}`);
	process.exit(1);
}
console.log("links: ok");
