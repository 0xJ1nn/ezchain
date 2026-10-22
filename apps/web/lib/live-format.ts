// Display-only formatters for live API decimal strings.
//
// These functions format values the server already computed. They never derive PnL,
// exposure or a quote (CONTRACT I10). Unknown values print an em dash, never zero.

import { MINUS, pct } from "./format";

export const DASH = "\u2014";

export function parseDecimal(value: string | null | undefined): number | null {
	if (value === null || value === undefined || value === "") return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

export function formatPrice(value: string | null | undefined): string {
	const n = parseDecimal(value);
	if (n === null) return DASH;
	if (n === 0) return "0";
	if (Math.abs(n) >= 0.000001) {
		return String(Number(n.toPrecision(5)));
	}
	return n
		.toFixed(10)
		.replace(/0+$/, "")
		.replace(/\.$/, "");
}

export function formatUsdPrice(value: string | null | undefined): string {
	const formatted = formatPrice(value);
	return formatted === DASH ? DASH : `$${formatted}`;
}

export function formatUsd(value: string | null | undefined): string {
	const n = parseDecimal(value);
	if (n === null) return DASH;
	const abs = Math.abs(n);
	const sign = n < 0 ? MINUS : "";
	// A sub-cent value is still a value. Printing $0.00 for a real position would be a
	// false zero; show enough decimals to distinguish it from no value at all.
	if (abs > 0 && abs < 0.01) {
		return `${sign}$${abs.toLocaleString("en-US", {
			minimumFractionDigits: 2,
			maximumFractionDigits: 8,
		})}`;
	}
	return `${sign}$${abs.toLocaleString("en-US", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	})}`;
}

export function formatUsdCompact(value: string | null | undefined): string {
	const n = parseDecimal(value);
	if (n === null) return DASH;
	const abs = Math.abs(n);
	const sign = n < 0 ? MINUS : "";
	if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(2)}B`;
	if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
	if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
	return `${sign}$${abs.toFixed(2)}`;
}

export function formatAmount(value: string | null | undefined): string {
	const n = parseDecimal(value);
	if (n === null) return DASH;
	// Token quantities are not dollars: 0.000419 must not render as 0. Keep the familiar
	// two-decimal display for whole units and add precision only below one.
	if (Math.abs(n) >= 1) {
		return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
	}
	return n.toLocaleString("en-US", {
		minimumFractionDigits: 0,
		maximumFractionDigits: 20,
	});
}

export function changeText(value: string | null | undefined): string {
	const n = parseDecimal(value);
	return n === null ? DASH : pct(n);
}

export function changeTone(value: string | null | undefined): "ok" | "err" | "muted" {
	const n = parseDecimal(value);
	if (n === null) return "muted";
	return n < 0 ? "err" : "ok";
}

export function ageSeconds(iso: string | null | undefined, now = Date.now()): number | null {
	if (!iso) return null;
	const observed = Date.parse(iso);
	if (!Number.isFinite(observed)) return null;
	return Math.max(0, Math.floor((now - observed) / 1000));
}

export function ageLabel(iso: string | null | undefined, now = Date.now()): string {
	const seconds = ageSeconds(iso, now);
	if (seconds === null) return DASH;
	if (seconds < 60) return `${seconds}s`;
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `${hours}h`;
	const days = Math.floor(hours / 24);
	if (days < 30) return `${days}d`;
	return `${Math.floor(days / 365)}y`;
}

/** Pair age is a listing age, not quote freshness. New pairs are called out. */
export function pairAgeLabel(iso: string | null | undefined, now = Date.now()): string {
	const seconds = ageSeconds(iso, now);
	if (seconds === null) return DASH;
	if (seconds < 60 * 60) return `new · ${ageLabel(iso, now)}`;
	return ageLabel(iso, now);
}

export function sourceLabel(name: string | null | undefined): string {
	if (!name) return "unknown";
	const known: Record<string, string> = {
		dexscreener: "DexScreener",
		jupiter: "Jupiter",
		fixture: "Fixture",
	};
	return known[name.toLowerCase()] ?? name;
}

export function quoteKindLabel(kind: string | null | undefined): string {
	if (!kind) return DASH;
	if (kind === "paper_mark") return "PAPER · MARK";
	if (kind === "jupiter") return "PAPER · JUPITER";
	return `PAPER · ${kind.toUpperCase()}`;
}

export function shortAddress(address: string | null | undefined): string {
	if (!address) return DASH;
	if (address.length <= 14) return address;
	return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

export function initials(symbol: string): string {
	return symbol.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?";
}

export function chainInitial(name: string): string {
	return name.trim().charAt(0).toUpperCase() || "?";
}
