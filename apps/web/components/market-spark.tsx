// Markets-only 7d sparkline. The wireframe draws a 72×18 polyline; this component keeps
// that geometry and adds the text alternative required for charts. It renders fixture
// values only — no price, PnL or quote math happens here.

const WIDTH = 72;
const HEIGHT = 18;

export function MarketSpark({
	values,
	tone = "ink",
	label,
}: {
	values: number[];
	tone?: "ink" | "err";
	label: string;
}) {
	if (values.length < 2) {
		return (
			<span className="muted" role="img" aria-label={label}>
				—
			</span>
		);
	}

	const min = Math.min(...values);
	const max = Math.max(...values);
	const span = max - min || 1;
	const points = values
		.map((value, index) => {
			const x = (index / (values.length - 1)) * WIDTH;
			const y = HEIGHT - ((value - min) / span) * (HEIGHT - 2) - 1;
			return `${x},${y}`;
		})
		.join(" ");

	return (
		<span
			style={{
				display: "inline-block",
				color: tone === "err" ? "var(--err)" : "var(--ink)",
			}}
		>
			<svg
				width={WIDTH}
				height={HEIGHT}
				viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
				role="img"
				aria-label={label}
			>
				<polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.2" />
			</svg>
		</span>
	);
}
