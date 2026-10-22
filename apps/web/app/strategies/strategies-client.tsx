"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent } from "react";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { signedUsd, usd } from "@/lib/format";
import {
	type PaperApiMode,
	createStrategy,
	deleteStrategy,
	patchStrategy,
	runStrategy,
} from "@/lib/paper-api";
import {
	type ParamField,
	type Strategy,
	type StrategyKind,
	type StrategyParams,
	type StrategyRun,
	type StrategyStatus,
	strategyChains,
	strategyKindMeta,
	strategyParamFields,
} from "@/lib/strategy-fixtures";
import styles from "./strategies.module.css";

interface Props {
	mode: PaperApiMode;
	initialStrategies: Strategy[];
	initialRuns: StrategyRun[];
}

interface FormState {
	name: string;
	chain: string;
	token_address: string;
	token_symbol: string;
	kind: StrategyKind;
	params: Record<string, string>;
}

function emptyForm(): FormState {
	return {
		name: "",
		chain: "solana",
		token_address: "",
		token_symbol: "",
		kind: "momentum",
		params: Object.fromEntries(
			strategyParamFields.map((field) => [field.name, field.defaultValue]),
		),
	};
}

function fieldsFor(kind: StrategyKind): ParamField[] {
	return strategyParamFields.filter((field) => field.kinds.includes(kind));
}

function paramNumber(strategy: Strategy, key: string) {
	const value = strategy.params[key];
	const amount = typeof value === "number" ? value : Number(value);
	return Number.isFinite(amount) ? amount : null;
}

function price(value: number | null) {
	if (value === null) return "—";
	if (value !== 0 && Math.abs(value) < 0.01) return `$${value.toPrecision(4)}`;
	return usd(value);
}

function pnl(value: string | null | undefined) {
	if (value === null || value === undefined) return "—";
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	return amount === 0 ? usd(0) : signedUsd(amount);
}

function paramSummary(strategy: Strategy) {
	const order = paramNumber(strategy, "order_usd");
	const max = paramNumber(strategy, "max_position_usd");
	const interval = paramNumber(strategy, "interval_minutes");

	switch (strategy.kind) {
		case "momentum":
		case "dip": {
			const tp = paramNumber(strategy, "take_profit_pct");
			const sl = paramNumber(strategy, "stop_loss_pct");
			const lookback = strategy.params.lookback ?? "—";
			return `order ${price(order)} · TP ${tp ?? "—"}% · SL ${sl ?? "—"}% · lookback ${lookback} · every ${interval ?? "—"}m · max ${price(max)}`;
		}
		case "dca":
			return `order ${price(order)} · every ${interval ?? "—"}m · max ${price(max)}`;
		case "threshold":
			return `order ${price(order)} · buy ≤ ${price(paramNumber(strategy, "buy_below_usd"))} · sell ≥ ${price(paramNumber(strategy, "sell_above_usd"))} · every ${interval ?? "—"}m · max ${price(max)}`;
		default:
			return "—";
	}
}

function duration(minutes: number) {
	if (minutes >= 1440 && minutes % 1440 === 0) return `${minutes / 1440}d`;
	if (minutes >= 60 && minutes % 60 === 0) return `${minutes / 60}h`;
	return `${minutes}m`;
}

function stamp(iso: string | null) {
	if (!iso) return "never";
	const date = iso.slice(0, 10);
	const time = iso.slice(11, 19);
	return `${date} ${time} UTC`;
}

function actionTone(action: string): "ok" | "err" | "warn" | "info" | "ghost" {
	switch (action) {
		case "buy":
		case "enter":
			return "ok";
		case "exit":
			return "info";
		case "skip":
			return "warn";
		case "error":
		case "denied":
			return "err";
		default:
			return "ghost";
	}
}

