// Browser-local watchlist.
//
// The store is deliberately small: a namespaced localStorage key holding a JSON array of
// token identities. It has no account, no API and no server copy. Reading tolerates a
// missing key, corrupt JSON, blocked storage and entries written by an older shape; a
// broken payload is an empty watchlist, never a render failure.
//
// The pure functions are exported for the Node-side tests. The subscribe/snapshot pair
// feeds `useSyncExternalStore` so every star on the page reads one store.

export const WATCHLIST_STORAGE_KEY = "ezchain.watchlist.v1";
export const WATCHLIST_MAX_ENTRIES = 50;

export interface WatchlistEntry {
	chain: string;
	base_token: string;
	symbol: string;
	name: string;
	added_at: string;
}

export interface WatchlistInput {
	chain: string;
	base_token: string;
	symbol?: string;
	name?: string;
}

export interface WatchlistStorage {
	getItem(key: string): string | null;
	setItem(key: string, value: string): void;
}

export type WatchlistToggleResult = {
	added: boolean;
	persisted: boolean;
	full: boolean;
};

export function normalizeChain(chain: string): string {
	return chain.trim().toLowerCase().slice(0, 32);
}

// EVM addresses are case-insensitive; Solana base58 addresses are not. Lower-case only
// the 0x form so a key stays stable without changing a case-sensitive mint.
export function normalizeBaseToken(token: string): string {
	const trimmed = token.trim().slice(0, 128);
	return /^0x[0-9a-fA-F]{40}$/.test(trimmed) ? trimmed.toLowerCase() : trimmed;
}

export function watchlistKey(chain: string, baseToken: string): string {
	return `${normalizeChain(chain)}:${normalizeBaseToken(baseToken)}`;
}

function cleanText(value: unknown, max: number): string {
	return typeof value === "string" ? value.trim().slice(0, max) : "";
}

function normalizeEntry(value: unknown): WatchlistEntry | null {
	if (!value || typeof value !== "object" || Array.isArray(value)) return null;
	const raw = value as Record<string, unknown>;
	const chain = normalizeChain(cleanText(raw.chain, 32));
	const baseToken = normalizeBaseToken(cleanText(raw.base_token, 128));
	if (!chain || !baseToken) return null;
	const symbol = cleanText(raw.symbol, 32);
	const name = cleanText(raw.name, 80);
	const addedAt = cleanText(raw.added_at, 40);
	return {
		chain,
		base_token: baseToken,
		symbol: symbol || (baseToken.length > 12 ? `${baseToken.slice(0, 4)}…${baseToken.slice(-4)}` : baseToken),
		name,
		added_at: Number.isNaN(Date.parse(addedAt)) ? "" : addedAt,
	};
}

function dedupe(entries: WatchlistEntry[]): WatchlistEntry[] {
	const seen = new Set<string>();
	const unique: WatchlistEntry[] = [];
	for (const entry of entries) {
		const key = watchlistKey(entry.chain, entry.base_token);
		if (seen.has(key)) continue;
		seen.add(key);
		unique.push(entry);
		if (unique.length >= WATCHLIST_MAX_ENTRIES) break;
	}
	return unique;
}

export function parseWatchlist(raw: string | null): WatchlistEntry[] {
	if (!raw) return [];
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return [];
	}
	if (!Array.isArray(parsed)) return [];
	const entries: WatchlistEntry[] = [];
	for (const item of parsed) {
		const entry = normalizeEntry(item);
		if (entry) entries.push(entry);
	}
	return dedupe(entries);
}

export function serializeWatchlist(entries: WatchlistEntry[]): string {
	return JSON.stringify(dedupe(entries));
}

function browserStorage(): WatchlistStorage | null {
	if (typeof window === "undefined") return null;
	try {
		return window.localStorage;
	} catch {
		return null;
	}
}

export function readWatchlist(storage: WatchlistStorage | null = browserStorage()): WatchlistEntry[] {
	if (!storage) return [];
	try {
		return parseWatchlist(storage.getItem(WATCHLIST_STORAGE_KEY));
	} catch {
		return [];
	}
}

