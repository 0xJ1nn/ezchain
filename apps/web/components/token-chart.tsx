import type { TokenChartSpec } from "@/lib/token-fixtures";

// Token-level level chart (900x300) from the token fixture. Static SVG like the
// position tile chart and the equity chart; data comes from the view model.
export function TokenChart({ chart }: { chart: TokenChartSpec }) {
	return (
		<div className="chart">
			<svg viewBox="0 0 900 300" role="img" aria-labelledby="token-chart-title token-chart-desc">
				<title id="token-chart-title">{chart.ariaTitle}</title>
				<desc id="token-chart-desc">{chart.ariaDesc}</desc>
				{chart.gridY.map((y) => (
					<line className="gridline" x1="0" y1={y} x2="840" y2={y} key={y} />
				))}
				<line className="axis" x1="840" y1="10" x2="840" y2="280" />
				{chart.axisLabels.map((a) => (
					<text className="axis-text" x={a.x} y={a.y} key={`${a.text}${a.x}`}>
						{a.text}
					</text>
				))}
				{chart.levels.map((level) => (
					<g key={level.label}>
						<line className={level.className} x1="0" y1={level.y} x2="840" y2={level.y} />
						<text className={level.labelClass ?? "level-label"} x="6" y={level.y - 4}>
							{level.label}
						</text>
					</g>
				))}
				{chart.points && <polyline className="series" points={chart.points} />}
				{chart.peak && (
					<>
						<circle className="dot" cx={chart.peak.cx} cy={chart.peak.cy} r={chart.peak.r ?? 3} />
						<text className="num-label" x={chart.peak.labelX} y={chart.peak.labelY}>
							{chart.peak.label}
						</text>
					</>
				)}
				{chart.entry && (
					<>
						<circle className="dot" cx={chart.entry.cx} cy={chart.entry.cy} r={chart.entry.r ?? 2.5} />
						<text className="level-label" x={chart.entry.labelX} y={chart.entry.labelY}>
							{chart.entry.label}
						</text>
					</>
				)}
				{chart.crosshair !== undefined && (
					<line className="crosshair" x1={chart.crosshair} y1="10" x2={chart.crosshair} y2="280" />
				)}
				<circle className="dot" cx={chart.last.cx} cy={chart.last.cy} r={chart.last.r ?? 3.5} />
				<text className="num-label" x={chart.last.labelX} y={chart.last.labelY}>
					{chart.last.label}
				</text>
				{chart.foot.map((f) => (
					<text className="axis-text" x={f.x} y={f.y} key={`${f.text}${f.x}`}>
						{f.text}
					</text>
				))}
			</svg>
		</div>
	);
}
