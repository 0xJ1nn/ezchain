"use client";

import Link from "next/link";
import { Fragment, useState } from "react";
import { Badge } from "@/components/ui";
import { ageLabel } from "@/lib/live-format";
import {
	type PaperApiMode,
	getStrategyRuns,
	patchStrategy,
	runStrategy,
} from "@/lib/paper-api";
import type { Strategy, StrategyRun, StrategyStatus } from "@/lib/strategy-fixtures";
import { strategyKindMeta } from "@/lib/strategy-fixtures";

function intervalLabel(params: Record<string, string | number>) {
	const raw = params.interval_minutes;
	const minutes = typeof raw === "number" ? raw : Number(raw);
	if (!Number.isFinite(minutes) || minutes <= 0) return "—";
	if (minutes >= 1440 && minutes % 1440 === 0) return `${minutes / 1440}d`;
	if (minutes >= 60 && minutes % 60 === 0) return `${minutes / 60}h`;
	return `${minutes}m`;
}

function stamp(iso: string | null) {
	if (!iso) return "never";
	return `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z`;
}

// The scheduler table of the one strategy surface. Pause, resume and a single manual
// cycle are the mutations the API actually exposes; the run log is the server's recorded
// decisions, not a browser timer. Fixture mode mutates local rows only.
export function AutomationsTable({
	mode,
	initialStrategies,
	initialRuns,
}: {
	mode: PaperApiMode;
	initialStrategies: Strategy[];
	initialRuns: StrategyRun[];
}) {
	const [rows, setRows] = useState(initialStrategies);
	const [runs, setRuns] = useState(initialRuns);
	const [busy, setBusy] = useState<string | null>(null);
	const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
	const [openLog, setOpenLog] = useState<string | null>(null);

	const recent = (id: string) =>
		runs
			.filter((run) => run.strategy_id === id)
			.sort((a, b) => b.created_at.localeCompare(a.created_at))
			.slice(0, 3);

	async function toggle(strategy: Strategy) {
		const status: StrategyStatus = strategy.status === "active" ? "paused" : "active";
		setBusy(`status:${strategy.id}`);
		setNotice(null);
		try {
			const updated =
				mode === "fixture" ? { ...strategy, status } : await patchStrategy(strategy.id, { status });
			setRows((current) => current.map((row) => (row.id === strategy.id ? updated : row)));
			setNotice({ tone: "ok", text: `${strategy.name} is now ${status}.` });
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The status change was refused.",
			});
		} finally {
			setBusy(null);
		}
	}

	async function runNow(strategy: Strategy) {
		setBusy(`run:${strategy.id}`);
		setNotice(null);
		try {
			let run: StrategyRun;
			if (mode === "fixture") {
				run = {
					id: `run-${Date.now().toString(36)}`,
					strategy_id: strategy.id,
					action: "evaluated",
					reason: "fixture sample · no order was placed",
					inputs: {},
					order_id: null,
					created_at: new Date().toISOString(),
				};
			} else {
				run = await runStrategy(strategy.id);
			}
			setRuns((current) => [run, ...current]);
			setRows((current) =>
				current.map((row) => (row.id === strategy.id ? { ...row, last_run_at: run.created_at } : row)),
			);
			setOpenLog(strategy.id);
			setNotice({
				tone: run.action === "denied" || run.action === "error" ? "err" : "ok",
				text: `${strategy.name}: ${run.action} · ${run.reason}`,
			});
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The run could not be evaluated.",
			});
		} finally {
			setBusy(null);
		}
	}

	async function toggleLog(strategy: Strategy) {
		if (openLog === strategy.id) {
			setOpenLog(null);
			return;
		}
		setOpenLog(strategy.id);
		if (mode !== "fixture") {
			const result = await getStrategyRuns(strategy.id);
			if (result.ok) setRuns((current) => [...result.data, ...current]);
		}
	}

	return (
		<div data-testid="automations-table">
			{notice && (
				<div className={`notice ${notice.tone} mb-12`} role="status">
					{notice.text}
				</div>
			)}
			<div className="table-wrap" tabIndex={0} role="region" aria-label="Scheduled strategies table">
				<table className="table">
					<caption className="sr-only">Scheduled strategies</caption>
					<thead>
						<tr>
							<th scope="col">Strategy</th>
							<th scope="col">Status</th>
							<th scope="col">Kind</th>
							<th scope="col">Interval</th>
							<th scope="col">Last run</th>
							<th scope="col">Run log</th>
							<th scope="col" />
						</tr>
					</thead>
					<tbody>
						{rows.map((strategy) => (
							<Fragment key={strategy.id}>
								<tr data-strategy={strategy.id}>
									<td>
										<Link href={`/strategies/${strategy.id}`}>
											<span className="strong">{strategy.name}</span>
										</Link>
										<span className="sub" style={{ display: "block" }}>
											{strategy.token_symbol} · {strategy.chain}
										</span>
									</td>
									<td>
										<Badge tone={strategy.status === "active" ? "ok" : "warn"}>
											{strategy.status}
										</Badge>
									</td>
									<td className="small">
										{strategyKindMeta[strategy.kind]?.label ?? strategy.kind}
									</td>
									<td className="mono small">{intervalLabel(strategy.params)}</td>
									<td className="small">
										{stamp(strategy.last_run_at)}
										<span className="sub" style={{ display: "block" }}>
											{strategy.last_run_at ? `${ageLabel(strategy.last_run_at)} ago` : "never"}
										</span>
									</td>
									<td>
										<button
											type="button"
											className="btn sm"
											aria-expanded={openLog === strategy.id}
											onClick={() => void toggleLog(strategy)}
										>
											{recent(strategy.id).length} decision
											{recent(strategy.id).length === 1 ? "" : "s"}
										</button>
									</td>
									<td className="right">
										<div className="row" style={{ justifyContent: "flex-end" }}>
											<button
												type="button"
												className="btn sm"
												disabled={busy !== null}
												onClick={() => void toggle(strategy)}
											>
												{busy === `status:${strategy.id}`
													? "Saving…"
													: strategy.status === "active"
														? "Pause"
														: "Resume"}
											</button>
											<button
												type="button"
												className="btn sm"
												disabled={busy !== null}
												title="Evaluates one cycle now and leaves the persisted status unchanged"
												onClick={() => void runNow(strategy)}
											>
												{busy === `run:${strategy.id}` ? "Running…" : "Run once"}
											</button>
										</div>
									</td>
								</tr>
								{openLog === strategy.id && (
									<tr data-log={strategy.id}>
										<td colSpan={7}>
											{recent(strategy.id).length === 0 ? (
												<div className="small muted">
													No decisions recorded yet. Run one cycle to add a server row.
												</div>
											) : (
												<ul className="list">
													{recent(strategy.id).map((run) => (
														<li className="list-row" key={run.id}>
															<Badge tone={run.action === "denied" ? "err" : "ghost"}>
																{run.action}
															</Badge>
															<span className="grow small">
																{run.reason || "no reason returned"} · {stamp(run.created_at)}
															</span>
														</li>
													))}
												</ul>
											)}
										</td>
									</tr>
								)}
							</Fragment>
						))}
					</tbody>
				</table>
			</div>
		</div>
	);
}
