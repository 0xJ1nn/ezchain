// Shared display types for the Trade screen. The chart and the exits panel are one
// thing: they edit the same exit lines, and the API reads the same numbers when it
// arms protection. Nothing here is a quote, a ledger value or a PnL computation.

export type ChartType = "candles" | "line" | "area";

export interface ExitTarget {
	/** Absolute USD price of the target. */
	p: number;
	/** Share of the position closed at this target, in percent. */
	q: number;
}

export interface ExitLines {
	entry: number | null;
	/** Stop-loss price. The chart keeps it strictly below entry. */
	sl: number | null;
	tp: ExitTarget[];
	trail: number | null;
}

/** A paper fill drawn on the chart as a B or S marker. */
export interface FillMarker {
	index: number;
	side: "B" | "S";
}

export interface ChartOverlays {
	e1: boolean;
	e2: boolean;
	vol: boolean;
	ath: boolean;
}

export const EMPTY_LINES: ExitLines = { entry: null, sl: null, tp: [], trail: null };
