// Reject secret-shaped strings and accidental key material in the public file set.
//
// The public set is "tracked files plus untracked files that are not ignored". Internal
// maintainer material is gitignored and out of scope; a force-added file is still scanned
// because it becomes tracked. Do not replace this with a directory-name skip list.
import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const textExt = /\.(md|txt|json|ya?ml|toml|ini|env|example|ts|tsx|js|mjs|py|sh|css|html)$/;
const patterns = [
	[/-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----/, "private key block"],
	[/\bghp_[A-Za-z0-9]{30,}\b/, "github token"],
	[/\bgithub_pat_[A-Za-z0-9_]{30,}\b/, "github fine-grained token"],
	[/\bsk-[A-Za-z0-9]{32,}\b/, "openai-style key"],
	[/\bAKIA[0-9A-Z]{16}\b/, "aws access key"],
	[/\bxox[baprs]-[A-Za-z0-9-]{10,}\b/, "slack token"],
	[/\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b/, "jwt"],
];
const errors = [];

function publicFiles() {
	try {
		const out = execFileSync(
			"git",
			["ls-files", "--cached", "--others", "--exclude-standard"],
			{ cwd: root, encoding: "utf8" },
		);
		return out
			.split("\n")
			.map((line) => line.trim())
			.filter(Boolean);
	} catch {
		console.error("secrets: git is required to enumerate the public file set");
		process.exit(2);
	}
}

for (const rel of publicFiles()) {
	if (!textExt.test(rel)) continue;
	const file = join(root, rel);
	if (!existsSync(file)) continue;
	const text = readFileSync(file, "utf8");
	for (const [re, label] of patterns) {
		if (re.test(text)) errors.push(`${relative(root, file)}: possible ${label}`);
	}
}

if (errors.length) {
	console.error(`secrets: ${errors.length} problem(s)`);
	for (const e of errors) console.error(`  - ${e}`);
	process.exit(1);
}
console.log("secrets: ok");
