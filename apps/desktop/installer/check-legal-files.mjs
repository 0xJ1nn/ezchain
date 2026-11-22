// Verify that every legal file an AGPL distribution must carry is staged into the
// desktop installer. NOTICE section 7 requires each distributed copy to keep the
// notice, so a packaging config that drops one of these files is a defect: fail
// the check rather than silently shipping an incomplete copy.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(desktop, "..", "..");
const config = JSON.parse(readFileSync(join(desktop, "package.json"), "utf8"));

const required = ["LICENSE", "NOTICE", "TRADEMARKS.md"];
const extraResources = config.build?.extraResources ?? [];
const errors = [];

for (const name of required) {
	const entry = extraResources.find((item) => item && item.to === name);
	if (!entry) {
		errors.push(`${name}: no extraResources entry targets ${name}`);
		continue;
	}
	const from = resolve(desktop, typeof entry.from === "string" ? entry.from : "");
	if (from !== join(root, name)) {
		errors.push(`${name}: extraResources.from is ${entry.from}, expected ../../${name}`);
	}
	if (!existsSync(from)) {
		errors.push(`${name}: source file is missing (${entry.from})`);
	}
}

if (errors.length) {
	console.error(`legal files: ${errors.length} problem(s)`);
	for (const error of errors) console.error(`  - ${error}`);
	process.exit(1);
}
console.log(`legal files: ok (${required.length} staged: ${required.join(", ")})`);
