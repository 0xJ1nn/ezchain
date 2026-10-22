import Link from "next/link";
import { Badge, LiveDot } from "@/components/ui";
import type { PositionTileView, TileChart } from "@/lib/position-fixtures";

function Chart({ chart }: { chart: TileChart }) {
	return (
		<div className="chart">
			<svg viewBox="0 0 616 112" role="img">
				<title>{chart.ariaLabel}</title>
				{chart.gridY.map((y) => (
					<line className="gridline" x1="0" y1={y} x2="560" y2={y} key={`g${y}`} />
				))}
				<line className="axis" x1="560" y1="0" x2="560" y2="112" />
				{chart.axis.map((a) => (
					<text className="axis-text" x={a.x} y={a.y} key={`a${a.text}`}>
						{a.text}
					</text>
				))}
				{chart.levels.map((level) => (
					<g key={`l${level.label}`}>
						<line className={level.className} x1="0" y1={level.y} x2="560" y2={level.y} />
						<text className={level.labelClass ?? "level-label"} x="4" y={level.y - 3}>
							{level.label}
						</text>
					</g>
				))}
				{chart.points && <polyline className="series" points={chart.points} />}
				<circle className="dot" cx={chart.dot.x} cy={chart.dot.y} r="2.5" />
				{chart.peak && (
					<>
						<circle className="peak" cx={chart.peak.x} cy={chart.peak.y} r="5" />
						<text className="num-label" x={chart.peak.x - 68} y={chart.peak.y - 4}>
							{chart.peak.label}
						</text>
					</>
				)}
				{chart.foot.map((f) => (
					<text className="axis-text" x={f.x} y={f.y} key={`f${f.text}`}>
						{f.text}
					</text>
				))}
			</svg>
		</div>
	);
}

export function PositionTile({ position }: { position: PositionTileView }) {
	const { ladder } = position;
	return (
		<article className={`tile ${position.tone}`} id={position.id}>
			<div className="tile-head">
				<div className="tile-id">
					<div className="token" title={position.name}>
						{position.symbol}
					</div>
					<div>
						<div className="tile-name">
							<Link href={`/positions/${position.id}`}>{position.name}</Link>
						</div>
						<div className="tile-sub">{position.sub}</div>
					</div>
				</div>
				<div className="tile-pnl">
					<div className={`pct ${position.pnlTone}`}>{position.pnlPct}</div>
					<div className="usd">
						{position.pnlUsd}
						{position.pnlSol && (
							<>
								{" · "}
								<span className={position.pnlTone}>{position.pnlSol}</span>
							</>
						)}
					</div>
				</div>
			</div>

			<div className="tile-price">
				<span className={`px${position.priceTone === "muted" ? " mut" : ""}`}>
					{position.price}
				</span>
				<span className={`small muted${position.markToneClass ? ` ${position.markToneClass}` : ""}`}>
					<LiveDot tone={position.markTone} />
					{position.mark}
				</span>
				<span className="tile-tags">
					{position.tags.map((tag) => (
						<Badge tone={tag.tone} key={tag.label}>
							{tag.label}
						</Badge>
					))}
				</span>
			</div>

			<div className="tile-chart">
				<div className="chart-scroll">
					<Chart chart={position.chart} />
				</div>
			</div>

			<div className="tile-market" role="group" aria-label="Market data">
				{position.market.map((cell) => (
					<div className="mcell" key={cell.k}>
						<div className="k">{cell.k}</div>
						<div className={`v${cell.vTone ? ` ${cell.vTone}` : ""}`}>{cell.v}</div>
						<div className="src">
							{cell.srcTone && <LiveDot tone={cell.srcTone} />}
							{cell.src}
						</div>
					</div>
				))}
			</div>

			<div className="tile-money">
				{position.money.map((cell) => (
					<div className="money-cell" key={cell.k}>
						<div className="k">{cell.k}</div>
						<div className={`v${cell.tone ? ` ${cell.tone}` : ""}`}>{cell.v}</div>
					</div>
				))}
			</div>

			<div className="tile-ladder">
				{ladder.kind === "steps" ? (
					<>
						<div className="ladder-top">
							<Badge tone={ladder.stateTone}>{ladder.state}</Badge>
							<span className="small muted">
								bag <span className="mono">{ladder.bag}</span> · next
							</span>
							<span className="tag" style={{ marginLeft: "auto" }}>
								{ladder.next}
							</span>
						</div>
						<div className="ladder">
							{ladder.steps.map((step, index) => (
								<div className="step" key={step.label}>
									<span className={`tick ${step.state}`} />
									<span className={`lbl${step.state === "on" ? " on" : ""}`}>
										{step.label}
									</span>
									{index < ladder.steps.length - 1 && <span className="bar" />}
								</div>
							))}
						</div>
					</>
				) : (
					<>
						<div className="ladder-top">
							<Badge tone="err">{ladder.state}</Badge>
							<span className="small muted">
								last executable value <span className="mono">{ladder.lastExecutable}</span>
							</span>
						</div>
						<div className="note err mt-8">
							<strong>Sell is unavailable.</strong> {ladder.note}
						</div>
					</>
				)}
			</div>

			<div className="tile-prov">
				{position.prov.map((item) =>
					item.label === "" ? null : item.className ? (
						<span className={item.className} key={item.label}>
							{item.label}
						</span>
					) : (
						<Badge tone={item.tone} key={item.label}>
							{item.label}
						</Badge>
					),
				)}
			</div>

			<div className="tile-actions">
				{position.budget.kind === "bar" ? (
					<div className="budget">
						<div className="bar">
							<div className="fill" style={{ width: position.budget.width }} />
						</div>
						<div className="cap">{position.budget.cap}</div>
					</div>
				) : (
					<div className="budget">
						<div className="cap">{position.budget.cap}</div>
					</div>
				)}
				{position.actions.map((action) => (
					<button
						className={action.className}
						type="button"
						key={action.label}
						disabled={action.disabled}
						aria-label={action.ariaLabel}
						title={action.disabled ? "Execution is not wired in M1" : undefined}
					>
						{action.label}
					</button>
				))}
			</div>
			<div className="tile-note">{position.note}</div>
		</article>
	);
}
