"use client";

import { useCallback, useEffect, useState } from "react";

// Trading preferences are a property of this browser, not of the account. They are
// deliberately not sent to an API: there is no settings write endpoint, and a dead
// control that pretends to save would be worse than an honest local choice. The
// values only shape the ticket's quick-buy chips, slippage and priority fee
// selectors; none of them changes an order amount, a quote or a risk decision.

export const DEFAULT_PRESETS = [25, 100, 250, 500] as const;
export const SLIPPAGE_OPTIONS = ["0.5", "1.0", "2.0"] as const;
export const PRIORITY_OPTIONS = [
	{ id: "slow", label: "Slow · 0.00005 SOL" },
	{ id: "fast", label: "Fast · 0.0001 SOL" },
	{ id: "turbo", label: "Turbo · 0.0005 SOL" },
] as const;

const KEY = "ezchain.tradingPrefs.v1";
const EVENT = "ezchain:trading-prefs";

export interface TradingPrefs {
	presets: number[];
	slippage: string;
	priority: string;
}

export const DEFAULT_PREFS: TradingPrefs = {
	presets: [...DEFAULT_PRESETS],
	slippage: "1.0",
	priority: "fast",
};

function normalise(value: unknown): TradingPrefs {
	if (value === null || typeof value !== "object") return { ...DEFAULT_PREFS, presets: [...DEFAULT_PRESETS] };
	const raw = value as Partial<TradingPrefs>;
	const presets = Array.isArray(raw.presets)
		? raw.presets
				.filter((v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0 && v <= 100000)
				.slice(0, 8)
				.map((v) => Math.round(v * 100) / 100)
		: [];
	const slippage = SLIPPAGE_OPTIONS.includes(raw.slippage as (typeof SLIPPAGE_OPTIONS)[number])
		? (raw.slippage as string)
		: DEFAULT_PREFS.slippage;
	const priority = PRIORITY_OPTIONS.some((o) => o.id === raw.priority)
		? (raw.priority as string)
		: DEFAULT_PREFS.priority;
	return { presets: presets.length ? presets : [...DEFAULT_PRESETS], slippage, priority };
}

function read(): TradingPrefs {
	if (typeof window === "undefined") return { ...DEFAULT_PREFS, presets: [...DEFAULT_PRESETS] };
	try {
		return normalise(JSON.parse(window.localStorage.getItem(KEY) ?? "null") as unknown);
	} catch {
		return { ...DEFAULT_PREFS, presets: [...DEFAULT_PRESETS] };
	}
}

export function saveTradingPrefs(next: TradingPrefs): TradingPrefs {
	const safe = normalise(next);
	try {
		window.localStorage.setItem(KEY, JSON.stringify(safe));
	} catch {
		// A blocked store keeps the in-memory choice; the page says where it is kept.
	}
	window.dispatchEvent(new CustomEvent(EVENT, { detail: safe }));
	return safe;
}

/** Subscribe to the stored preferences and to changes from the ticket or settings. */
function useSyncedPrefs(): [TradingPrefs, (next: TradingPrefs) => void] {
	const [prefs, setPrefs] = useState<TradingPrefs>(() => ({ ...DEFAULT_PREFS, presets: [...DEFAULT_PRESETS] }));
	useEffect(() => {
		setPrefs(read());
		const changed = (event: Event) => setPrefs(normalise((event as CustomEvent).detail));
		const storage = (event: StorageEvent) => {
			if (event.key === KEY) setPrefs(read());
		};
		window.addEventListener(EVENT, changed);
		window.addEventListener("storage", storage);
		return () => {
			window.removeEventListener(EVENT, changed);
			window.removeEventListener("storage", storage);
		};
	}, []);
	const update = useCallback((next: TradingPrefs) => setPrefs(saveTradingPrefs(next)), []);
	return [prefs, update];
}

/** Read the saved preferences after mount. Defaults render on the server. */
export function useTradingPrefs(): TradingPrefs {
	return useSyncedPrefs()[0];
}

/** The ticket can change slippage and priority fee in place; presets are edited in settings. */
export function useTicketTradeSettings() {
	const [prefs, update] = useSyncedPrefs();
	return {
		prefs,
		setSlippage: (slippage: string) => update({ ...prefs, slippage }),
		setPriority: (priority: string) => update({ ...prefs, priority }),
	};
}

export function useTradingPrefsEditor() {
	const [prefs, update] = useSyncedPrefs();
	const reset = useCallback(
		() => update({ ...DEFAULT_PREFS, presets: [...DEFAULT_PRESETS] }),
		[update],
	);
	return { prefs, update, reset };
}

export function slippageLabel(value: string): string {
	return `${value}%`;
}

export function priorityLabel(value: string): string {
	return PRIORITY_OPTIONS.find((option) => option.id === value)?.label ?? PRIORITY_OPTIONS[1]!.label;
}
