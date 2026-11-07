import { useId } from "react";
import type { Candle } from "@/lib/api";
import { formatPrice } from "@/lib/live-format";

// Closed OHLC candles with a volume pane, drawn from the server series
// (GET /api/v1/tokens/{chain}/{address}/candles). The component only scales
// values to pixels; it never derives a price, PnL or quote. A bar with unknown
// volume draws no volume bar — unknown is not zero. Colors come from the
// existing chart token system (ui.css .candle-* classes); no new colors.

const WIDTH = 900;
const HEIGHT = 300;
const PLOT_X2 = 840;
const PRICE_TOP = 10;
const PRICE_BOTTOM = 224;
const VOL_TOP = 236;
const VOL_BOTTOM = 286;

function num(value: string): number | null {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function footTime(iso: string): string {
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return "";
	return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
}

export function CandleChart({
	candles,
	symbol,
	timeframe,
}: {
	candles: Candle[];
	symbol: string;
	timeframe: string;
}) {
	const id = useId().replace(/[^a-zA-Z0-9]/g, "");
	const titleId = `candle-title-${id}`;
	const descId = `candle-desc-${id}`;

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
			(row): row is { key: string; open: number; high: number; low: number; close: number; volume: number | null; openTime: string } =>
				row.open !== null && row.high !== null && row.low !== null && row.close !== null,
		);
	if (rows.length === 0) return null;

	let low = Number.POSITIVE_INFINITY;
	let high = Number.NEGATIVE_INFINITY;
	let maxVolume = 0;
	for (const row of rows) {
		low = Math.min(low, row.low);
		high = Math.max(high, row.high);
		if (row.volume !== null) maxVolume = Math.max(maxVolume, row.volume);
	}
	if (!Number.isFinite(low) || !Number.isFinite(high) || high === low) {
		high = low + 1;
	}
	const span = high - low;
	const step = PLOT_X2 / rows.length;
	const y = (price: number) => PRICE_TOP + ((high - price) / span) * (PRICE_BOTTOM - PRICE_TOP);
	const grid = [0, 0.5, 1].map((fraction) => {
		const price = low + span * fraction;
		return { price, y: y(price) };
	});
	const last = rows[rows.length - 1] as (typeof rows)[number];
	const first = rows[0] as (typeof rows)[number];
	const mid = rows[Math.floor(rows.length / 2)] as (typeof rows)[number];

	return (
		<div className="chart">
			<svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-labelledby={`${titleId} ${descId}`}>
				<title id={titleId}>{`OHLC candles, ${symbol}, ${timeframe}`}</title>
				<desc id={descId}>
					{rows.length} closed {timeframe} candles. First {footTime(first.openTime)} UTC, last
					close {formatPrice(String(last.close))} at {footTime(last.openTime)} UTC. Volume pane
					below the price pane.
				</desc>
				{grid.map((line, index) => (
					<g key={`g${index}`}>
						<line className="gridline" x1="0" y1={line.y} x2={PLOT_X2} y2={line.y} />
						<text className="axis-text" x={PLOT_X2 + 6} y={line.y + 3}>
							{formatPrice(String(line.price))}
						</text>
					</g>
				))}
				<line className="axis" x1={PLOT_X2} y1={PRICE_TOP} x2={PLOT_X2} y2={PRICE_BOTTOM} />
				<line className="axis" x1="0" y1={VOL_BOTTOM} x2={PLOT_X2} y2={VOL_BOTTOM} />
				{rows.map((row, index) => {
					const center = index * step + step / 2;
					const up = row.close >= row.open;
					const bodyWidth = Math.max(1, step * 0.6);
					const top = y(Math.max(row.open, row.close));
					const bottom = y(Math.min(row.open, row.close));
					const volumeHeight =
						row.volume !== null && maxVolume > 0
							? Math.max(1, (row.volume / maxVolume) * (VOL_BOTTOM - VOL_TOP))
							: null;
					return (
						<g key={row.key}>
							<line
								className={up ? "candle-wick up" : "candle-wick down"}
								x1={center}
								y1={y(row.high)}
								x2={center}
								y2={y(row.low)}
							/>
							<rect
								className={up ? "candle-up" : "candle-down"}
								x={center - bodyWidth / 2}
								y={top}
								width={bodyWidth}
								height={Math.max(1, bottom - top)}
							/>
							{volumeHeight !== null && (
								<rect
									className={up ? "vol vol-up" : "vol vol-down"}
									x={center - bodyWidth / 2}
									y={VOL_BOTTOM - volumeHeight}
									width={bodyWidth}
									height={volumeHeight}
								/>
							)}
						</g>
					);
				})}
				<circle className="dot" cx={PLOT_X2} cy={y(last.close)} r="3.5" />
				<text className="axis-text" x="0" y={HEIGHT - 4}>
					{footTime(first.openTime)}
				</text>
				<text className="axis-text" x={PLOT_X2 / 2} y={HEIGHT - 4}>
					{footTime(mid.openTime)}
				</text>
				<text className="axis-text" x={PLOT_X2 - 40} y={HEIGHT - 4}>
					{footTime(last.openTime)}
				</text>
			</svg>
		</div>
	);
}

/** The explicit empty state: no series is plotted, the API reason is printed. */
export function CandleEmptyState({ reason, detail }: { reason: string; detail?: string }) {
	return (
		<div className="note" role="status" aria-label="No candle series">
			<strong>No candle series</strong>
			<div className="small mt-8">
				<span className="mono">{reason}</span> — the server returned no closed bars for this
				token, so nothing is plotted.{" "}
				{detail ?? "An empty series is never replaced with a fabricated chart."}
			</div>
		</div>
	);
}
