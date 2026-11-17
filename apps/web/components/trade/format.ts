// Display-only formatting for the Trade screen. Two decimals for readable prices,
// more precision for sub-dollar assets so a real price is never printed as zero.

export function fmt(v: number | null | undefined): string {
	if (v === null || v === undefined || !Number.isFinite(v)) return "—";
	if (Math.abs(v) >= 1) {
		return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	}
	return String(Number(v.toPrecision(5)));
}

export function usd(v: number | null | undefined): string {
	if (v === null || v === undefined || !Number.isFinite(v)) return "—";
	return `${v >= 0 ? "+" : "−"}$${fmt(Math.abs(v))}`;
}

export function pct(v: number | null | undefined): string {
	if (v === null || v === undefined || !Number.isFinite(v)) return "—";
	return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}%`;
}

/** Parses a USD price input. Returns null for blank, unknown or non-positive text. */
export function readPrice(raw: string): number | null {
	const value = Number(raw.replace(/,/g, "").trim());
	return Number.isFinite(value) && value > 0 ? value : null;
}
