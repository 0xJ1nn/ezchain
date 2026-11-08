import Link from "next/link";
import { Badge } from "@/components/ui";
import type { Candle, CandleSeries } from "@/lib/api";
import {
	candleErrorCopy,
	candleReasonCopy,
	lastClose,
	type CandleLoad,
} from "@/lib/candle-view";
import {
	DASH,
	ageLabel,
	formatAmount,
	formatPrice,
	formatUsdPrice,
	sourceLabel,
} from "@/lib/live-format";
import styles from "./token-chart.module.css";

const FULL = { width: 900, height: 300, left: 10, right: 88, top: 12, bottom: 26 };
const MINI = { width: 240, height: 56, left: 2, right: 2, top: 5, bottom: 5 };

function numeric(value: string): number | null {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function chartId(series: CandleSeries, compact: boolean): string {
	const suffix = `${series.timeframe}-${compact ? "mini" : "full"}`;
	return `candles-${series.chain}-${series.token_address.slice(-8)}-${suffix}`.replace(
		/[^a-zA-Z0-9_-]/g,
		"",
	);
}

function barTime(iso: string, daily: boolean): string {
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return DASH;
	const options: Intl.DateTimeFormatOptions = daily
		? { month: "short", day: "numeric", timeZone: "UTC" }
		: {
				month: "short",
				day: "numeric",
				hour: "2-digit",
				minute: "2-digit",
				hourCycle: "h23",
				timeZone: "UTC",
			};
	return `${date.toLocaleString("en-US", options)} UTC`;
}

function chartPoints(candles: Candle[], box: typeof FULL) {
	const closes = candles.map((candle) => numeric(candle.close));
	const highs = candles.map((candle) => numeric(candle.high));
	const lows = candles.map((candle) => numeric(candle.low));
	if (closes.some((value) => value === null)) return null;
	const validCloses = closes as number[];
	const validHighs = highs.filter((value): value is number => value !== null);
	const validLows = lows.filter((value): value is number => value !== null);
	const high = Math.max(...validHighs, ...validCloses);
	const low = Math.min(...validLows, ...validCloses);
	const span = high - low || Math.abs(high) * 0.001 || 1;
	const innerWidth = box.width - box.left - box.right;
	const innerHeight = box.height - box.top - box.bottom;
	const x = (index: number) =>
		candles.length === 1
			? box.left + innerWidth / 2
			: box.left + (index / (candles.length - 1)) * innerWidth;
	const y = (value: number) => box.top + ((high - value) / span) * innerHeight;
	const points = validCloses
		.map((value, index) => `${x(index).toFixed(2)},${y(value).toFixed(2)}`)
		.join(" ");
	return { high, low, x, y, points };
}

function CandleSvg({ series, compact }: { series: CandleSeries; compact: boolean }) {
	const box = compact ? MINI : FULL;
	const geometry = chartPoints(series.candles, box);
	if (!geometry) return null;
	const id = chartId(series, compact);
	const closes = series.candles.map((candle) => numeric(candle.close) ?? 0);
	const latest = closes.at(-1) ?? 0;
	const latestX = geometry.x(series.candles.length - 1);
	const latestY = geometry.y(latest);
	const gridLines = compact ? [] : [0.25, 0.5, 0.75];
	const daily = series.timeframe === "1d";

	return (
		<div className={compact ? styles.miniChart : styles.chart}>
			<svg
				viewBox={`0 0 ${box.width} ${box.height}`}
				role={compact ? undefined : "img"}
				aria-hidden={compact ? true : undefined}
				aria-labelledby={compact ? undefined : `${id}-title`}
				aria-describedby={compact ? undefined : `${id}-desc`}
				className={styles.svg}
			>
				<title id={`${id}-title`}>
					{`${series.chain} ${series.timeframe} closed candles, ${series.candles.length} bars`}
				</title>
				<desc id={`${id}-desc`}>
					{`Close line from ${formatUsdPrice(series.candles[0]?.close)} to ${formatUsdPrice(
						lastClose(series),
					)}. Source ${sourceLabel(series.source.name)}. A table of the newest bars follows the chart.`}
				</desc>
				{gridLines.map((fraction) => {
					const value = geometry.high - (geometry.high - geometry.low) * fraction;
					return (
						<line
							key={fraction}
							className={styles.grid}
							x1={box.left}
							y1={geometry.y(value)}
							x2={box.width - box.right}
							y2={geometry.y(value)}
						/>
					);
				})}
				<polyline className={styles.series} points={geometry.points} />
				<circle className={styles.dot} cx={latestX} cy={latestY} r={compact ? 2 : 3} />
				{!compact && (
					<>
						<text className={styles.axisText} x={box.left} y={box.top - 2}>
							{formatUsdPrice(String(geometry.high))}
						</text>
						<text
							className={styles.axisText}
							x={box.left}
							y={box.height - box.bottom - 2}
						>
							{formatUsdPrice(String(geometry.low))}
						</text>
						<text
							className={styles.lastLabel}
							x={latestX + 6 > box.width - box.right ? latestX - 6 : latestX + 6}
							y={Math.max(box.top + 8, latestY - 6)}
							textAnchor={
								latestX + 6 > box.width - box.right ? "end" : "start"
							}
						>
							{formatUsdPrice(lastClose(series))}
						</text>
						<text className={styles.axisText} x={box.left} y={box.height - 6}>
							{barTime(series.candles[0].open_time, daily)}
						</text>
						<text
							className={styles.axisText}
							x={box.width - box.right}
							y={box.height - 6}
							textAnchor="end"
						>
							{barTime(series.candles[series.candles.length - 1].close_time, daily)}
						</text>
					</>
				)}
			</svg>
		</div>
	);
}

function CandleTable({ series }: { series: CandleSeries }) {
	const rows = [...series.candles].reverse().slice(0, 30);
	return (
		<details className={styles.tableDetails}>
			<summary>
				{rows.length === series.candles.length
					? `View all ${rows.length} closed bars as a table`
					: `View the newest ${rows.length} of ${series.candles.length} closed bars as a table`}
			</summary>
			<div className="table-wrap">
				<table className="table compact">
					<thead>
						<tr>
							<th scope="col">Open time</th>
							<th scope="col" className="num">
								Open
							</th>
							<th scope="col" className="num">
								High
							</th>
							<th scope="col" className="num">
								Low
							</th>
							<th scope="col" className="num">
								Close
							</th>
							<th scope="col" className="num">
								Volume
							</th>
						</tr>
					</thead>
					<tbody>
						{rows.map((candle) => (
							<tr key={candle.open_time}>
								<td className="mono small">{barTime(candle.open_time, series.timeframe === "1d")}</td>
								<td className="num mono">{formatPrice(candle.open)}</td>
								<td className="num mono">{formatPrice(candle.high)}</td>
								<td className="num mono">{formatPrice(candle.low)}</td>
								<td className="num mono">{formatPrice(candle.close)}</td>
								<td className="num mono">
									{candle.volume === null ? DASH : formatAmount(candle.volume)}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>
			<p className={styles.tableNote}>
				Newest first. Only closed bars are shown; volume {DASH} means the provider did not
				report it, not zero.
			</p>
		</details>
	);
}

function CandleMeta({ series }: { series: CandleSeries }) {
	return (
		<div className={styles.meta}>
			<span>
				{series.candles.length} closed <span className="mono">{series.timeframe}</span> bars
			</span>
			<span>
				last close <span className="mono">{formatUsdPrice(lastClose(series))}</span>
			</span>
			<span>
				{sourceLabel(series.source.name)} · {ageLabel(series.source.observed_at)} old
			</span>
			<Badge tone={series.stale ? "warn" : "ghost"}>
				{series.stale ? "STALE SERIES" : "fresh"}
			</Badge>
			{series.pool_address && (
				<span title={series.pool_address} className="mono">
					pool {series.pool_address.slice(0, 6)}…{series.pool_address.slice(-4)}
				</span>
			)}
		</div>
	);
}

export function CandleView({
	load,
	retryHref,
}: {
	load: CandleLoad;
	retryHref?: string;
}) {
	if (!load.ok) {
		const copy = candleErrorCopy(load.error);
		return (
			<div className={styles.empty} role="status">
				<div className="row">
					<Badge tone="err">{load.error.code}</Badge>
					{load.error.retryable && <Badge tone="warn">retryable</Badge>}
				</div>
				<div className={styles.emptyTitle}>{copy.title}</div>
				<p className={styles.emptyBody}>{copy.body}</p>
				<p className={styles.emptyBody}>
					API said: <span className="mono">{load.error.message}</span>
				</p>
				{retryHref && (
					<Link className="btn sm" href={retryHref}>
						Retry
					</Link>
				)}
			</div>
		);
	}

	const { series } = load;
	if (series.candles.length === 0) {
		const copy = candleReasonCopy(series.reason);
		return (
			<div className={styles.empty} role="status">
				<div className="row">
					<Badge tone="warn">{series.reason ?? "CANDLES_EMPTY"}</Badge>
					<Badge tone={series.stale ? "warn" : "ghost"}>
						{series.stale ? "stale" : "not stale"}
					</Badge>
				</div>
				<div className={styles.emptyTitle}>{copy?.title ?? "No closed bars"}</div>
				<p className={styles.emptyBody}>
					{copy?.body ??
						"The API returned no bars for this token and timeframe. No chart is invented."}
				</p>
				<p className={styles.emptyBody}>
					{sourceLabel(series.source.name)} · {ageLabel(series.source.observed_at)} old ·
					requested <span className="mono">{series.timeframe}</span> bars
				</p>
			</div>
		);
	}

	return (
		<div className={styles.wrap}>
			<div className={styles.chartScroll}>
				<CandleSvg series={series} compact={false} />
			</div>
			<CandleMeta series={series} />
			<CandleTable series={series} />
		</div>
	);
}

/** Inline sparkline for Detailed mode; a reason is printed when no series exists. */
export function CandleSparkline({ load, label }: { load: CandleLoad; label: string }) {
	if (!load.ok) {
		return (
			<span className={styles.miniEmpty} title={load.error.message}>
				{DASH} <Badge tone="warn">{load.error.code}</Badge>
			</span>
		);
	}
	if (load.series.candles.length === 0) {
		return (
			<span
				className={styles.miniEmpty}
				title={candleReasonCopy(load.series.reason)?.body ?? "no closed bars"}
			>
				{DASH} <Badge tone="warn">{load.series.reason ?? "CANDLES_EMPTY"}</Badge>
			</span>
		);
	}
	return (
		<span
			className={styles.mini}
			role="img"
			aria-label={`${label} ${load.series.timeframe} close sparkline, ${load.series.candles.length} closed bars, latest ${formatUsdPrice(lastClose(load.series))}, ${sourceLabel(load.series.source.name)}`}
		>
			<CandleSvg series={load.series} compact />
			<span className="small muted">{load.series.timeframe}</span>
		</span>
	);
}