// Fixture mode evaluates a deterministic sample so the create -> run -> pause flow is
// testable offline. This is a fixture of an engine decision, not strategy code: the
// browser never drives live orders.
function fixtureDecision(strategy: Strategy): {
	action: string;
	reason: string;
	inputs: Record<string, unknown>;
	order_id: string | null;
} {
	switch (strategy.kind) {
		case "momentum":
			return {
				action: "enter",
				reason: `momentum:lookback +6.4% ≥ entry ${paramNumber(strategy, "entry_change_pct") ?? "—"}% · paper order accepted`,
				inputs: { lookback: strategy.params.lookback, change_pct: 6.4, order_usd: strategy.params.order_usd },
				order_id: `paper-${Date.now().toString(36)}`,
			};
		case "dip":
			return {
				action: "enter",
				reason: `dip:lookback −7.1% ≤ −${paramNumber(strategy, "entry_change_pct") ?? "—"}% · paper order accepted`,
				inputs: { lookback: strategy.params.lookback, change_pct: -7.1, order_usd: strategy.params.order_usd },
				order_id: `paper-${Date.now().toString(36)}`,
			};
		case "dca":
			return {
				action: "buy",
				reason: `dca:interval elapsed · ${price(paramNumber(strategy, "order_usd"))} paper buy`,
				inputs: { interval_minutes: strategy.params.interval_minutes, order_usd: strategy.params.order_usd },
				order_id: `paper-${Date.now().toString(36)}`,
			};
		case "threshold": {
			const buyBelow = paramNumber(strategy, "buy_below_usd");
			const mark = 0.000018;
			if (buyBelow !== null && mark <= buyBelow) {
				return {
					action: "buy",
					reason: `threshold:mark $0.00001800 ≤ buy ${price(buyBelow)} · paper order accepted`,
					inputs: { mark_usd: mark, buy_below_usd: buyBelow },
					order_id: `paper-${Date.now().toString(36)}`,
				};
			}
			return {
				action: "hold",
				reason: `threshold:mark $0.00001800 is not inside the configured band`,
				inputs: { mark_usd: mark, buy_below_usd: buyBelow },
				order_id: null,
			};
		}
	}
}

