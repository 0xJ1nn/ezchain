// Formats the server's `TokenOut.safety` payload.
//
// The API owns what "pass", "warn" and "unknown" mean. This module only maps the reported
// vocabulary to a tone, and a missing status is printed as unknown. It never upgrades an
// absent check to a pass and never invents the five P0 placeholder checks.

import type { SafetyCheckPayload, TokenSafety } from "./api";

export type SafetyTone = "ok" | "warn" | "err";
export type SafetyDemoState = "pass" | "warn" | "unknown";

const DEMO_STATES = new Set<SafetyDemoState>(["pass", "warn", "unknown"]);

/** Fixture-only QA state from `?safety=`; live mode never reads it. */
export function parseSafetyDemo(
	value: string | string[] | undefined,
): SafetyDemoState | undefined {
	const raw = Array.isArray(value) ? value[0] : value;
	return raw && DEMO_STATES.has(raw as SafetyDemoState)
		? (raw as SafetyDemoState)
		: undefined;
}

export interface SafetyCheckView {
	key: string;
	label: string;
	status: string;
	reason: string | null;
}

function text(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function safetyStatus(safety: TokenSafety | null | undefined): string | null {
	return text(safety?.status);
}

export function safetyStatusLabel(safety: TokenSafety | null | undefined): string {
	return safetyStatus(safety) ?? "unknown";
}

export function safetyTone(status: string | null | undefined): SafetyTone {
	const value = status?.trim().toLowerCase();
	if (!value) return "warn";
	if (["pass", "passed", "ok", "clear", "safe", "good"].includes(value)) return "ok";
	if (["warn", "warning", "caution", "review"].includes(value)) return "warn";
	if (["fail", "failed", "error", "blocked", "danger", "unsafe", "bad"].includes(value)) {
		return "err";
	}
	return "warn";
}

export function safetyReason(safety: TokenSafety | null | undefined): string | null {
	return text(safety?.reason);
}

function checkFromObject(
	key: string,
	value: unknown,
): SafetyCheckView {
	const record =
		value && typeof value === "object" && !Array.isArray(value)
			? (value as SafetyCheckPayload)
			: {};
	const label = text(record.name) ?? text(record.check) ?? text(record.id) ?? key;
	const status =
		text(record.status) ?? text(record.result) ?? text(typeof value === "string" ? value : null);
	return {
		key,
		label,
		status: status ?? "unknown",
		reason: text(record.reason) ?? text(record.detail),
	};
}

/**
 * Normalizes the optional `checks` field. Both a list and a name-keyed object are
 * accepted because the API schema is an open object; anything else yields no rows rather
 * than a made-up check.
 */
export function safetyChecks(safety: TokenSafety | null | undefined): SafetyCheckView[] {
	const checks = safety?.checks;
	if (!checks) return [];
	if (Array.isArray(checks)) {
		return checks
			.map((entry, index) => {
				if (!entry || typeof entry !== "object") return null;
				const record = entry as SafetyCheckPayload;
				const key =
					text(record.id) ?? text(record.check) ?? text(record.name) ?? `check-${index}`;
				return checkFromObject(key, entry);
			})
			.filter((entry): entry is SafetyCheckView => entry !== null);
	}
	if (typeof checks === "object") {
		return Object.entries(checks).map(([key, entry]) => checkFromObject(key, entry));
	}
	return [];
}
