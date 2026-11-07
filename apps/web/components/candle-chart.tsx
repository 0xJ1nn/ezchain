import type { CandleSeries } from "@/lib/api";

export function CandleChart({ series }: { series: CandleSeries }) {
	const candles = series.candles;
	if (candles.length === 0) return null;

	const width = 900;
	const height = 300;
	const plotLeft = 8;
	const plotRight = 820;
	const plotTop = 16;
	const plotBottom = 248;
	const volumeTop = 256;
	const volumeBottom = 292;

	const prices = candles.flatMap((candle) => [
		Number(candle.open),
		Number(candle.high),
		Number(candle.low),
		Number(candle.close),
	]);
	const minPrice = Math.min(...prices);
	const maxPrice = Math.max(...prices);
	const priceRange = maxPrice - minPrice || 1;

	const volumes = candles
		.map((candle) => (candle.volume === null ? null : Number(candle.volume)))
		.filter((value): value is number => value !== null);
	const maxVolume = volumes.length > 0 ? Math.max(...volumes) : 1;

	const xStep = (plotRight - plotLeft) / candles.length;
	const candleWidth = Math.max(2, xStep * 0.6);

	function priceY(value: number): number {
		return plotBottom - ((value - minPrice) / priceRange) * (plotBottom - plotTop);
	}

	function volumeY(value: number): number {
		return volumeBottom - (value / maxVolume) * (volumeBottom - volumeTop);
	}

	const first = candles[0];
	const last = candles[candles.length - 1];

	return (
		<div data-testid="candle-chart" className="chart">
			<svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Candle chart">
				<title>Candle chart</title>
				<desc>{candles.length} closed bars</desc>
				<text className="axis-text" x={plotRight + 6} y={plotTop + 4}>
					candles
				</text>
				<line className="gridline" x1={plotLeft} y1={plotTop} x2={plotRight} y2={plotTop} />
				<line className="gridline" x1={plotLeft} y1={(plotTop + plotBottom) / 2} x2={plotRight} y2={(plotTop + plotBottom) / 2} />
				<line className="gridline" x1={plotLeft} y1={plotBottom} x2={plotRight} y2={plotBottom} />
				<line className="axis" x1={plotRight} y1={plotTop} x2={plotRight} y2={plotBottom} />
				<text className="axis-text" x={plotRight + 6} y={plotTop + 4}>
					{maxPrice.toFixed(4)}
				</text>
				<text className="axis-text" x={plotRight + 6} y={(plotTop + plotBottom) / 2 + 4}>
					{((maxPrice + minPrice) / 2).toFixed(4)}
				</text>
				<text className="axis-text" x={plotRight + 6} y={plotBottom + 4}>
					{minPrice.toFixed(4)}
				</text>
				<text className="axis-text" x={plotLeft} y={height - 4}>
					{first.open_time.slice(11, 16)}
				</text>
				<text className="axis-text" x={plotRight - 40} y={height - 4}>
					{last.open_time.slice(11, 16)}
				</text>
				{candles.map((candle, index) => {
					const xCenter = plotLeft + xStep * index + xStep / 2;
					const openY = priceY(Number(candle.open));
					const closeY = priceY(Number(candle.close));
					const highY = priceY(Number(candle.high));
					const lowY = priceY(Number(candle.low));
					const bodyTop = Math.min(openY, closeY);
					const bodyHeight = Math.max(1, Math.abs(openY - closeY));
					const rising = Number(candle.close) >= Number(candle.open);
					const volume = candle.volume === null ? null : Number(candle.volume);
					return (
						<g key={`${candle.open_time}-${index}`}>
							<line
								data-candle=""
								x1={xCenter}
								y1={highY}
								x2={xCenter}
								y2={lowY}
								stroke="var(--text)"
								strokeWidth={1}
							/>
							<rect
								data-candle=""
								x={xCenter - candleWidth / 2}
								y={bodyTop}
								width={candleWidth}
								height={bodyHeight}
								fill={rising ? "var(--text)" : "var(--err, #99302c)"}
							/>
							{volume !== null ? (
								<rect
									data-volume=""
									x={xCenter - candleWidth / 2}
									y={volumeY(volume)}
									width={candleWidth}
									height={Math.max(1, volumeBottom - volumeY(volume))}
									fill="var(--muted, #6f6f68)"
									opacity={0.5}
								/>
							) : null}
						</g>
					);
				})}
			</svg>
		</div>
	);
}