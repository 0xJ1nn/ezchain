"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import type { Candle } from "@/lib/api";
import { fmt, pct, usd } from "./format";
import type { ChartOverlays, ChartType, ExitLines, FillMarker } from "./types";
import styles from "./trade.module.css";

// Port of the approved brand chart. The geometry is copied verbatim: axis width 76,
// bottom 22, volume 17% of height, legend pad 40, candle body 72% of the slot,
// EMA 20 and 50, six price ticks, eight time labels, 22 px pills stacked 24 px
// apart, a 20 px last-price tag and the same drag clamping. The component only
// scales real OHLCV values from the candle API into that structure; it invents no
// prices, colours or states.

const W = 900;
const H = 460;
const AX = 76;
const BT = 22;
const VOL = Math.round(H * 0.17);
const LEGEND = 40;
const PH = H - BT - VOL - LEGEND;
const PILL_H = 22;
const PILL_STACK = 24;
const BODY = 0.72;

const DEFAULT_TIMES = ["09:00", "11:00", "13:00", "15:00", "17:00", "19:00", "21:00", "23:00"];

interface Row {
	key: string;
	open: number;
	high: number;
	low: number;
	close: number;
	volume: number | null;
	openTime: string;
}

function num(value: string): number | null {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function timeLabels(rows: Row[]): string[] {
	if (rows.length < 2) return DEFAULT_TIMES;
	const first = Date.parse(rows[0]!.openTime);
	const last = Date.parse(rows[rows.length - 1]!.openTime);
	if (!Number.isFinite(first) || !Number.isFinite(last) || last <= first) return DEFAULT_TIMES;
	return Array.from({ length: 8 }, (_, i) => {
		const d = new Date(first + ((last - first) * i) / 7);
		return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
	});
}

function ema(values: number[], period: number): number[] {
	const a = 2 / (period + 1);
	let e = values[0] ?? 0;
	return values.map((v) => (e = v * a + e * (1 - a)));
}

function linePath(values: number[], x: (i: number) => number, y: (p: number) => number): string {
	return values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
}

export function TradeChart({
	candles,
	symbol,
	timeframe,
	lines,
	markers,
	size,
	type,
	overlays,
	volume24hUsd,
	onChange,
	timeframeControl,
}: {
	candles: Candle[];
	symbol: string;
	timeframe: string;
	lines: ExitLines;
	markers: FillMarker[];
	/** Position size in base units, used only for the chart's "$ from entry". Null stays "—". */
	size: number | null;
	type: ChartType;
	overlays: ChartOverlays;
	volume24hUsd: number | null;
	onChange: (lines: ExitLines) => void;
	timeframeControl?: ReactNode;
}) {
	const [chartType, setChartType] = useState<ChartType>(type);
	const [shown, setShown] = useState<ChartOverlays>(overlays);
	const [active, setActive] = useState<number | null>(null);
	const [dragging, setDragging] = useState<string | null>(null);
	const svgRef = useRef<SVGSVGElement | null>(null);
	const crossRef = useRef<SVGGElement | null>(null);
	const dragRef = useRef<string | null>(null);
	const wrapRef = useRef<HTMLDivElement | null>(null);
	const [compact, setCompact] = useState(false);

	// The board switches each order pill to its compact label below 680 px of chart width.
	useEffect(() => {
		const wrap = wrapRef.current;
		if (!wrap || typeof ResizeObserver === "undefined") return;
		const measure = () => setCompact(wrap.clientWidth < 680);
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(wrap);
		return () => observer.disconnect();
	}, []);

	const rows = useMemo<Row[]>(
		() =>
			candles
				.map((c) => ({
					key: c.open_time,
					open: num(c.open) ?? Number.NaN,
					high: num(c.high) ?? Number.NaN,
					low: num(c.low) ?? Number.NaN,
					close: num(c.close) ?? Number.NaN,
					volume: c.volume === null ? null : num(c.volume),
					openTime: c.open_time,
				}))
				.filter((r) => [r.open, r.high, r.low, r.close].every((v) => Number.isFinite(v))),
		[candles],
	);

	const geometry = useMemo(() => {
		if (rows.length === 0) return null;
		const closes = rows.map((r) => r.close);
		const e20 = ema(closes, 20);
		const e50 = ema(closes, 50);
		const ath = shown.ath ? Math.max(...rows.map((r) => r.high)) : null;
		const values = [
			...(ath === null ? [] : [ath]),
			...rows.map((r) => r.high),
			...rows.map((r) => r.low),
			...(lines.entry === null ? [] : [lines.entry]),
			...(lines.sl === null ? [] : [lines.sl]),
			...lines.tp.map((t) => t.p),
			...(lines.trail === null ? [] : [lines.trail]),
		].filter((v) => Number.isFinite(v));
		let lo = values.length ? Math.min(...values) : Math.min(...rows.map((r) => r.low));
		let hi = values.length ? Math.max(...values) : Math.max(...rows.map((r) => r.high));
		if (hi === lo) hi = lo + 1;
		const pad = (hi - lo) * 0.08;
		lo -= pad;
		hi += pad;
		const n = rows.length;
		const cw = (W - AX) / n;
		const x = (i: number) => i * cw + cw / 2;
		const y = (p: number) => LEGEND + (1 - (p - lo) / (hi - lo)) * PH;
		const py = (Y: number) => lo + (1 - (Y - LEGEND) / PH) * (hi - lo);
		const vmax = Math.max(1, ...rows.map((r) => r.volume ?? 0));
		const vy0 = LEGEND + PH + 6;
		const ticks = Array.from({ length: 6 }, (_, i) => lo + ((hi - lo) * (i + 0.5)) / 6);
		const times = timeLabels(rows);
		return { e20, e50, ath, lo, hi, n, cw, x, y, py, vmax, vy0, ticks, times };
	}, [rows, lines, shown.ath]);

	if (rows.length === 0 || geometry === null) {
		return (
			<div id="chart" className={styles.chartCard}>
				<div className={styles.chartEmpty} role="status">
					No candle series — nothing is plotted from a failed or empty read.
				</div>
			</div>
		);
	}

	const { e20, e50, ath, cw, x, y, py, vmax, vy0, ticks, times, n } = geometry;
	const last = rows[n - 1]!;
	const lastUp = last.close >= rows[Math.max(0, n - 2)]!.close;
	const legendIndex = active ?? n - 1;
	const legendRow = rows[legendIndex]!;
	const legendChange = (legendRow.close / legendRow.open - 1) * 100;
	const base = lines.entry ?? last.close;

	// Pills never overlap: sort by exact line position, then push close ones 24 px apart.
	const orderLines: Array<{ k: string; p: number; label: string }> = [];
	if (lines.entry !== null) orderLines.push({ k: "entry", p: lines.entry, label: "Entry" });
	if (lines.sl !== null) orderLines.push({ k: "sl", p: lines.sl, label: "SL" });
	lines.tp.forEach((t, i) =>
		orderLines.push({ k: `tp${i}`, p: t.p, label: `TP${i + 1}${t.q ? ` · ${t.q}%` : ""}` }),
	);
	if (lines.trail !== null) orderLines.push({ k: "trail", p: lines.trail, label: "Trail" });
	const pillY = new Map<string, number>();
	let previousY: number | null = null;
	orderLines
		.map((l) => ({ k: l.k, y: y(l.p) }))
		.sort((a, b) => a.y - b.y)
		.forEach(({ k, y: position }) => {
			const placed = previousY === null ? position : Math.max(position, previousY + PILL_STACK);
			pillY.set(k, placed);
			previousY = placed;
		});

	interface OrderLine {
		k: string;
		p: number;
		label: string;
	}

	function pillText(line: OrderLine): string {
		if (line.k === "entry" || compact) return `${line.label} ${fmt(line.p)}`;
		const change = (line.p / base - 1) * 100;
		const money = size === null ? null : (line.p - base) * size;
		return `${line.label}  ${fmt(line.p)}  ${pct(change)}  ${usd(money)}`;
	}

	function moveLine(k: string, clientY: number) {
		const svg = svgRef.current;
		if (!svg) return;
		const rect = svg.getBoundingClientRect();
		const p = py(((clientY - rect.top) * H) / rect.height);
		const next: ExitLines = { ...lines, tp: lines.tp.map((t) => ({ ...t })) };
		if (k === "sl") next.sl = Math.min(p, base * 0.999);
		else if (k === "trail") next.trail = p;
		else if (k.startsWith("tp")) {
			const index = Number(k.slice(2));
			if (next.tp[index]) next.tp[index]!.p = Math.max(p, base * 1.001);
		} else return;
		onChange(next);
	}

	function onLineDown(event: ReactPointerEvent<SVGGElement>, k: string) {
		if (k === "entry") return;
		event.preventDefault();
		dragRef.current = k;
		setDragging(k);
		svgRef.current?.setPointerCapture(event.pointerId);
		if (crossRef.current) crossRef.current.style.display = "none";
	}

	function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
		const svg = svgRef.current;
		if (!svg) return;
		const rect = svg.getBoundingClientRect();
		const mx = ((event.clientX - rect.left) * W) / rect.width;
		const my = ((event.clientY - rect.top) * H) / rect.height;
		if (dragRef.current) {
			moveLine(dragRef.current, event.clientY);
			return;
		}
		const cross = crossRef.current;
		if (!cross || mx > W - AX || my < LEGEND || my > LEGEND + PH) {
			if (cross) cross.style.display = "none";
			return;
		}
		const index = Math.max(0, Math.min(n - 1, Math.floor(mx / cw)));
		cross.style.display = "";
		const vertical = cross.querySelector<SVGLineElement>("line.v");
		const horizontal = cross.querySelector<SVGLineElement>("line.h");
		const tag = cross.querySelector<SVGRectElement>("rect.tag");
		const tagText = cross.querySelector<SVGTextElement>("text.tagt");
		vertical?.setAttribute("x1", String(x(index)));
		vertical?.setAttribute("x2", String(x(index)));
		horizontal?.setAttribute("y1", String(my));
		horizontal?.setAttribute("y2", String(my));
		tag?.setAttribute("y", String(my - 10));
		if (tagText) {
			tagText.setAttribute("y", String(my + 4));
			tagText.textContent = fmt(py(my));
		}
		setActive(index);
	}

	function endDrag() {
		if (!dragRef.current) return;
		dragRef.current = null;
		setDragging(null);
	}

	const lineControls = [
		{ key: "e1", label: "EMA 20", color: "var(--blue)" },
		{ key: "e2", label: "EMA 50", color: "var(--amber)" },
		{ key: "vol", label: "VOL", color: "var(--green)" },
		{ key: "ath", label: "ATH", color: "var(--amber)" },
	] as const;

	const stats: Array<[string, string]> = [
		["Last", fmt(last.close)],
		["24h high", fmt(Math.max(...rows.map((r) => r.high)))],
		["24h low", fmt(Math.min(...rows.map((r) => r.low)))],
		["24h volume", volume24hUsd === null ? "—" : `$${(volume24hUsd / 1e9).toFixed(2)}B`],
		["All-time high", ath === null ? "—" : fmt(ath)],
		["Open P&L", lines.entry === null || size === null ? "—" : usd((last.close - lines.entry) * size)],
	];

	return (
		<div id="chart" className={styles.chartCard}>
			<div className={styles.ctool}>
				{timeframeControl}
				<div className={styles.seg} role="group" aria-label="Chart type">
					{(["candles", "line", "area"] as ChartType[]).map((option) => (
						<button
							key={option}
							type="button"
							className={chartType === option ? styles.segOn : undefined}
							aria-pressed={chartType === option}
							data-testid={`chart-type-${option}`}
							onClick={() => setChartType(option)}
						>
							{option[0]!.toUpperCase() + option.slice(1)}
						</button>
					))}
				</div>
				<div className={styles.indicators} role="group" aria-label="Chart overlays">
					{lineControls.map((control) => (
						<button
							key={control.key}
							type="button"
							className={shown[control.key] ? styles.indOn : undefined}
							style={{ ["--c" as string]: control.color }}
							aria-pressed={shown[control.key]}
							data-testid={`chart-overlay-${control.key}`}
							onClick={() => setShown((current) => ({ ...current, [control.key]: !current[control.key] }))}
						>
							{control.label}
						</button>
					))}
				</div>
			</div>

			<div className={styles.ezcWrap} ref={wrapRef} data-compact={compact ? "true" : "false"}>
				<div className={styles.ezcLegend}>
					<b>{symbol}</b>
					<span className={styles.tf}>{timeframe}</span>
					<span>
						O <i>{fmt(legendRow.open)}</i>
					</span>
					<span>
						H <i>{fmt(legendRow.high)}</i>
					</span>
					<span>
						L <i>{fmt(legendRow.low)}</i>
					</span>
					<span>
						C <i className={legendChange >= 0 ? styles.up : styles.down}>
							{fmt(legendRow.close)} {pct(legendChange)}
						</i>
					</span>
					<span className={styles.e1}>
						EMA 20 <i>{fmt(e20[legendIndex] ?? Number.NaN)}</i>
					</span>
					<span className={styles.e2}>
						EMA 50 <i>{fmt(e50[legendIndex] ?? Number.NaN)}</i>
					</span>
				</div>

				<svg
					ref={svgRef}
					className={styles.ezc}
					viewBox={`0 0 ${W} ${H}`}
					role="img"
					aria-label={`OHLC candles, ${symbol}, ${timeframe}`}
					onPointerMove={onPointerMove}
					onPointerUp={endDrag}
					onPointerCancel={endDrag}
					onPointerLeave={() => {
						if (crossRef.current) crossRef.current.style.display = "none";
						setActive(null);
					}}
				>
					<g className={styles.grid}>
						{ticks.map((t, i) => (
							<line key={`p${i}`} x1={0} x2={W - AX} y1={y(t)} y2={y(t)} />
						))}
						{times.map((_, i) => (
							<line
								key={`t${i}`}
								y1={LEGEND}
								y2={vy0 + VOL}
								x1={((i + 0.5) * (W - AX)) / times.length}
								x2={((i + 0.5) * (W - AX)) / times.length}
							/>
						))}
					</g>
					<g className={styles.axis}>
						{ticks.map((t, i) => (
							<text key={`pt${i}`} x={W - AX + 10} y={y(t) + 4}>
								{fmt(t)}
							</text>
						))}
						{times.map((t, i) => (
							<text
								key={`tt${i}`}
								x={((i + 0.5) * (W - AX)) / times.length}
								y={H - 6}
								textAnchor="middle"
							>
								{t}
							</text>
						))}
					</g>
					{shown.vol && (
						<g className={styles.vol}>
							{rows.map((r, i) => (
								<rect
									key={r.key}
									className={r.close >= r.open ? styles.up : styles.down}
									x={x(i) - cw * 0.34}
									width={cw * 0.68}
									y={vy0 + VOL - ((r.volume ?? 0) / vmax) * VOL}
									height={((r.volume ?? 0) / vmax) * VOL}
								/>
							))}
						</g>
					)}
					{ath !== null && (
						<g className={styles.ath}>
							<line x1={0} x2={W - AX} y1={y(ath)} y2={y(ath)} />
							<text x={8} y={y(ath) - 6}>
								ATH {fmt(ath)}
							</text>
						</g>
					)}
					{shown.e1 && <path className={`${styles.ema} ${styles.e1}`} d={linePath(e20, x, y)} />}
					{shown.e2 && <path className={`${styles.ema} ${styles.e2}`} d={linePath(e50, x, y)} />}
					{chartType === "candles" ? (
						<g className={styles.candles}>
							{rows.map((r, i) => {
								const up = r.close >= r.open;
								const top = y(Math.max(r.open, r.close));
								const bottom = y(Math.min(r.open, r.close));
								return (
									<g key={r.key} className={up ? styles.up : styles.down}>
										<line x1={x(i)} x2={x(i)} y1={y(r.high)} y2={y(r.low)} />
										<rect
											x={x(i) - cw * (BODY / 2)}
											width={cw * BODY}
											y={top}
											height={Math.max(1, bottom - top)}
											rx={1}
										/>
									</g>
								);
							})}
						</g>
					) : (
						<g className={styles.lineview}>
							<path
								className={styles.linePath}
								d={linePath(
									rows.map((r) => r.close),
									x,
									y,
								)}
							/>
						</g>
					)}
					{markers.map((marker) => {
						const row = rows[marker.index];
						if (!row) return null;
						const buy = marker.side === "B";
						const yy = buy ? y(row.low) + 14 : y(row.high) - 14;
						return (
							<g key={`${marker.index}-${marker.side}`} className={buy ? styles.mkrB : styles.mkrS}>
								<circle cx={x(marker.index)} cy={yy} r={8} />
								<text x={x(marker.index)} y={yy + 3.5} textAnchor="middle">
									{marker.side}
								</text>
							</g>
						);
					})}
					<g className={lastUp ? styles.lastUp : styles.lastDown}>
						<line x1={0} x2={W - AX} y1={y(last.close)} y2={y(last.close)} />
						<rect x={W - AX + 2} y={y(last.close) - 10} width={AX - 4} height={20} rx={5} />
						<text x={W - AX + 10} y={y(last.close) + 4}>
							{fmt(last.close)}
						</text>
					</g>
					<g className={styles.olines}>
						{orderLines.map((line) => {
							const cls = line.k.replace(/\d/, "");
							const text = pillText(line);
							const tw = text.length * 6.3 + 20;
							const pyk = pillY.get(line.k) ?? y(line.p);
							return (
								<g
									key={line.k}
									className={`${styles.ol} ${styles[cls] ?? ""} ${dragging === line.k ? styles.dragging : ""}`}
									data-k={line.k}
									data-price={line.p}
									data-testid={`order-line-${line.k}`}
									onPointerDown={(event) => onLineDown(event, line.k)}
								>
									<line className={styles.line} x1={0} x2={W - AX} y1={y(line.p)} y2={y(line.p)} />
									<line className={styles.hit} x1={0} x2={W - AX} y1={y(line.p)} y2={y(line.p)} />
									<g className={styles.pill} transform={`translate(${W - AX - tw - 8} ${pyk - 11})`}>
										<rect width={tw} height={PILL_H} rx={6} />
										<text x={10} y={15} xmlSpace="preserve">
											{text}
										</text>
									</g>
									{line.k !== "entry" && (
										<g className={styles.grip} transform={`translate(${W - AX - tw - 32} ${pyk - 10})`}>
											<rect width={20} height={20} rx={6} />
											<path d="M7 8h6M7 12h6" />
										</g>
									)}
								</g>
							);
						})}
					</g>
					<g ref={crossRef} className={styles.xh} style={{ display: "none" }}>
						<line className={styles.v} y1={LEGEND} y2={vy0 + VOL} />
						<line className={styles.h} x1={0} x2={W - AX} />
						<rect className={styles.tag} x={W - AX + 2} width={AX - 4} height={20} rx={5} />
						<text className={styles.tagt} x={W - AX + 10} />
					</g>
				</svg>
			</div>

			<div className={styles.cstats}>
				{stats.map(([k, value]) => (
					<div key={k}>
						<span>{k}</span>
						<b>{value}</b>
					</div>
				))}
			</div>
		</div>
	);
}