export function StrategiesClient({ mode, initialStrategies, initialRuns }: Props) {
	const [strategies, setStrategies] = useState<Strategy[]>(initialStrategies);
	const [runs, setRuns] = useState<StrategyRun[]>(
		[...initialRuns].sort((a, b) => b.created_at.localeCompare(a.created_at)),
	);
	const [form, setForm] = useState<FormState>(emptyForm);
	const [errors, setErrors] = useState<Record<string, string>>({});
	const [busy, setBusy] = useState<string | null>(null);
	const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
	const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

	const fields = useMemo(() => fieldsFor(form.kind), [form.kind]);
	const latest = useMemo(() => {
		const map = new Map<string, StrategyRun>();
		for (const run of runs) {
			if (!map.has(run.strategy_id)) map.set(run.strategy_id, run);
		}
		return map;
	}, [runs]);

	const activeCount = strategies.filter((strategy) => strategy.status === "active").length;
	const pausedCount = strategies.length - activeCount;

	function updateField(key: keyof FormState, value: string) {
		setForm((current) => ({ ...current, [key]: value }));
	}

	function updateParam(key: string, value: string) {
		setForm((current) => ({
			...current,
			params: { ...current.params, [key]: value },
		}));
	}

	function validate(): { params?: StrategyParams; errors: Record<string, string> } {
		const next: Record<string, string> = {};
		if (!form.name.trim()) next.name = "Name is required.";
		if (!form.token_address.trim() || form.token_address.trim().length < 8) {
			next.token_address = "Enter the full token address (at least 8 characters).";
		}
		if (!form.token_symbol.trim()) next.token_symbol = "Symbol is required.";

		const params: StrategyParams = {};
		for (const field of fields) {
			const raw = (form.params[field.name] ?? "").trim();
			if (!raw) {
				next[field.name] = `${field.label} is required.`;
				continue;
			}
			if (field.type === "text") {
				params[field.name] = raw;
				continue;
			}
			const amount = Number(raw);
			if (!Number.isFinite(amount)) {
				next[field.name] = "Enter a number.";
				continue;
			}
			if (field.min !== undefined && amount < Number(field.min)) {
				next[field.name] = `Minimum is ${field.min}.`;
				continue;
			}
			if ((field.name === "order_usd" || field.name === "max_position_usd") && amount <= 0) {
				next[field.name] = "Must be greater than zero.";
				continue;
			}
			params[field.name] = amount;
		}

		const order = Number(params.order_usd);
		const max = Number(params.max_position_usd);
		if (Number.isFinite(order) && Number.isFinite(max) && max < order) {
			next.max_position_usd = "Max position must be at least the order size.";
		}
		return { params, errors: next };
	}

	async function submit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const checked = validate();
		setErrors(checked.errors);
		if (!checked.params || Object.keys(checked.errors).length > 0) return;

		setBusy("create");
		setNotice(null);
		const input = {
			name: form.name.trim(),
			chain: form.chain,
			token_address: form.token_address.trim(),
			token_symbol: form.token_symbol.trim().toUpperCase(),
			kind: form.kind,
			params: checked.params,
			status: "active" as const,
		};

		try {
			let created: Strategy;
			if (mode === "fixture") {
				created = {
					...input,
					id: `str-${Date.now().toString(36)}`,
					last_run_at: null,
					created_at: new Date().toISOString(),
					pnl_usd: "0.00",
				};
			} else {
				created = await createStrategy(input);
			}
			setStrategies((current) => [created, ...current]);
			setForm(emptyForm());
			setNotice({
				tone: "ok",
				text:
					mode === "fixture"
						? `Created “${created.name}” in the fixture session. Run it to see a decision.`
						: `Created “${created.name}”.`,
			});
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The strategy could not be created.",
			});
		} finally {
			setBusy(null);
		}
	}

	async function toggleStatus(strategy: Strategy) {
		const status: StrategyStatus = strategy.status === "active" ? "paused" : "active";
		setBusy(`status:${strategy.id}`);
		setNotice(null);
		try {
			const updated =
				mode === "fixture"
					? { ...strategy, status }
					: await patchStrategy(strategy.id, { status });
			setStrategies((current) =>
				current.map((row) => (row.id === strategy.id ? updated : row)),
			);
			setNotice({
				tone: "ok",
				text: `${strategy.name} is now ${status}.`,
			});
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "Status could not be changed.",
			});
		} finally {
			setBusy(null);
		}
	}

	async function runNow(strategy: Strategy) {
		if (strategy.status === "paused") return;
		setBusy(`run:${strategy.id}`);
		setNotice(null);
		try {
			let run: StrategyRun;
			if (mode === "fixture") {
				const decision = fixtureDecision(strategy);
				run = {
					id: `run-${Date.now().toString(36)}`,
					strategy_id: strategy.id,
					...decision,
					created_at: new Date().toISOString(),
				};
			} else {
				run = await runStrategy(strategy.id);
			}
			setRuns((current) => [run, ...current]);
			setStrategies((current) =>
				current.map((row) =>
					row.id === strategy.id ? { ...row, last_run_at: run.created_at } : row,
				),
			);
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

	async function remove(strategy: Strategy) {
		setBusy(`delete:${strategy.id}`);
		setNotice(null);
		try {
			if (mode === "live") {
				await deleteStrategy(strategy.id);
			}
			setStrategies((current) => current.filter((row) => row.id !== strategy.id));
			setRuns((current) => current.filter((run) => run.strategy_id !== strategy.id));
			setConfirmDelete(null);
			setNotice({ tone: "ok", text: `Deleted ${strategy.name}.` });
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The strategy could not be deleted.",
			});
		} finally {
			setBusy(null);
		}
	}

	return (
		<>
			<section className="kpi-band mb-12" aria-label="Strategy summary">
				<div className="stat">
					<div className="label">Installed</div>
					<div className="value sm">{strategies.length}</div>
					<div className="meta">declarative configs · no code</div>
				</div>
				<div className="stat">
					<div className="label">Active</div>
					<div className="value sm ok">{activeCount}</div>
					<div className="meta">schedule eligible</div>
				</div>
				<div className="stat">
					<div className="label">Paused</div>
					<div className="value sm">{pausedCount}</div>
					<div className="meta">never evaluated while paused</div>
				</div>
				<div className="stat">
					<div className="label">
						Paper PnL · 7d <Badge tone="ghost">PAPER</Badge>
					</div>
					<div className="value sm">
						{mode === "fixture"
							? pnl(
									String(
										strategies.reduce(
											(sum, strategy) => sum + Number(strategy.pnl_usd ?? 0),
											0,
										),
									),
								)
							: "—"}
					</div>
					<div className="meta">
						{mode === "fixture" ? "fixture sample" : "not returned by the list endpoint"}
					</div>
				</div>
			</section>

			{notice && (
				<div className={`${styles.notice} ${notice.tone} mb-12`} role="status">
					{notice.text}
				</div>
			)}

			<div className={styles.board}>
				<section aria-label="Installed strategies">
					<div className="between mb-8">
						<div>
							<h2 style={{ fontSize: 13 }}>Installed strategies</h2>
							<div className="small muted">
								{strategies.length} total · {activeCount} active · {pausedCount} paused
							</div>
						</div>
						<Badge tone="ghost">PAPER</Badge>
					</div>

					{strategies.length === 0 ? (
						<div className={styles.empty}>
							<strong>No strategies are installed.</strong>
							Nothing is bundled and nothing trades by default. Create a declarative
							strategy to let the engine evaluate it in paper mode.
						</div>
					) : (
						<div className={styles.list}>
							{strategies.map((strategy) => {
								const meta = strategyKindMeta[strategy.kind];
								const last = latest.get(strategy.id);
								const paused = strategy.status === "paused";
								const rowBusy =
									busy === `run:${strategy.id}` ||
									busy === `status:${strategy.id}` ||
									busy === `delete:${strategy.id}`;
								return (
									<article className={styles.card} key={strategy.id}>
										<div className={styles.cardHead}>
											<div>
												<h3>
													<Link href={`/strategies/${strategy.id}`}>{strategy.name}</Link>
												</h3>
												<div className={styles.cardSub}>
													{strategy.token_symbol} · {strategy.chain} · {meta.label} ·{" "}
													<span title={strategy.token_address}>
														{strategy.token_address.slice(0, 6)}…
														{strategy.token_address.slice(-4)}
													</span>
												</div>
											</div>
											<div className={styles.badges}>
												<Badge tone={paused ? "warn" : "ok"}>
													{paused ? "paused" : "active"}
												</Badge>
												<Badge tone="ghost">PAPER</Badge>
											</div>
										</div>

										<dl className={styles.facts}>
											<div>
												<dt>Last run</dt>
												<dd title={stamp(strategy.last_run_at)}>
													{strategy.last_run_at
														? strategy.last_run_at.slice(5, 16).replace("T", " ")
														: "never"}
												</dd>
											</div>
											<div>
												<dt>
													PnL · 7d <Badge tone="ghost">PAPER</Badge>
												</dt>
												<dd
													className={
														strategy.pnl_usd && Number(strategy.pnl_usd) < 0 ? "err" : undefined
													}
													title={
														mode === "fixture"
															? "fixture sample"
															: "not returned by the strategy list endpoint"
													}
												>
													{strategy.pnl_usd ? pnl(strategy.pnl_usd) : "—"}
												</dd>
											</div>
											<div>
												<dt>Schedule</dt>
												<dd>
													{strategy.params.interval_minutes
														? duration(Number(strategy.params.interval_minutes))
														: "—"}
												</dd>
											</div>
											<div>
												<dt>Kind</dt>
												<dd>{meta.label}</dd>
											</div>
										</dl>

										<div className={styles.params}>{paramSummary(strategy)}</div>

										<div className={styles.decision}>
											<span className={styles.decisionLabel}>Last decision</span>
											{last ? (
												<>
													<Badge tone={actionTone(last.action)}>{last.action}</Badge>
													<span>{last.reason}</span>
												</>
											) : (
												<span className="muted">No runs yet · run-now evaluates once.</span>
											)}
										</div>

										<div className={styles.actions}>
											<ButtonLink href={`/strategies/${strategy.id}`} size="sm">
												Open
											</ButtonLink>
											<button
												className="btn sm"
												type="button"
												onClick={() => void toggleStatus(strategy)}
												disabled={rowBusy}
											>
												{paused ? "Resume" : "Pause"}
											</button>
											<button
												className="btn sm primary"
												type="button"
												onClick={() => void runNow(strategy)}
												disabled={paused || rowBusy}
												title={paused ? "Resume the strategy before running it" : undefined}
											>
												{busy === `run:${strategy.id}` ? "Running…" : "Run now"}
											</button>
											{confirmDelete === strategy.id ? (
												<span className={styles.confirm}>
													Delete “{strategy.name}”?
													<button
														className="btn danger sm"
														type="button"
														onClick={() => void remove(strategy)}
														disabled={rowBusy}
													>
														Confirm delete
													</button>
													<button
														className="btn ghost sm"
														type="button"
														onClick={() => setConfirmDelete(null)}
													>
														Cancel
													</button>
												</span>
											) : (
												<button
													className="btn ghost sm"
													type="button"
													onClick={() => setConfirmDelete(strategy.id)}
												>
													Delete
												</button>
											)}
										</div>
									</article>
								);
							})}
						</div>
					)}

					<Panel
						className="mt-16"
						flush
						title="Decision log"
						sub={`${runs.length} runs · newest first`}
						action={<Badge tone="ghost">PAPER</Badge>}
						footer="Each run records its inputs, decision, reason and paper order id. A denied or skipped run is still a run."
					>
						{runs.length === 0 ? (
							<div className="panel-body small muted">
								No runs yet. Run-now or let the scheduler evaluate an active strategy.
							</div>
						) : (
							<div className={`table-wrap ${styles.logTable}`}>
								<table className="table compact">
									<thead>
										<tr>
											<th scope="col">Time</th>
											<th scope="col">Strategy</th>
											<th scope="col">Action</th>
											<th scope="col">Decision / reason</th>
											<th scope="col" className="col-hide-sm">
												Order
											</th>
										</tr>
									</thead>
									<tbody>
										{runs.slice(0, 14).map((run) => {
											const strategy = strategies.find((row) => row.id === run.strategy_id);
											return (
												<tr key={run.id}>
													<td className="mono" title={stamp(run.created_at)}>
														{run.created_at.slice(11, 19)}
													</td>
													<td>
														{strategy ? (
															<Link href={`/strategies/${strategy.id}`}>{strategy.name}</Link>
														) : (
															<span className="muted">deleted strategy</span>
														)}
													</td>
													<td>
														<Badge tone={actionTone(run.action)}>
															<span className={styles.runAction}>{run.action}</span>
														</Badge>
													</td>
													<td>{run.reason}</td>
													<td className="col-hide-sm">
														{run.order_id ? (
															<code className="mono">{run.order_id}</code>
														) : (
															<span className="muted">—</span>
														)}
													</td>
												</tr>
											);
										})}
									</tbody>
								</table>
							</div>
						)}
					</Panel>
				</section>

				<Panel
					title="Create strategy"
					sub="declarative config only · no code"
					action={<Badge tone="ghost">PAPER</Badge>}
				>
					<form className={styles.form} onSubmit={(event) => void submit(event)} id="new-strategy">
						<div className={styles.formGrid}>
							<div className={`field ${styles.wide}`}>
								<label htmlFor="strategy-name">Strategy name</label>
								<input
									className="input"
									id="strategy-name"
									name="name"
									value={form.name}
									onChange={(event) => updateField("name", event.target.value)}
									placeholder="BONK momentum"
									autoComplete="off"
								/>
								{errors.name && <span className="error">{errors.name}</span>}
							</div>

							<div className="field">
								<label htmlFor="strategy-chain">Chain</label>
								<select
									className="select"
									id="strategy-chain"
									name="chain"
									value={form.chain}
									onChange={(event) => updateField("chain", event.target.value)}
								>
									{strategyChains.map((chain) => (
										<option value={chain.id} key={chain.id}>
											{chain.name}
										</option>
									))}
								</select>
							</div>

							<div className="field">
								<label htmlFor="strategy-symbol">Symbol</label>
								<input
									className="input mono"
									id="strategy-symbol"
									name="token_symbol"
									value={form.token_symbol}
									onChange={(event) => updateField("token_symbol", event.target.value)}
									placeholder="BONK"
									autoComplete="off"
								/>
								{errors.token_symbol && <span className="error">{errors.token_symbol}</span>}
							</div>

							<div className={`field ${styles.wide}`}>
								<label htmlFor="strategy-token">Token address</label>
								<input
									className="input mono"
									id="strategy-token"
									name="token_address"
									value={form.token_address}
									onChange={(event) => updateField("token_address", event.target.value)}
									placeholder="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
									autoComplete="off"
									spellCheck={false}
								/>
								{errors.token_address && (
									<span className="error">{errors.token_address}</span>
								)}
							</div>
						</div>

						<div className="field">
							<span className="label">Kind</span>
							<div className={styles.kindGrid} role="group" aria-label="Strategy kind">
								{(Object.keys(strategyKindMeta) as StrategyKind[]).map((kind) => {
									const meta = strategyKindMeta[kind];
									return (
										<button
											className={styles.kindOption}
											type="button"
											key={kind}
											aria-pressed={form.kind === kind}
											onClick={() => {
												updateField("kind", kind);
												setErrors({});
											}}
										>
											<span className={styles.name}>{meta.label}</span>
											<span className={styles.rule}>{meta.rule}</span>
										</button>
									);
								})}
							</div>
						</div>

						<div className={styles.formGrid}>
							{fields.map((field) => (
								<div className="field" key={field.name}>
									<label htmlFor={`strategy-${field.name}`}>{field.label}</label>
									<input
										className={`input ${field.type === "text" ? "" : "mono"}`}
										id={`strategy-${field.name}`}
										name={field.name}
										type={field.type}
										min={field.min}
										step={field.step}
										value={form.params[field.name] ?? ""}
										onChange={(event) => updateParam(field.name, event.target.value)}
									/>
									{errors[field.name] ? (
										<span className="error">{errors[field.name]}</span>
									) : (
										<span className="hint">{field.hint}</span>
									)}
								</div>
							))}
						</div>

						<div className={styles.formFoot}>
							<button className="btn primary" type="submit" disabled={busy === "create"}>
								{busy === "create" ? "Creating…" : "Create strategy"}
							</button>
							<span className="small muted">
								{mode === "fixture"
									? "Fixture session: nothing is sent to an API."
									: "POST /api/v1/strategies · the engine owns evaluation."}
							</span>
						</div>
					</form>
				</Panel>
			</div>
		</>
	);
}
