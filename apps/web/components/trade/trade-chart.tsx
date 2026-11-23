"use client";

import { useId, useRef, useState, type PointerEvent as ReactPointerEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import type { Candle } from "@/lib/api";
import { formatPrice } from "@/lib/live-format";

// The Trade chart is drawn only from the closed bars the API returned. Price overlays
// (EMAs, the all-time high of the loaded series, buy and sell markers) are computed from
// those bars; exit lines are the caller's state. Nothing here invents a price or a fill.

export type ChartType = "candles" | "line" | "area";
export type ExitLineKey = "stop" | "trail" | `tp-${number}`;

export interface ExitLineValues {
	stop: number | null;
	targets: (number | null)[];
	trail: number | null;
}

export interface TradeMarker {
	index: number;
	side: "buy" | "sell";
}

const W = 900;
const H = 440;
const LEFT = 8;
const RIGHT = 92;
const TOP = 14;

function dec(value: number): string {
	if (!Number.isFinite(value)) return "0";
	const text = value.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
	return text === "" || text === "-0" ? "0" : text;
}

function num(value: string): number | null {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function ema(values: number[], period: number): number[] {
	if (values.length === 0) return [];
	const out: number[] = [];
	let previous = 0;
	for (let index = 0; index < values.length; index += 1) {
		if (index < period) {
			const prefix = values.slice(0, index + 1);
			previous = prefix.reduce((sum, value) => sum + value, 0) / prefix.length;
		} else {
			const factor = 2 / (period + 1);
			previous = values[index]! * factor + previous * (1 - factor);
		}
		out.push(previous);
	}
	return out;
}

function clock(iso: string): string {
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return "";
	return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
}

type Line = { key: ExitLineKey; label: string; price: number; css: string };

export function TradeChart({
	candles,
	symbol,
	timeframe,
	type,
	showEma20,
	showEma50,
	showVolume,
	showAth,
	entry,
	markers,
	lines,
	onLineChange,
}: {
	candles: Candle[];
	symbol: string;
	timeframe: string;
	type: ChartType;
	showEma20: boolean;
	showEma50: boolean;
	showVolume: boolean;
	showAth: boolean;
	entry: number | null;
	markers: TradeMarker[];
	lines: ExitLineValues;
	onLineChange: (key: ExitLineKey, price: number) => void;
}) {
	const id = useId().replace(/[^a-zA-Z0-9]/g, "");
	const svgRef = useRef<SVGSVGElement | null>(null);
	const dragRef = useRef<ExitLineKey | null>(null);
	const [hover, setHover] = useState<number | null>(null);

	const rows = candles
		.map((candle) => ({
			key: candle.open_time,
			open: num(candle.open),
			high: num(candle.high),
			low: num(candle.low),
			close: num(candle.close),
			volume: candle.volume === null ? null : num(candle.volume),
			openTime: candle.open_time,
		}))
		.filter(
			(row): row is {
				key: string;
				open: number;
				high: number;
				low: number;
				close: number;
				volume: number | null;
				openTime: string;
			} =>
				row.open !== null &&
				row.high !== null &&
				row.low !== null &&
				row.close !== null,
		);
	if (rows.length === 0) return null;

	const priceBottom = showVolume ? 330 : 392;
	const volTop = 342;
	const volBottom = 404;
	const plotW = W - LEFT - RIGHT;

	const lineList: Line[] = [];
	if (lines.stop !== null) lineList.push({ key: "stop", label: "SL", price: lines.stop, css: "level-stop" });
	lines.targets.forEach((price, index) => {
		if (price !== null) lineList.push({ key: `tp-${index}`, label: `TP${index + 1}`, price, css: "level" });
	});
	if (lines.trail !== null) lineList.push({ key: "trail", label: "TRAIL", price: lines.trail, css: "level-trail" });

	const closes = rows.map((row) => row.close);
	const ema20 = ema(closes, 20);
	const ema50 = ema(closes, 50);
	const ath = Math.max(...rows.map((row) => row.high));
	const maxVolume = rows.reduce((max, row) => Math.max(max, row.volume ?? 0), 0);

	let low = Math.min(...rows.map((row) => row.low));
	let high = Math.max(...rows.map((row) => row.high));
	for (const value of [entry, ath, ...lineList.map((line) => line.price)]) {
		if (value !== null) {
			low = Math.min(low, value);
			high = Math.max(high, value);
		}
	}
	if (showEma20) {
		low = Math.min(low, ...ema20);
		high = Math.max(high, ...ema20);
	}
	if (showEma50) {
		low = Math.min(low, ...ema50);
		high = Math.max(high, ...ema50);
	}
	const pad = (high - low || Math.abs(high) * 0.001 || 1) * 0.06;
	low -= pad;
	high += pad;
	const span = high - low;

	const step = plotW / rows.length;
	const center = (index: number) => LEFT + index * step + step / 2;
	const y = (price: number) => TOP + ((high - price) / span) * (priceBottom - TOP);
	const priceAt = (py: number) => high - ((py - TOP) / (priceBottom - TOP)) * span;
	const path = (values: number[]) =>
		values.map((value, index) => `${index ? "L" : "M"}${center(index).toFixed(1)} ${y(value).toFixed(1)}`).join(" ");
	const bodyWidth = Math.max(1, step * 0.6);

	const readoutIndex = hover ?? rows.length - 1;
	const readout = rows[readoutIndex]!;

	function toSvgY(clientY: number): number {
		const rect = svgRef.current?.getBoundingClientRect();
		if (!rect || rect.height === 0) return TOP;
		return ((clientY - rect.top) / rect.height) * H;
	}

	function moveLine(key: ExitLineKey, clientY: number) {
		const next = priceAt(toSvgY(clientY));
		if (Number.isFinite(next)) onLineChange(key, Number(dec(next)));
	}

	function onLinePointerDown(event: ReactPointerEvent<SVGGElement>, key: ExitLineKey) {
		event.preventDefault();
		dragRef.current = key;
		event.currentTarget.setPointerCapture(event.pointerId);
		setHover(null);
	}

	function onLinePointerMove(event: ReactPointerEvent<SVGGElement>, key: ExitLineKey) {
		if (dragRef.current === key) moveLine(key, event.clientY);
	}

	function onLinePointerUp(event: ReactPointerEvent<SVGGElement>) {
		dragRef.current = null;
		if (event.currentTarget.hasPointerCapture(event.pointerId)) {
			event.currentTarget.releasePointerCapture(event.pointerId);
		}
	}

	function onLineKeyDown(event: ReactKeyboardEvent<SVGGElement>, key: ExitLineKey, price: number) {
		const stepPrice = span / 80;
		let next: number | null = null;
		if (event.key === "ArrowUp") next = price + stepPrice;
		if (event.key === "ArrowDown") next = price - stepPrice;
		if (next === null) return;
		event.preventDefault();
		onLineChange(key, Number(dec(next)));
	}

	return (
		<div className="chart" data-testid="trade-chart-wrap">
			<svg
				ref={svgRef}
				viewBox={`0 0 ${W} ${H}`}
				role="img"
				aria-label={`Trade chart, ${symbol}, ${timeframe}`}
				data-testid="trade-chart"
			>
				<title id={`trade-title-${id}`}>{`Trade chart, ${symbol}, ${timeframe}`}</title>
				{[0, 0.5, 1].map((fraction) => {
					const price = low + span * fraction;
					return (
						<g key={fraction} pointerEvents="none">
							<line className="gridline" x1={LEFT} y1={y(price)} x2={LEFT + plotW} y2={y(price)} />
							<text className="axis-text" x={LEFT + plotW + 6} y={y(price) + 3}>
								{formatPrice(dec(price))}
							</text>
						</g>
					);
				})}
				<g pointerEvents="none">
					<line className="axis" x1={LEFT + plotW} y1={TOP} x2={LEFT + plotW} y2={priceBottom} />
					{showVolume && (
						<line className="axis" x1={LEFT} y1={volBottom} x2={LEFT + plotW} y2={volBottom} />
					)}
				</g>

				{type === "candles" ? (
					<g pointerEvents="none">
						{rows.map((row, index) => {
							const cx = center(index);
							const up = row.close >= row.open;
							const top = y(Math.max(row.open, row.close));
							const bottom = y(Math.min(row.open, row.close));
							const volumeHeight =
								row.volume !== null && maxVolume > 0
									? Math.max(1, (row.volume / maxVolume) * (volBottom - volTop))
									: null;
							return (
								<g key={row.key}>
									<line
										className={up ? "candle-wick up" : "candle-wick down"}
										x1={cx}
										y1={y(row.high)}
										x2={cx}
										y2={y(row.low)}
									/>
									<rect
										className={up ? "candle-up" : "candle-down"}
										x={cx - bodyWidth / 2}
										y={top}
										width={bodyWidth}
										height={Math.max(1, bottom - top)}
									/>
									{showVolume && volumeHeight !== null && (
										<rect
											className={up ? "vol vol-up" : "vol vol-down"}
											x={cx - bodyWidth / 2}
											y={volBottom - volumeHeight}
											width={bodyWidth}
											height={volumeHeight}
										/>
									)}
								</g>
							);
						})}
					</g>
				) : (
					<g pointerEvents="none">
						{type === "area" && (
							<path
								data-testid="chart-area"
								className="area"
								d={`${path(closes)}L${center(rows.length - 1).toFixed(1)} ${priceBottom}L${center(0).toFixed(1)} ${priceBottom}Z`}
							/>
						)}
						<path
							data-testid="chart-line"
							className="series"
							d={path(closes)}
						/>
						{showVolume &&
							rows.map((row, index) => {
								if (row.volume === null || maxVolume <= 0) return null;
								const height = Math.max(1, (row.volume / maxVolume) * (volBottom - volTop));
								return (
									<rect
										key={row.key}
										className="vol vol-up"
										x={center(index) - bodyWidth / 2}
										y={volBottom - height}
										width={bodyWidth}
										height={height}
									/>
								);
							})}
					</g>
				)}

				<g pointerEvents="none">
					{showEma20 && <path data-testid="chart-ema20" className="series" d={path(ema20)} />}
					{showEma50 && <path data-testid="chart-ema50" className="series-2" d={path(ema50)} />}
					{showAth && (
						<g data-testid="chart-ath">
							<line className="level" x1={LEFT} y1={y(ath)} x2={LEFT + plotW} y2={y(ath)} />
							<text className="level-label" x={LEFT + 4} y={y(ath) - 5}>
								ATH {formatPrice(dec(ath))}
							</text>
						</g>
					)}
					{entry !== null && (
						<line className="level-entry" x1={LEFT} y1={y(entry)} x2={LEFT + plotW} y2={y(entry)} />
					)}
				</g>

				<g pointerEvents="none">
					{markers.map((marker, index) => {
						const cx = center(marker.index);
						const cy = y(rows[marker.index]?.close ?? entry ?? low);
						const points =
							marker.side === "buy"
								? `${cx},${cy + 10} ${cx - 6},${cy + 20} ${cx + 6},${cy + 20}`
								: `${cx},${cy - 10} ${cx - 6},${cy - 20} ${cx + 6},${cy - 20}`;
						return (
							<polygon
								key={`${marker.side}-${index}`}
								data-testid={`chart-marker-${marker.side}`}
								className={marker.side === "buy" ? "candle-up" : "candle-down"}
								points={points}
							/>
						);
					})}
				</g>

				{/* The crosshair overlay sits below the exit lines so dragging still wins. */}
				<rect
					x={LEFT}
					y={TOP}
					width={plotW}
					height={priceBottom - TOP}
					fill="transparent"
					onPointerMove={(event) => {
						const rect = svgRef.current?.getBoundingClientRect();
						if (!rect) return;
						const svgX = ((event.clientX - rect.left) / rect.width) * W;
						const index = Math.max(0, Math.min(rows.length - 1, Math.floor((svgX - LEFT) / step)));
						setHover(index);
					}}
					onPointerLeave={() => setHover(null)}
				/>
				{hover !== null && (
					<g data-testid="chart-crosshair" pointerEvents="none">
						<line className="crosshair" x1={center(hover)} y1={TOP} x2={center(hover)} y2={priceBottom} />
						<text className="num-label" x={center(hover) + 4} y={priceBottom - 4}>
							{formatPrice(dec(rows[hover]!.close))}
						</text>
					</g>
				)}

				<g pointerEvents="none">
					{rows.length > 1 && (
						<>
							<text className="axis-text" x={LEFT} y={H - 8}>
								{clock(rows[0]!.openTime)}
							</text>
							<text className="axis-text" x={LEFT + plotW} y={H - 8} textAnchor="end">
								{clock(rows[rows.length - 1]!.openTime)}
							</text>
						</>
					)}
				</g>

				{lineList.map((line) => (
					<g
						key={line.key}
						role="slider"
						tabIndex={0}
						aria-label={`${line.label} price`}
						aria-valuemin={Number(dec(low))}
						aria-valuemax={Number(dec(high))}
						aria-valuenow={Number(dec(line.price))}
						aria-valuetext={formatPrice(dec(line.price))}
						data-testid={`exit-line-${line.key}`}
						onPointerDown={(event) => onLinePointerDown(event, line.key)}
						onPointerMove={(event) => onLinePointerMove(event, line.key)}
						onPointerUp={onLinePointerUp}
						onKeyDown={(event) => onLineKeyDown(event, line.key, line.price)}
						style={{ cursor: "ns-resize", outline: "none" }}
					>
						<line
							x1={LEFT}
							y1={y(line.price)}
							x2={LEFT + plotW}
							y2={y(line.price)}
							stroke="transparent"
							strokeWidth={14}
						/>
						<line className={line.css} x1={LEFT} y1={y(line.price)} x2={LEFT + plotW} y2={y(line.price)} />
						<text className="level-label" x={LEFT + 4} y={y(line.price) - 5}>
							{line.label} {formatPrice(dec(line.price))}
						</text>
						<rect
							data-testid={`exit-grip-${line.key}`}
							x={LEFT + plotW - 26}
							y={y(line.price) - 8}
							width={20}
							height={16}
							rx={4}
							className="level-grip"
							style={{ fill: "var(--ink)", opacity: 0.75 }}
						/>
					</g>
				))}
			</svg>
			<div
				className="chart-legend"
				data-testid="chart-ohlc"
				style={{ position: "absolute", right: 96, top: 4 }}
			>
				<span>
					O <i>{formatPrice(dec(readout.open))}</i>
				</span>
				<span>
					H <i>{formatPrice(dec(readout.high))}</i>
				</span>
				<span>
					L <i>{formatPrice(dec(readout.low))}</i>
				</span>
				<span>
					C <i>{formatPrice(dec(readout.close))}</i>
				</span>
				<span>{clock(readout.openTime)} UTC</span>
			</div>
		</div>
	);
}
