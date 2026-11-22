// WCAG 2.1 contrast gate for design/tokens.json.
//
// Usage: node tools/check-contrast.mjs [--check|--json]
//   --check  human-readable table (default)
//   --json   machine-readable results
//
// Body text must reach 4.5:1; non-essential text and state borders must reach 3:1.
// Scope of the pairs:
//   - text.primary/secondary/muted on the five content surfaces (canvas, raised, overlay,
//     sunken, subtle). hairline and borderStrong are line roles, never text backdrops.
//   - text.inverse is body text on the ink fill (text.primary), not on a surface.
//   - text.disabled is exempt from the 4.5:1 body rule by WCAG 1.4.3, but is still held
//     to 3:1 so disabled copy stays perceivable.
//   - semantic text on its own bg (4.5:1).
//   - the data (fresh/stale/unknown) and risk (ok/warn/block) groups: text on own bg
//     (4.5:1) and border on own bg (3:1), because those left rules are the non-colour
//     shape cue that must survive grayscale.
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const tokens = JSON.parse(readFileSync(join(root, "design/tokens.json"), "utf8"));
const json = process.argv.includes("--json");

const BODY_MIN = 4.5;
const NON_ESSENTIAL_MIN = 3;
const CONTENT_SURFACES = ["canvas", "raised", "overlay", "sunken", "subtle"];

function luminance(hex) {
	if (typeof hex !== "string" || !/^#[0-9a-f]{6}$/i.test(hex)) {
		throw new Error(`expected a 6-digit hex colour, got ${JSON.stringify(hex)}`);
	}
	const channels = [1, 3, 5].map((i) => {
		const srgb = parseInt(hex.slice(i, i + 2), 16) / 255;
		return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
	});
	return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground, background) {
	const [lighter, darker] = [luminance(foreground), luminance(background)].sort(
		(a, b) => b - a,
	);
	return (lighter + 0.05) / (darker + 0.05);
}

const results = [];
function check(theme, label, kind, foreground, background, min) {
	const ratio = contrast(foreground, background);
	results.push({
		theme,
		pair: label,
		kind,
		foreground,
		background,
		ratio: Number(ratio.toFixed(2)),
		min,
		pass: ratio + 1e-9 >= min,
	});
}

for (const [theme, color] of [
	["light", tokens.color],
	["dark", tokens.dark?.color],
]) {
	if (!color || typeof color !== "object") throw new Error(`missing ${theme} color group`);

	for (const role of ["primary", "secondary", "muted"]) {
		for (const surface of CONTENT_SURFACES) {
			check(
				theme,
				`text.${role} / surface.${surface}`,
				"body",
				color.text[role],
				color.surface[surface],
				BODY_MIN,
			);
		}
	}

	check(
		theme,
		"text.inverse / text.primary (ink fill)",
		"body",
		color.text.inverse,
		color.text.primary,
		BODY_MIN,
	);

	for (const surface of CONTENT_SURFACES) {
		check(
			theme,
			`text.disabled / surface.${surface}`,
			"non-essential",
			color.text.disabled,
			color.surface[surface],
			NON_ESSENTIAL_MIN,
		);
	}

	for (const [name, group] of Object.entries(color.semantic)) {
		check(
			theme,
			`semantic.${name}.text / semantic.${name}.bg`,
			"body",
			group.text,
			group.bg,
			BODY_MIN,
		);
	}

	for (const groupName of ["data", "risk"]) {
		for (const [name, group] of Object.entries(color[groupName])) {
			check(
				theme,
				`${groupName}.${name}.text / ${groupName}.${name}.bg`,
				"body",
				group.text,
				group.bg,
				BODY_MIN,
			);
			check(
				theme,
				`${groupName}.${name}.border / ${groupName}.${name}.bg`,
				"border",
				group.border,
				group.bg,
				NON_ESSENTIAL_MIN,
			);
		}
	}
}

const failed = results.filter((r) => !r.pass);
const summary = {
	checked: results.length,
	passed: results.length - failed.length,
	failed: failed.length,
};
const ok = failed.length === 0;

if (json) {
	console.log(
		JSON.stringify(
			{
				ok,
				thresholds: { body: BODY_MIN, nonEssentialAndBorder: NON_ESSENTIAL_MIN },
				summary,
				results,
			},
			null,
			2,
		),
	);
} else {
	const themeWidth = Math.max(...results.map((r) => r.theme.length), 5);
	const pairWidth = Math.max(...results.map((r) => r.pair.length), 4);
	const rows = [
		`${"theme".padEnd(themeWidth)}  ${"pair".padEnd(pairWidth)}  ratio   min  kind            result`,
		`${"-".repeat(themeWidth)}  ${"-".repeat(pairWidth)}  ${"-----"}  ${"---"}  ${"--------------"}  ${"------"}`,
		...results.map(
			(r) =>
				`${r.theme.padEnd(themeWidth)}  ${r.pair.padEnd(pairWidth)}  ${r.ratio
					.toFixed(2)
					.padStart(5)}  ${r.min.toFixed(2)}  ${r.kind.padEnd(14)}  ${
					r.pass ? "PASS" : "FAIL"
				}`,
		),
	];
	console.log(rows.join("\n"));
	console.log(
		`\n${summary.checked} pairs checked · ${summary.passed} passed · ${summary.failed} failed`,
	);
	if (!ok) {
		console.error(
			`contrast: ${summary.failed} pair(s) below threshold — fix the token value in design/tokens.json (both themes where affected)`,
		);
	}
}

process.exit(ok ? 0 : 1);
