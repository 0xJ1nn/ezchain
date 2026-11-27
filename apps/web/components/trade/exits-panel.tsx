"use client";

import { Badge, Panel } from "@/components/ui";
import { formatUsdPrice } from "@/lib/live-format";

function dec(value: number): string {
	if (!Number.isFinite(value)) return "0";
	const text = value.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
	return text === "" || text === "-0" ? "0" : text;
}

function pct(value: number, entry: number): string {
	return `${(((value / entry) - 1) * 100).toFixed(1)}%`;
}

export interface ExitsNotice {
	tone: "ok" | "err";
	text: string;
}

/**
 * The exits panel and the chart are one state: every field below is the same value the
 * draggable line shows. Saving goes through the protection client, never through a local
 * fill, and the copy says paper throughout.
 */
export function ExitsPanel({
	entry,
	stop,
	targets,
	sizes,
	trail,
	trailStart,
	trailDistance,
	rewardRisk,
	notice,
	busy,
	onStop,
	onTarget,
	onSize,
	onBreakeven,
	onTrail,
	onTrailStart,
	onTrailDistance,
	onSave,
}: {
	entry: number;
	stop: number;
	targets: number[];
	sizes: string[];
	trail: number;
	trailStart: number;
	trailDistance: string;
	rewardRisk: string;
	notice: ExitsNotice | null;
	busy: boolean;
	onStop: (value: number) => void;
	onTarget: (index: number, value: number) => void;
	onSize: (index: number, value: string) => void;
	onBreakeven: () => void;
	onTrail: (value: number) => void;
	onTrailStart: (value: number) => void;
	onTrailDistance: (value: string) => void;
	onSave: () => void;
}) {
	return (
		<Panel
			title="Exits"
			sub={`Stop, targets and trail · entry ${formatUsdPrice(dec(entry))}`}
			action={<Badge tone="ghost">PAPER</Badge>}
			footer="Exits are stored through the paper protection client. An unknown value stays a dash, never zero."
		>
			<div className="stack-sm" data-testid="exits-panel">
				<div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
					<span className="micro">Stop loss</span>
					<input
						className="input mono"
						data-testid="exit-stop-input"
						aria-label="Stop loss price"
						value={dec(stop)}
						onChange={(event) => {
							const next = Number(event.target.value);
							if (Number.isFinite(next)) onStop(next);
						}}
					/>
					<span className="small muted">{pct(stop, entry)}</span>
					<button
						className="btn sm"
						type="button"
						data-testid="exit-breakeven"
						onClick={onBreakeven}
					>
						Move to breakeven
					</button>
				</div>

				<div className="stack-sm">
					<span className="micro">Three-target ladder · partial sizes</span>
					{targets.map((target, index) => (
						<div className="row" key={index} data-testid={`exit-target-row-${index}`} style={{ gap: 8, flexWrap: "wrap" }}>
							<span className="small mono">TP{index + 1}</span>
							<input
								className="input mono"
								data-testid={`exit-tp-${index}-input`}
								aria-label={`Take profit ${index + 1} price`}
								value={dec(target)}
								onChange={(event) => {
									const next = Number(event.target.value);
									if (Number.isFinite(next)) onTarget(index, next);
								}}
							/>
							<input
								className="input mono"
								data-testid={`exit-tp-${index}-size`}
								aria-label={`Take profit ${index + 1} size percent`}
								value={sizes[index]}
								onChange={(event) => onSize(index, event.target.value)}
							/>
							<span className="small muted">% of position · {pct(target, entry)}</span>
						</div>
					))}
				</div>

				<div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
					<span className="micro">Trailing · current stop</span>
					<input
						className="input mono"
						data-testid="exit-trail-price"
						aria-label="Trailing stop price"
						value={dec(trail)}
						onChange={(event) => {
							const next = Number(event.target.value);
							if (Number.isFinite(next)) onTrail(next);
						}}
					/>
					<span className="micro">start</span>
					<input
						className="input mono"
						data-testid="exit-trail-start"
						aria-label="Trailing start price"
						value={dec(trailStart)}
						onChange={(event) => {
							const next = Number(event.target.value);
							if (Number.isFinite(next)) onTrailStart(next);
						}}
					/>
					<span className="micro">distance %</span>
					<input
						className="input mono"
						data-testid="exit-trail-distance"
						aria-label="Trailing distance percent"
						value={trailDistance}
						onChange={(event) => onTrailDistance(event.target.value)}
					/>
				</div>

				<div className="row" data-testid="reward-risk">
					<span className="micro">Reward to risk</span>
					<span className="strong">{rewardRisk}</span>
				</div>

				<div className="row">
					<button
						className="btn primary"
						type="button"
						data-testid="save-exits"
						disabled={busy}
						onClick={onSave}
					>
						{busy ? "Saving…" : "Save exits"}
					</button>
					<Badge tone="ghost">PAPER</Badge>
				</div>

				{notice && (
					<div
						className={`note ${notice.tone === "ok" ? "ok" : "err"}`}
						data-testid="exits-status"
						role="status"
					>
						{notice.text}
					</div>
				)}
			</div>
		</Panel>
	);
}
