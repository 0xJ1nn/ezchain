import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/shell";
import { CandleView } from "@/components/token-chart";
import { Badge, Panel, Stat } from "@/components/ui";
import { getCandles, getToken, asApiError } from "@/lib/api";
import {
	TIMEFRAMES,
	candleErrorFrom,
	parseCandleFixtureState,
	parseCandleTimeframe,
	type CandleLoad,
} from "@/lib/candle-view";
import { getPaperFills, getPaperPositions, type PaperFillRecord } from "@/lib/paper-api";
import {
	STALE_AFTER_SECONDS,
	money,
	markAge,
	parsePositionSort,
	parsePositionView,
	positionRouteHref,
	positionsHref,
	quantity,
} from "@/lib/position-view";
import styles from "./detail.module.css";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function one(value: string | string[] | undefined): string | undefined {
	const raw = Array.isArray(value) ? value[0] : value;
	return raw && raw.trim() ? raw.trim() : undefined;
}

export default async function PositionDetailPage({
	params,
	searchParams,
}: {
	params: Promise<{ chain: string; token: string }>;
	searchParams: Promise<SearchParams>;
}) {
	const { chain, token } = await params;
	const raw = await searchParams;
	const view = parsePositionView(raw.view);
	const sort = parsePositionSort(raw.sort);
	const state = one(raw.state);
	const chainFilter = one(raw.chain);
	const backHref = positionsHref({ view, chain: chainFilter, sort, state });
	const timeframe = parseCandleTimeframe(raw.timeframe);

	const positionsResult = await getPaperPositions();
	if (!positionsResult.ok) {
		return (
			<AppShell active="positions">
				<header className="page-head">
					<div className="titles">
						<div className="crumbs">
							<Link href={backHref}>Positions</Link> / {chain}
						</div>
						<h1>Position unavailable</h1>
					</div>
				</header>
				<Panel
					title="No position read"
					action={<Badge tone="err">{positionsResult.error.code}</Badge>}
				>
					<p className={styles.body}>{positionsResult.error.message}</p>
					<p className={styles.body}>
						The positions request failed, so this page does not know whether the position
						exists. No value or chart is fabricated from the failure.
					</p>
					<div className="row mt-12">
						<Link className="btn" href={backHref}>
							Back to positions
						</Link>
					</div>
				</Panel>
			</AppShell>
		);
	}

	const position = positionsResult.data.find(
		(candidate) =>
			candidate.chain === chain &&
			(candidate.token_address === token ||
				candidate.token_address.toLowerCase() === token.toLowerCase()),
	);
	if (!position) notFound();

	const mode = positionsResult.mode;
	const fixtureState = mode === "fixture" ? parseCandleFixtureState(raw.candles) : undefined;
	const [tokenLookup, candles, fillsResult] = await Promise.all([
		getToken(position.chain, position.token_address)
			.then(() => ({ ok: true as const }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getCandles(position.chain, position.token_address, {
			timeframe,
			limit: 200,
			fixtureState,
		})
			.then((series) => ({ ok: true as const, series }) satisfies CandleLoad)
			.catch(
				(error) =>
					({ ok: false as const, error: candleErrorFrom(error) }) satisfies CandleLoad,
			),
		getPaperFills(200),
	]);
	// The sell ticket lives on the token route. Only offer it when the token registry
	// resolves this exact position; otherwise the primary action would be a 404.
	const sellHref = tokenLookup.ok
		? `/tokens/${encodeURIComponent(position.chain)}/${encodeURIComponent(
				position.token_address,
			)}?side=sell`
		: null;
	const sellUnavailableReason = !tokenLookup.ok
		? tokenLookup.error.code === "NOT_FOUND" || tokenLookup.error.code === "CHAIN_NOT_FOUND"
			? "this token is not in the market registry"
			: `the token registry lookup failed (${tokenLookup.error.code})`
		: null;

	const fills: PaperFillRecord[] = fillsResult.ok
		? fillsResult.data
				.filter(
					(fill) =>
						fill.chain === position.chain &&
						fill.token_address.toLowerCase() === position.token_address.toLowerCase(),
				)
				.slice(0, 6)
		: [];

	const age = markAge(position);
	const stale = age.stale || position.mark_usd === null;
	const routeQuery = {
		view,
		chain: chainFilter,
		sort,
		state,
		candles: fixtureState,
	};

	return (
		<AppShell active="positions">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href={backHref}>Positions</Link> / {position.chain} / {position.symbol}
					</div>
					<div className="row">
						<span className="token" aria-hidden="true">
							{position.symbol.slice(0, 2).toUpperCase()}
						</span>
						<div>
							<h1>
								{position.symbol}{" "}
								<span className="muted" style={{ fontWeight: 400 }}>
									{position.chain}
								</span>
							</h1>
							<p className={`sub ${styles.address}`}>{position.token_address}</p>
						</div>
					</div>
				</div>
				<div className="page-actions">
					<Badge tone="ghost">PAPER</Badge>
					<Badge tone={stale ? "warn" : "ghost"}>
						{position.mark_usd === null
							? "unknown mark"
							: age.stale
								? `stale · ${age.label}`
								: `mark ${age.label} old`}
					</Badge>
					<Link className="btn" href={backHref}>
						Back to positions
					</Link>
					{sellHref ? (
						<Link className="btn primary" href={sellHref}>
							Paper sell ticket
						</Link>
					) : (
						<>
							<Badge tone="warn">sell unavailable</Badge>
							<span className="small muted">
								Sell unavailable for this token: {sellUnavailableReason}.
							</span>
						</>
					)}
				</div>
			</header>

			<section className="kpi-band mb-12" aria-label="Position facts">
				<Stat
					label="Quantity"
					value={quantity(position.quantity)}
					meta={`${position.symbol} · native units`}
				/>
				<Stat label="Avg cost" value={money(position.avg_cost_usd)} meta="server cost basis/unit" />
				<Stat
					label="Mark"
					value={position.mark_usd === null ? "—" : money(position.mark_usd)}
					meta={
						position.mark_usd === null
							? "unknown · not zero"
							: `${position.source?.name ?? "unknown source"} · ${age.label} old`
					}
					tone={position.mark_usd === null ? "warn" : undefined}
				/>
				<Stat
					label="Value"
					value={money(position.market_value_usd)}
					meta={
						position.market_value_usd === null
							? "unknown · not zero"
							: "known mark · not executable"
					}
					tone={position.market_value_usd === null ? "warn" : undefined}
				/>
				<Stat
					label="Unrealized"
					value={
						position.unrealized_usd === null
							? "—"
							: money(position.unrealized_usd, true)
					}
					tone={
						position.unrealized_usd === null
							? "warn"
							: Number(position.unrealized_usd) < 0
								? "err"
								: "ok"
					}
					meta={
						position.unrealized_usd === null
							? "unknown · not zero"
							: "server PnL · fees included"
					}
				/>
				<Stat
					label="Realized"
					value={money(position.realized_usd, true)}
					tone={Number(position.realized_usd) < 0 ? "err" : "ok"}
					meta="closed quantity · this token"
				/>
			</section>

			{stale && (
				<div className="note warn mb-12">
					<strong>This mark is not fresh.</strong>{" "}
					{position.mark_usd === null
						? "The account has no mark for this token; value and unrealized stay —."
						: `The last observation is ${age.label} old, past the ${STALE_AFTER_SECONDS}s threshold. It is not executable.`}
				</div>
			)}

			<div className="split">
				<div className="stack">
					<Panel
						title="Price history"
						sub={`${position.chain} · closed ${timeframe} bars`}
						action={
							<nav className={styles.timeframes} aria-label="Candle timeframe">
								{TIMEFRAMES.map((option) => (
									<Link
										key={option.value}
										className={`${styles.timeframe}${
											option.value === timeframe ? ` ${styles.timeframeActive}` : ""
										}`}
										href={positionRouteHref(position.chain, position.token_address, {
											...routeQuery,
											timeframe: option.value,
										})}
										aria-current={option.value === timeframe ? "page" : undefined}
									>
										{option.label}
									</Link>
								))}
							</nav>
						}
						footer={
							<span>
								<span className="mono">
									GET /api/v1/tokens/{"{chain}"}/{"{address}"}/candles
								</span>{" "}
								· closed bars only, oldest first · the forming bar is never shown
								{fixtureState && (
									<>
										{" "}
										· fixture demo state{" "}
										<span className="mono">?candles={fixtureState}</span>
									</>
								)}
							</span>
						}
					>
						<CandleView
							load={candles}
							retryHref={positionRouteHref(
								position.chain,
								position.token_address,
								{ ...routeQuery, timeframe },
							)}
						/>
					</Panel>

					<Panel
						flush
						title="Recent fills"
						sub="from /api/v1/paper/fills · this chain and token"
						action={<Link href="/ledger">Ledger</Link>}
						footer={
							<span>
								{fillsResult.ok
									? "Fill history is read-only. The ledger remains the only accountant."
									: `Fills unavailable: ${fillsResult.error.code}. No fill rows are invented.`}
							</span>
						}
					>
						{fills.length > 0 ? (
							<div className="table-wrap">
								<table className="table compact" data-testid="position-fills">
									<thead>
										<tr>
											<th scope="col">Time</th>
											<th scope="col">Side</th>
											<th scope="col" className="num">
												Quantity
											</th>
											<th scope="col" className="num col-hide-sm">
												Price
											</th>
											<th scope="col" className="num">
												Notional
											</th>
											<th scope="col" className="num col-hide-sm">
												Fee
											</th>
											<th scope="col" className="col-hide-sm">
												Quote
											</th>
										</tr>
									</thead>
									<tbody>
										{fills.map((fill) => (
											<tr key={fill.id}>
												<td className={`mono small ${styles.nowrap}`}>
													{fill.created_at.replace("T", " ").slice(0, 16)}
												</td>
												<td>{fill.side}</td>
												<td className={`num mono ${styles.nowrap}`}>
													{quantity(fill.quantity)}
												</td>
												<td className={`num mono col-hide-sm ${styles.nowrap}`}>
													{money(fill.price_usd)}
												</td>
												<td className={`num mono ${styles.nowrap}`}>
													{money(fill.notional_usd)}
												</td>
												<td className={`num mono col-hide-sm ${styles.nowrap}`}>
													{money(fill.fee_usd)}
												</td>
												<td className="small col-hide-sm">{fill.quote_kind}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						) : (
							<div className="panel-body">
								<div className={styles.body}>
									{fillsResult.ok
										? "No fills among the newest 200 account fills for this position. Nothing is filled or closed here."
										: `The fills request failed with ${fillsResult.error.code}. The page does not substitute fixture rows.`}
								</div>
							</div>
						)}
					</Panel>
				</div>

				<div className="stack">
					<Panel title="Position" sub="server view model · read-only">
						<dl className={styles.facts}>
							<div>
								<dt>Chain</dt>
								<dd className="mono">{position.chain}</dd>
							</div>
							<div>
								<dt>Token</dt>
								<dd className={`mono ${styles.address}`}>{position.token_address}</dd>
							</div>
							<div>
								<dt>Mode</dt>
								<dd>
									<Badge tone="ghost">{position.mode}</Badge>
								</dd>
							</div>
							<div>
								<dt>Mark source</dt>
								<dd>{position.source?.name ?? "—"}</dd>
							</div>
							<div>
								<dt>Mark age</dt>
								<dd>
									{age.unknown ? "unknown" : `${age.label} old`}{" "}
									<Badge tone={age.stale ? "warn" : "ghost"}>
										{age.stale ? "stale" : age.unknown ? "unknown" : "fresh"}
									</Badge>
								</dd>
							</div>
							<div>
								<dt>Observed at</dt>
								<dd className="mono small">{position.source?.observed_at ?? "—"}</dd>
							</div>
						</dl>
					</Panel>

					<Panel
						title="Protection"
						action={<Badge tone="ghost">unknown</Badge>}
						footer="The positions API does not report a protection field. This card names the gap instead of inventing a rule."
					>
						<div className={styles.protection}>
							<Badge tone="ghost">Protection status not reported by the positions API</Badge>
							<p className={styles.body}>
								The positions API does not report protection. This page cannot tell
								whether a stop, take-profit or trailing rule is attached to the
								position, so it shows nothing as an existing or absent rule.
							</p>
						</div>
					</Panel>

					<Panel title="Activity" sub="ledger and journal">
						<p className={styles.body}>
							This position is derived from append-only ledger entries. The fill table is
							the activity for this token; the full ledger stays at{" "}
							<Link href="/ledger">/ledger</Link>.
						</p>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
