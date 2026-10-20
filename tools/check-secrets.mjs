// Reject secret-shaped strings and accidental key material in tracked text files.
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const skip = new Set([".git", "node_modules", "LICENSE"]);
const allow = new Set();
const patterns = [
	[/-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----/, "private key block"],
	[/\bghp_[A-Za-z0-9]{30,}\b/, "github token"],
	[/\bgithub_pat_[A-Za-z0-9_]{30,}\b/, "github fine-grained token"],
	[/\bsk-[A-Za-z0-9]{32,}\b/, "openai-style key"],
	[/\bAKIA[0-9A-Z]{16}\b/, "aws access key"],
	[/\bxox[baprs]-[A-Za-z0-9-]{10,}\b/, "slack token"],
	[/\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b/, "jwt"],
];
const textExt = /\.(md|txt|json|ya?ml|toml|ini|env|example|ts|tsx|js|mjs|py|sh|css|html)$/;
const errors = [];

function walk(d) {
	for (const e of readdirSync(d, { withFileTypes: true })) {
		if (skip.has(e.name)) continue;
		const p = join(d, e.name);
		if (e.isDirectory()) walk(p);
		else if (textExt.test(e.name) && !allow.has(p)) {
			const text = readFileSync(p, "utf8");
			for (const [re, label] of patterns) {
				if (re.test(text)) errors.push(`${relative(root, p)}: possible ${label}`);
			}
		}
	}
}

walk(root);
if (errors.length) {
	console.error(`secrets: ${errors.length} problem(s)`);
	for (const e of errors) console.error(`  - ${e}`);
	process.exit(1);
}
console.log("secrets: ok");
