// Browser-session store for protection arms in fixture mode.
//
// The strategy fixture session keeps mutations in page-local React state. Protection
// arms must also show up on the automations page after a full page load, so this store
// keeps the session delta in sessionStorage: same browser tab, same session, and every
// Playwright test gets an isolated context. The API is never touched from a fixture
// build — a fixture session is visibly labelled wherever it renders.

import type { Protection } from "./protection-api";

const STORAGE_KEY = "ezchain.fixture-protections.v1";

export interface ProtectionDelta {
	/** Created or edited arms, stored as complete API-shaped rows. */
	arms: Protection[];
	/** Arms disarmed this session; they stay listed with status "cancelled". */
	removedIds: string[];
}

const EMPTY: ProtectionDelta = { arms: [], removedIds: [] };

function isProtection(value: unknown): value is Protection {
	return typeof value === "object" && value !== null && typeof (value as Protection).id === "string";
}

export function readProtectionDelta(): ProtectionDelta {
	if (typeof window === "undefined") return EMPTY;
	try {
		const raw = window.sessionStorage.getItem(STORAGE_KEY);
		if (!raw) return EMPTY;
		const parsed: unknown = JSON.parse(raw);
		if (typeof parsed !== "object" || parsed === null) return EMPTY;
		const candidate = parsed as Partial<ProtectionDelta>;
		return {
			arms: Array.isArray(candidate.arms) ? candidate.arms.filter(isProtection) : [],
			removedIds: Array.isArray(candidate.removedIds)
				? candidate.removedIds.filter((id): id is string => typeof id === "string")
				: [],
		};
	} catch {
		// A corrupted session store fails closed to the fixture armory, never to a guess.
		return EMPTY;
	}
}

function writeDelta(delta: ProtectionDelta): void {
	if (typeof window === "undefined") return;
	window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(delta));
}

/** Upsert a created or edited arm into the session delta. */
export function saveProtectionArm(arm: Protection): void {
	const delta = readProtectionDelta();
	const rest = delta.arms.filter((row) => row.id !== arm.id);
	writeDelta({ ...delta, arms: [arm, ...rest] });
}

/** A disarm is a state change: the arm is kept in the session and marked cancelled. */
export function markProtectionRemoved(id: string): void {
	const delta = readProtectionDelta();
	if (!delta.removedIds.includes(id)) {
		writeDelta({ ...delta, removedIds: [...delta.removedIds, id] });
	}
}

/**
 * Server fixture arms plus the session delta. Session arms override same-id rows and
 * removed rows stay visible with status "cancelled", matching what the API returns
 * after a DELETE.
 */
export function mergeProtections(base: Protection[], delta: ProtectionDelta): Protection[] {
	const removed = new Set(delta.removedIds);
	const sessionById = new Map(delta.arms.map((arm) => [arm.id, arm]));
	const merged = base.map((arm) => {
		if (removed.has(arm.id)) return { ...arm, status: "cancelled" };
		const session = sessionById.get(arm.id);
		return session ?? arm;
	});
	for (const arm of delta.arms) {
		if (!merged.some((row) => row.id === arm.id)) merged.unshift(arm);
	}
	return merged;
}