export function writeWatchlist(
	entries: WatchlistEntry[],
	storage: WatchlistStorage | null = browserStorage(),
): boolean {
	if (!storage) return false;
	try {
		storage.setItem(WATCHLIST_STORAGE_KEY, serializeWatchlist(entries));
		return true;
	} catch {
		return false;
	}
}

export function isWatchlisted(
	entries: WatchlistEntry[],
	chain: string,
	baseToken: string,
): boolean {
	const key = watchlistKey(chain, baseToken);
	return entries.some((entry) => watchlistKey(entry.chain, entry.base_token) === key);
}

export function addWatchlistEntry(
	entries: WatchlistEntry[],
	input: WatchlistInput,
): WatchlistEntry[] {
	const candidate = normalizeEntry({
		chain: input.chain,
		base_token: input.base_token,
		symbol: input.symbol ?? "",
		name: input.name ?? "",
		added_at: new Date().toISOString(),
	});
	if (!candidate) return entries;
	if (isWatchlisted(entries, candidate.chain, candidate.base_token)) return entries;
	if (entries.length >= WATCHLIST_MAX_ENTRIES) return entries;
	return [...entries, candidate];
}

export function removeWatchlistEntry(
	entries: WatchlistEntry[],
	chain: string,
	baseToken: string,
): WatchlistEntry[] {
	const key = watchlistKey(chain, baseToken);
	return entries.filter((entry) => watchlistKey(entry.chain, entry.base_token) !== key);
}

export function toggleWatchlistEntry(
	entries: WatchlistEntry[],
	input: WatchlistInput,
): WatchlistEntry[] {
	return isWatchlisted(entries, input.chain, input.base_token)
		? removeWatchlistEntry(entries, input.chain, input.base_token)
		: addWatchlistEntry(entries, input);
}

function sameWatchlist(left: WatchlistEntry[], right: WatchlistEntry[]): boolean {
	if (left === right) return true;
	if (left.length !== right.length) return false;
	return left.every((entry, index) => {
		const other = right[index];
		return (
			other !== undefined &&
			entry.chain === other.chain &&
			entry.base_token === other.base_token &&
			entry.symbol === other.symbol &&
			entry.name === other.name &&
			entry.added_at === other.added_at
		);
	});
}

type Listener = () => void;

const EMPTY: WatchlistEntry[] = [];
const listeners = new Set<Listener>();
let snapshot: WatchlistEntry[] = EMPTY;
let loaded = false;
let storageListenerAttached = false;

function emit() {
	for (const listener of [...listeners]) listener();
}

function replaceSnapshot(next: WatchlistEntry[]) {
	if (sameWatchlist(snapshot, next)) return;
	snapshot = next;
	emit();
}

function onStorage(event: StorageEvent) {
	if (event.key !== null && event.key !== WATCHLIST_STORAGE_KEY) return;
	replaceSnapshot(readWatchlist());
}

export function subscribeWatchlist(listener: Listener): () => void {
	if (!loaded) {
		loaded = true;
		replaceSnapshot(readWatchlist());
	}
	listeners.add(listener);
	if (typeof window !== "undefined" && !storageListenerAttached) {
		window.addEventListener("storage", onStorage);
		storageListenerAttached = true;
	}
	return () => {
		listeners.delete(listener);
		if (listeners.size === 0 && storageListenerAttached && typeof window !== "undefined") {
			window.removeEventListener("storage", onStorage);
			storageListenerAttached = false;
		}
	};
}

export function getWatchlistSnapshot(): WatchlistEntry[] {
	return snapshot;
}

export function getWatchlistServerSnapshot(): WatchlistEntry[] {
	// The server has no browser storage. Every star starts unpressed and reconciles on
	// mount, so server HTML and the hydration render agree.
	return EMPTY;
}

export function toggleWatchlist(input: WatchlistInput): WatchlistToggleResult {
	const wasStarred = isWatchlisted(snapshot, input.chain, input.base_token);
	const next = toggleWatchlistEntry(snapshot, input);
	const full = !wasStarred && next === snapshot && snapshot.length >= WATCHLIST_MAX_ENTRIES;
	const persisted = writeWatchlist(next);
	loaded = true;
	replaceSnapshot(next);
	return {
		added: !wasStarred && isWatchlisted(next, input.chain, input.base_token),
		persisted,
		full,
	};
}
