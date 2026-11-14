// One home for how the persisted operator switch is described on screen.
//
// The API reports one flag with two levels: `scope` is the widest level in force and
// `scopes` lists the levels that flag governs. The labels here are those scope values,
// never a word the web bundle invented; a read without scopes names no level at all.
// Both surfaces that describe the switch import this module, so the dashboard card and
// the risk page cannot drift apart.

export type KillSwitchLevel = "global" | "strategy";

/** The order the API lists its scopes in, so both surfaces read the same way. */
export const KILL_SWITCH_LEVEL_ORDER: readonly KillSwitchLevel[] = ["global", "strategy"];

/**
 * What each level actually gates. The level is one flag, not two: when it is armed, both
 * sentences are true at once.
 */
export const KILL_SWITCH_LEVELS: Record<KillSwitchLevel, { label: string; gates: string }> = {
	global: {
		label: "Global",
		gates:
			"every new entry, whoever places it — a manual paper ticket, the REST route or a strategy — refused before a quote.",
	},
	strategy: {
		label: "Strategy",
		gates:
			"strategy-attributed intents, which the engine also holds with KILL_SWITCH_ACTIVE in its run log.",
	},
};

/**
 * The consequence a reader would otherwise miss: a manual paper order is an entry, so
 * the global level refuses a buy while it is armed. A sell is an exit and still settles.
 */
export const KILL_SWITCH_MANUAL_ORDER_NOTE =
	"Manual paper orders are entries, so a manual paper buy is refused while armed; exits, stops and reduce-only sells still settle.";

/** The levels the API reported, in its own order. An empty list means "not reported". */
export function killSwitchLevels(
	scopes: readonly KillSwitchLevel[] | null | undefined,
): KillSwitchLevel[] {
	if (!scopes || scopes.length === 0) return [];
	return KILL_SWITCH_LEVEL_ORDER.filter((level) => scopes.includes(level));
}

/**
 * "global and strategy" — the API's own scope values. Without them the copy says the
 * read did not report the levels rather than guessing both.
 */
export function killSwitchScopeSummary(
	scopes: readonly KillSwitchLevel[] | null | undefined,
): string {
	const levels = killSwitchLevels(scopes);
	return levels.length > 0 ? levels.join(" and ") : "levels not reported by the API";
}
