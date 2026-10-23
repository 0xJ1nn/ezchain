import Link from "next/link";
import { AppShell } from "@/components/shell";
import { PositionTile } from "@/components/position-tile";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
import { positionTiles } from "@/lib/position-fixtures";
import { usd, signedUsd, MINUS } from "@/lib/format";
import {
	fixturePaperAccountFlat,
	fixturePaperPositionsUnpricedSource,
	getPaperSnapshot,
	type PaperAccount,
	type PaperPosition,
	type PaperResult,
	type PaperSnapshot,
} from "@/lib/paper-api";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const STALE_AFTER_SECONDS = 60;

function money(value: string | null | undefined, signed = false) {
	if (value === null || value === undefined || value === "") return "—";
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	if (amount !== 0 && Math.abs(amount) < 0.01) {
		const decimal = Math.abs(amount).toFixed(12).replace(/0+$/, "").replace(/\.$/, "");
		return `${amount < 0 ? MINUS : ""}$${decimal}`;
	}
	if (amount === 0) return usd(0);
	return signed ? signedUsd(amount) : usd(amount);
}

function quantity(value: string) {
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	return amount.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

interface MarkAge {
	label: string;
	stale: boolean;
	unknown: boolean;
}

function markAge(position: PaperPosition): MarkAge {
	// A source describes a mark. An unmarked position keeps no source even if an older
	// server leaked one, so the mark is unknown regardless of source.observed_at.
	if (position.mark_usd === null || !position.source?.observed_at) {
		return { label: "unknown mark", stale: false, unknown: true };
	}
	const observed = Date.parse(position.source.observed_at);
	if (Number.isNaN(observed)) {
		return { label: "unknown age", stale: false, unknown: true };
	}
	const seconds = Math.max(0, Math.round((Date.now() - observed) / 1000));
	if (seconds <= STALE_AFTER_SECONDS) {
		return { label: `${seconds}s`, stale: false, unknown: false };
	}
	const minutes = Math.round(seconds / 60);
	return { label: `${minutes}m`, stale: true, unknown: false };
}

function chainCount(positions: PaperPosition[]) {
	return new Set(positions.map((position) => position.chain)).size;
}

function needsAttention(positions: PaperPosition[]) {
	return positions.filter((position) => {
		const age = markAge(position);
		return age.stale || age.unknown || position.mark_usd === null;
	}).length;
}

function markCountLabel(count: number) {
	return `${count} open mark${count === 1 ? "" : "s"} unknown`;
}

function demoResult<T>(mode: PaperSnapshot["mode"], message: string): PaperResult<T> {
	return {
		ok: false,
		mode,
		error: { code: "FIXTURE_DEMO_ERROR", message },
	};
}

export default async function PositionsPage({
	searchParams,
}: {
	searchParams: Promise<{ state?: string }>;
}) {
	const { state } = await searchParams;
	const snapshot = await getPaperSnapshot();
	const mode = snapshot.mode;

	// Deterministic state gallery in fixture mode only. Live mode always shows the API's
	// answer, including an outage — a demo flag never masks real data.
	const demo = mode === "fixture" ? state : undefined;
	const accountResult =
		demo === "error"
			? demoResult<PaperAccount>(mode, "Fixture demo: the paper account request failed.")
			: demo === "empty"
				? {
						ok: true as const,
						mode,
						data: fixturePaperAccountFlat,
					}
				: snapshot.account;
	const positionsResult =
		demo === "error"
			? demoResult<PaperPosition[]>(mode, "Fixture demo: the positions request failed.")
			: demo === "empty"
				? { ok: true as const, mode, data: [] as PaperPosition[] }
				: demo === "unpriced-source"
					? { ok: true as const, mode, data: fixturePaperPositionsUnpricedSource }
					: demo === "stale"
					? {
							ok: true as const,
							mode,
							data: snapshot.positions.ok
								? snapshot.positions.data.map((position) => ({
										...position,
										source: position.source
											? {
													...position.source,
													observed_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
												}
											: null,
									}))
								: [],
						}
					: snapshot.positions;

	const account = accountResult.ok ? accountResult.data : null;
	const positions = positionsResult.ok ? positionsResult.data : [];
	const positionsFailed = !positionsResult.ok;
	const failed = !accountResult.ok || positionsFailed;
	const staleCount = positions.filter((position) => markAge(position).stale).length;
	const unknownMarkCount = positions.filter((position) => position.mark_usd === null).length;
	const attention = needsAttention(positions);

	const errorMessage = !accountResult.ok
		? accountResult.error.message
		: !positionsResult.ok
			? positionsResult.error.message
			: null;
	const errorCode = !accountResult.ok
		? accountResult.error.code
		: !positionsResult.ok
			? positionsResult.error.code
			: null;

	return (
		<AppShell active="positions">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Positions</div>
					<h1>Positions</h1>
					<p className="sub">
						Paper account and open positions read from{" "}
						<span className="mono">GET /api/v1/paper/account</span> and{" "}
						<span className="mono">/api/v1/paper/positions</span>. The page formats the
						server view model; it never recomputes PnL, cost basis or executable value.
						An unknown mark is <span className="mono">—</span>, never zero.
					</p>
				</div>
				<div className="page-actions">
					<Badge tone={mode === "fixture" ? "ghost" : "info"} title="Data source">
						{mode === "fixture" ? "FIXTURE" : "LIVE API"}
					</Badge>
					<Link className="btn" href="/ledger">
						Closed trades
					</Link>
					<Link className="btn primary" href="/markets">
						New order
					</Link>
				</div>
			</header>

			<section className="kpi-band mb-12" aria-label="Paper account">
				<Stat
					label={
						<span>
							Cash
							<span className={styles.paperTag}>
								<Badge tone="ghost">PAPER</Badge>
							</span>
						</span>
					}
					value={account ? money(account.cash_usd) : "—"}
					meta={account ? "ledger cash · USD" : "account unavailable"}
				/>
				<Stat
					label={
						<span>
							Equity
							<span className={styles.paperTag}>
								<Badge tone="ghost">PAPER</Badge>
							</span>
						</span>
					}
					value={account ? money(account.equity_usd) : "—"}
					meta={
						account
							? account.marks_unknown
								? `equity unavailable — ${markCountLabel(account.unpriced_positions.length)}`
								: "cash + known marks"
							: "account unavailable"
					}
				/>
				<Stat
					label={
						<span>
							Realized
							<span className={styles.paperTag}>
								<Badge tone="ghost">PAPER</Badge>
							</span>
						</span>
					}
					value={account ? money(account.realized_usd, true) : "—"}
					tone={account && Number(account.realized_usd) < 0 ? "err" : "ok"}
					meta="closed trades · fees included"
				/>
				<Stat
					label={
						<span>
							Unrealized
							<span className={styles.paperTag}>
								<Badge tone="ghost">PAPER</Badge>
							</span>
						</span>
					}
					value={account ? money(account.unrealized_usd, true) : "—"}
					tone={
						account?.unrealized_usd !== null &&
						account?.unrealized_usd !== undefined &&
						Number(account.unrealized_usd) < 0
							? "err"
							: "ok"
					}
					meta={
						account?.marks_unknown
							? `unrealized unavailable — ${markCountLabel(account.unpriced_positions.length)}`
							: "known marks · not executable"
					}
				/>
				<Stat
					label={
						<span>
							Open positions
							<span className={styles.paperTag}>
								<Badge tone="ghost">PAPER</Badge>
							</span>
						</span>
					}
					value={String(positions.length)}
					meta={
						positions.length > 0
							? `${chainCount(positions)} chain${chainCount(positions) === 1 ? "" : "s"} · ${staleCount} stale`
							: positionsFailed
								? "positions unavailable"
								: "flat"
					}
				/>
				<Stat
					label={
						<span>
							Needs attention
							<span className={styles.paperTag}>
								<Badge tone="ghost">PAPER</Badge>
							</span>
						</span>
					}
					value={String(attention)}
					tone={attention > 0 ? "err" : undefined}
					meta={
						attention > 0
							? `${unknownMarkCount} unknown mark · ${staleCount} stale`
							: "every mark fresh"
					}
				/>
			</section>

			{!failed && account?.marks_unknown && (
				<div className="note warn mb-12">
					<strong>
						Equity unavailable — {markCountLabel(account.unpriced_positions.length)}.
					</strong>{" "}
					The account total and every equity share are withheld until every open position
					has a mark. Known marks and the USD cash balance are still shown; they are not a
					total.
				</div>
			)}

			{errorMessage && (
				<section className={`panel mb-12 ${styles.statePanel}`} role="alert">
					<div className="panel-head">
						<h2>Paper data unavailable</h2>
						<div className="right">
							<Badge tone="err">{errorCode ?? "ERROR"}</Badge>
						</div>
					</div>
					<div className="panel-body">
						<div className={styles.stateTitle}>Nothing is being guessed.</div>
						<p className={styles.stateBody}>
							{errorMessage} Missing account and position values print{" "}
							<span className="mono">—</span>; no tile or total is fabricated from a
							failed request.
						</p>
						<div className="row mt-12">
							<Link className="btn" href="/positions">
								Retry
							</Link>
							{mode === "fixture" && (
								<span className="small muted">
									Fixture demo state <span className="mono">?state=error</span>
								</span>
							)}
						</div>
					</div>
				</section>
			)}

			{!failed && staleCount > 0 && (
				<div className="note warn mb-12">
					<strong>{staleCount} stale mark{staleCount === 1 ? "" : "s"}.</strong> A mark older
					than {STALE_AFTER_SECONDS}s is labelled stale and is not executable. Unrealized
					values stay marked to that last known price.
				</div>
			)}

			<Panel
				flush
				title="Server positions"
				sub={`${positions.length} from /api/v1/paper/positions`}
				action={<Badge tone="ghost">PAPER</Badge>}
				footer={
					<div className="between" style={{ flexWrap: "wrap", gap: 8 }}>
						<span>
							Server view model · mark age from <span className="mono">source.observed_at</span> ·{" "}
							{STALE_AFTER_SECONDS}s stale threshold
						</span>
						<span>
							{mode === "fixture"
								? "EZCHAIN_WEB_MODE=fixture · sample account, no API call"
								: "Live API · paper mode"}
						</span>
					</div>
				}
			>
				{positions.length > 0 ? (
					<div className="table-wrap">
						<table className="table">
							<thead>
								<tr>
									<th scope="col">Token</th>
									<th scope="col" className="col-hide-sm">
										Chain
									</th>
									<th scope="col" className="num">
										Quantity
									</th>
									<th scope="col" className="num col-hide-sm">
										Avg cost
									</th>
									<th scope="col" className="num">
										Mark
									</th>
									<th scope="col" className="num col-hide-sm">
										Value
									</th>
									<th scope="col" className="num">
										Unrealized
									</th>
									<th scope="col" className="num col-hide-sm">
										Realized
									</th>
									<th scope="col">Source · age</th>
									<th scope="col">Mode</th>
								</tr>
							</thead>
							<tbody>
								{positions.map((position) => {
									const age = markAge(position);
									const unrealized = position.unrealized_usd;
									return (
										<tr
											key={`${position.chain}:${position.token_address}`}
											className={age.stale ? styles.staleRow : undefined}
										>
											<td>
												<div className={styles.tokenCell}>
													<span className="token sm" aria-hidden="true">
														{position.symbol.slice(0, 2).toUpperCase()}
													</span>
													<span>
														<span className="strong">{position.symbol}</span>
														<span
															className={styles.tokenAddress}
															style={{ display: "block" }}
															title={position.token_address}
														>
															{position.token_address.slice(0, 6)}…
															{position.token_address.slice(-4)}
														</span>
													</span>
												</div>
											</td>
											<td className="col-hide-sm">{position.chain}</td>
											<td className="num mono">{quantity(position.quantity)}</td>
											<td className="num mono col-hide-sm">
												{money(position.avg_cost_usd)}
											</td>
											<td className={`num ${styles.monoCell}`}>
												{position.mark_usd === null ? (
													<span className={styles.unknown}>—</span>
												) : (
													money(position.mark_usd)
												)}
											</td>
											<td className="num mono col-hide-sm">
												{money(position.market_value_usd)}
											</td>
											<td
												className={`num ${styles.monoCell}${
													unrealized && Number(unrealized) < 0 ? " err" : ""
												}`}
											>
												{unrealized === null ? (
													<span className={styles.unknown}>—</span>
												) : (
													money(unrealized, true)
												)}
											</td>
											<td className="num mono col-hide-sm">
												{money(position.realized_usd, true)}
											</td>
											<td>
												{position.mark_usd !== null && position.source ? (
													<span className={styles.accountMeta}>
														<Badge tone={age.stale ? "warn" : "ghost"}>
															{age.stale ? `stale · ${age.label}` : `${position.source.name} · ${age.label}`}
														</Badge>
													</span>
												) : (
													<Badge tone="warn">unknown mark</Badge>
												)}
											</td>
											<td>
												<Badge tone="ghost">{position.mode}</Badge>
											</td>
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				) : (
					<div className="panel-body">
						<div className={styles.stateTitle}>
							{positionsFailed ? "No positions read" : "No open positions"}
						</div>
						<p className={styles.stateBody}>
							{positionsFailed
								? "The positions request failed. Nothing is rendered as a position; missing values stay —."
								: "The paper account is flat. Open a market and place a paper order to create the first position. Unknown values are never shown as zero."}
						</p>
						{!positionsFailed && (
							<div className="row mt-12">
								<ButtonLink href="/markets" variant="primary">
									Browse markets
								</ButtonLink>
								{mode === "fixture" && (
									<span className="small muted">
										Fixture demo state <span className="mono">?state=empty</span>
									</span>
								)}
							</div>
						)}
					</div>
				)}
			</Panel>

			{mode === "fixture" && !failed && positions.length > 0 && (
				<section className="mt-16" aria-label="Position tiles">
					<div className={styles.tileHeading}>
						<h2>Position tiles</h2>
						<span className="small muted">
							the existing tile component · fixture chart and ladder fixture
						</span>
						<Badge tone="ghost">FIXTURE TILES</Badge>
					</div>
					<section className="grid cols-2 tiles" id="open">
						{positionTiles.map((position) => (
							<PositionTile position={position} key={position.id} />
						))}
					</section>
				</section>
			)}
		</AppShell>
	);
}
