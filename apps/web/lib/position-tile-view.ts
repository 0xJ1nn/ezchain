// Project a server PositionView row into the signature tile's one view model.
//
// The API does not return a chart history, a ladder, a trail or a hold budget for a
// position. Those tile zones are rendered as explicitly empty or "—" here: a server row
// is never padded with a drawn line, an invented rung or a made-up percentage.

import type { PositionTileView, TileTone } from "./position-fixtures";
import type { PaperPosition } from "./paper-api";
import { markAge, money, positionKey, quantity } from "./position-view";

function tone(position: PaperPosition): TileTone {
	if (position.mark_usd === null || position.mark_status === "unknown") return "error";
	if (position.unrealized_usd === null) return "flat";
	const value = Number(position.unrealized_usd);
	if (!Number.isFinite(value) || value === 0) return "flat";
	return value > 0 ? "win" : "loss";
}

function pnlTone(position: PaperPosition): "ok" | "err" | "muted" {
	if (position.unrealized_usd === null) return "muted";
	const value = Number(position.unrealized_usd);
	if (!Number.isFinite(value) || value === 0) return "muted";
	return value > 0 ? "ok" : "err";
}

function markTone(position: PaperPosition): "live" | "stale" | "dead" {
	if (position.mark_usd === null || position.mark_status === "unknown") return "dead";
	if (position.mark_status === "stale" || position.mark_status === "future") return "stale";
	return "live";
}

/** One tile per live or fixture server position, using only fields the API returned. */
export function positionToTile(position: PaperPosition): PositionTileView {
	const age = markAge(position);
	const source = position.source?.name ?? "no source returned";
	return {
		tileState: position.mark_usd === null ? "stale" : "riding",
		id: positionKey(position),
		symbol: position.symbol.slice(0, 2).toUpperCase(),
		name: position.symbol,
		sub: `${position.symbol} // ${source} // ${position.chain} ↗`,
		tone: tone(position),
		pnlPct: "—",
		pnlUsd: money(position.unrealized_usd, true),
		pnlTone: pnlTone(position),
		price: money(position.mark_usd),
		priceTone: position.mark_usd === null ? "muted" : undefined,
		mark: `${source} · ${age.label}`,
		markTone: markTone(position),
		markToneClass: position.mark_usd === null ? "err" : undefined,
		tags: [
			{ label: "PAPER" },
			{ label: position.chain, tone: "ghost" },
			{ label: age.label, tone: age.stale || age.unknown ? "warn" : "ghost" },
		],
		chart: {
			ariaLabel: `No chart history returned for ${position.symbol}; no line is drawn`,
			gridY: [],
			levels: [],
			points: "",
			dot: { x: 0, y: 0 },
			axis: [],
			foot: [],
		},
		market: [
			{ k: "Qty", v: quantity(position.quantity), src: "server" },
			{ k: "Value", v: money(position.market_value_usd), src: "server" },
			{ k: "Avg cost", v: money(position.avg_cost_usd), src: "server" },
			{ k: "Mark", v: money(position.mark_usd), src: source },
		],
		money: [
			{ k: "In", v: money(position.avg_cost_usd) },
			{ k: "Value", v: money(position.market_value_usd) },
			{
				k: "Unreal",
				v: money(position.unrealized_usd, true),
				tone: pnlTone(position) === "ok" ? "ok" : pnlTone(position) === "err" ? "err" : undefined,
			},
			{ k: "Realized", v: "—" },
		],
		ladder: {
			kind: "note",
			state: "NOT REPORTED",
			stateTone: "err",
			lastExecutable: "—",
			note: "The API returns no ladder, trail or rung for this position. No step is invented.",
		},
		prov: [
			{
				label: `mark ${position.mark_status} · ${age.label}`,
				tone: age.stale || age.unknown ? "warn" : "ghost",
			},
			{ label: `source ${source}`, tone: "ghost" },
		],
		budget: { kind: "text", cap: "NO HOLD BUDGET RETURNED" },
		actions: [{ label: "⋯", className: "btn sm", disabled: true, ariaLabel: `More actions for ${position.symbol}` }],
		note: "Server position view. Chart, ladder and trail zones stay empty because the API does not return them.",
	};
}
