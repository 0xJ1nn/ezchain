import Link from "next/link";
import { AppShell } from "@/components/shell";
import { PositionDetailed, PositionGrid, PositionList, PositionViewSwitcher } from "@/components/position-views";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
import { getCandles } from "@/lib/api";
import { candleErrorFrom, type CandleLoad } from "@/lib/candle-view";
import {
	fixturePaperAccountFlat,
	fixturePaperPositionsUnpricedSource,
	getPaperSnapshot,
	type PaperAccount,
	type PaperPosition,
	type PaperResult,
	type PaperSnapshot,
} from "@/lib/paper-api";
import {
	POSITION_SORTS,
	STALE_AFTER_SECONDS,
	chainOptions,
	money,
	markAge,
	parseChainFilter,
	parsePositionSort,
	parsePositionView,
	positionKey,
	positionsHref,
	visiblePositions,
	type PositionQuery,
} from "@/lib/position-view";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

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

async function detailedCandles(positions: PaperPosition[]): Promise<Record<string, CandleLoad>> {
	const entries: [string, CandleLoad][] = [];
	// Bounded concurrency: Detailed mode is a page render, not a provider stress test.
	for (let start = 0; start < positions.length; start += 6) {
		const batch = positions.slice(start, start + 6);
		const loaded = await Promise.all(
			batch.map(async (position): Promise<[string, CandleLoad]> => {
				try {
					const series = await getCandles(position.chain, position.token_address, {
						timeframe: "1h",
						limit: 60,
					});
					return [positionKey(position), { ok: true, series }];
				} catch (error) {
					return [positionKey(position), { ok: false, error: candleErrorFrom(error) }];
				}
			}),
		);
		entries.push(...loaded);
	}
	return Object.fromEntries(entries);
}

export default async function PositionsPage({
	searchParams,
}: {
	searchParams: Promise<{ state?: string; view?: string; chain?: string; sort?: string }>;
}) {
	const raw = await searchParams;
	const snapshot = await getPaperSnapshot();
	const mode = snapshot.mode;
	const view = parsePositionView(raw.view);
	const sort = parsePositionSort(raw.sort);

	// Deterministic state gallery in fixture mode only. Live mode always shows the API's
	// answer, including an outage — a demo flag never masks real data.
	const demo = mode === "fixture" ? raw.state : undefined;
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
		demo === "error" || demo === "positions-error"
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

	const chains = chainOptions(positions);
	const chain = parseChainFilter(raw.chain, positions);
	const query: PositionQuery = { view, chain, sort, state: demo };
	const visible = visiblePositions(positions, { chain, sort });
	const candles = view === "detailed" && !positionsFailed
		? await detailedCandles(visible)
		: {};

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
						<span className="mono">/api/v1/paper/positions</span>. List, Grid and Detailed
						render the same server rows; the view is a URL parameter and every row opens a
						full position. The page formats the server view model; it never recomputes PnL,
						cost basis or executable value. An unknown mark is{" "}
						<span className="mono">—</span>, never zero.
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
					tone={
						account
							? Number(account.realized_usd) < 0
								? "err"
								: "ok"
							: undefined
					}
					meta={account ? "closed trades · fees included" : "account unavailable"}
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
					value={positionsFailed ? "—" : String(positions.length)}
					meta={
						positionsFailed
							? "positions unavailable"
							: positions.length > 0
								? `${chainCount(positions)} chain${chainCount(positions) === 1 ? "" : "s"} · ${staleCount} stale`
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
					value={positionsFailed ? "—" : String(attention)}
					tone={!positionsFailed && attention > 0 ? "err" : undefined}
					meta={
						positionsFailed
							? "count not read"
							: attention > 0
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
				sub={
					positionsFailed
						? "positions unavailable · count not read"
						: `${positions.length} from /api/v1/paper/positions`
				}
				action={<Badge tone="ghost">PAPER</Badge>}
				footer={
					<div className="between" style={{ flexWrap: "wrap", gap: 8 }}>
						<span>
							Server view model · {view} view · mark age from{" "}
							<span className="mono">source.observed_at</span> · {STALE_AFTER_SECONDS}s
							stale threshold
						</span>
						<span>
							{mode === "fixture"
								? "EZCHAIN_WEB_MODE=fixture · sample account, no API call"
								: "Live API · paper mode"}
						</span>
					</div>
				}
			>
				<div className={styles.toolbar}>
					<PositionViewSwitcher view={view} query={query} />
					<form
						action="/positions"
						method="get"
						className={styles.filters}
						aria-label="Position filters"
					>
						<input type="hidden" name="view" value={view} />
						{demo && <input type="hidden" name="state" value={demo} />}
						<label className={styles.filterField}>
							<span className="micro">Chain</span>
							<select
								className="select sm"
								name="chain"
								key={`chain-${chain ?? "all"}`}
								defaultValue={chain ?? "all"}
							>
								<option value="all">All chains</option>
								{chains.map((option) => (
									<option value={option} key={option}>
										{option}
									</option>
								))}
							</select>
						</label>
						<label className={styles.filterField}>
							<span className="micro">Sort</span>
							<select
								className="select sm"
								name="sort"
								key={`sort-${sort}`}
								defaultValue={sort}
							>
								{POSITION_SORTS.map((option) => (
									<option value={option.value} key={option.value}>
										{option.label}
									</option>
								))}
							</select>
						</label>
						<button className="btn sm" type="submit">
							Apply
						</button>
						<Link className="btn sm" href={positionsHref({ view, state: demo })}>
							Reset
						</Link>
					</form>
					<span className={styles.toolbarNote}>
						View, chain filter and sort persist in the URL. Strategy filtering is not
						available: the server position model carries no strategy field.
					</span>
				</div>

				{positions.length === 0 ? (
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
				) : visible.length === 0 ? (
					<div className="panel-body">
						<div className={styles.stateTitle}>No positions on this chain filter</div>
						<p className={styles.stateBody}>
							The account has {positions.length} open position
							{positions.length === 1 ? "" : "s"}, none on{" "}
							<span className="mono">{chain}</span>. Clear the filter to see every server
							row.
						</p>
						<div className="row mt-12">
							<Link className="btn" href={positionsHref({ view, sort, state: demo })}>
								Clear chain filter
							</Link>
						</div>
					</div>
				) : view === "grid" ? (
					<PositionGrid positions={visible} query={query} />
				) : view === "detailed" ? (
					<PositionDetailed
						positions={visible}
						query={query}
						candles={candles}
						timeframe="1h"
					/>
				) : (
					<PositionList positions={visible} query={query} />
				)}
			</Panel>
		</AppShell>
	);
}
