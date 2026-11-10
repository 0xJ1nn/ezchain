import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, Panel } from "@/components/ui";
import { asApiError, getChains, isLiveMode, type ApiError, type Chain } from "@/lib/api";
import { ModeBanner } from "@/lib/live-banner";
import {
	ageLabel,
	chainInitial,
	changeText,
	changeTone,
	formatPrice,
	initials,
	sourceLabel,
} from "@/lib/live-format";
import {
	getSignalMovers,
	type SignalDirection,
	type SignalMover,
	type SignalMovers,
	type SignalWindow,
} from "@/lib/signals-api";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

type PanelOutcome = { ok: true; data: SignalMovers } | { ok: false; error: ApiError };

const PANELS: {
	id: string;
	window: SignalWindow;
	direction: SignalDirection;
	title: string;
	badge: "UP" | "DOWN";
}[] = [
	{ id: "h1-up", window: "h1", direction: "up", title: "Gainers · 1h", badge: "UP" },
	{ id: "h1-down", window: "h1", direction: "down", title: "Losers · 1h", badge: "DOWN" },
	{ id: "h24-up", window: "h24", direction: "up", title: "Gainers · 24h", badge: "UP" },
	{ id: "h24-down", window: "h24", direction: "down", title: "Losers · 24h", badge: "DOWN" },
];

function one(value: string | string[] | undefined): string {
	return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

function excludedSummary(data: SignalMovers): string {
	const parts: string[] = [];
	const named: [string, number][] = [
		["no token", data.exclusions.unknown_token],
		["no change for the window", data.exclusions.unknown_change],
		["no liquidity", data.exclusions.unknown_liquidity],
		["below the liquidity floor", data.exclusions.below_liquidity_floor],
		["stale mark", data.exclusions.stale],
		["another pair of the same token", data.exclusions.duplicate_token],
		["moved the other way", data.exclusions.wrong_direction],
	];
	for (const [label, count] of named) {
		if (count > 0) parts.push(`${count} ${label}`);
	}
	return parts.length > 0 ? parts.join(", ") : "none";
}

function MoverTable({ rows, chains }: { rows: SignalMover[]; chains: Chain[] }) {
	return (
		<div className="table-wrap">
			<table className="table">
				<thead>
					<tr>
						<th>Token</th>
						<th>Chain</th>
						<th className="num">Change</th>
						<th className="num col-hide-sm">Price</th>
						<th>Source · age</th>
						<th />
					</tr>
				</thead>
				<tbody>
					{rows.map((row) => (
						<tr key={`${row.chain}:${row.pair_address}`}>
							<td>
								<span className="row">
									<span className="token sm" aria-hidden="true">
										{initials(row.symbol)}
									</span>
									<span>
										<span className="strong">{row.symbol}</span>
										<span className="sub" style={{ display: "block" }}>
											{row.name} · {row.dex}
										</span>
									</span>
								</span>
							</td>
							<td>
								<span className="chain">
									<span className="sq">
										{chainInitial(chains.find((c) => c.id === row.chain)?.name ?? row.chain)}
									</span>
									{chains.find((c) => c.id === row.chain)?.name ?? row.chain}
								</span>
							</td>
							<td className={`num mono ${changeTone(row.change_pct)}`}>
								{changeText(row.change_pct)}
							</td>
							<td className="num col-hide-sm">{formatPrice(row.price_usd)}</td>
							<td className="small">
								{sourceLabel(row.source.name)}
								<span className="sub" style={{ display: "block" }}>
									{ageLabel(row.observed_at)} ago
								</span>
							</td>
							<td className="right">
								<Link className="btn sm" href={`/tokens/${row.chain}/${row.token_address}`}>
									Open
								</Link>
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function MoverPanel({
	title,
	badge,
	outcome,
	chains,
}: {
	title: string;
	badge: "UP" | "DOWN";
	outcome: PanelOutcome;
	chains: Chain[];
}) {
	if (!outcome.ok) {
		return (
			<Panel title={title} action={<Badge tone="err">{outcome.error.code}</Badge>} flush>
				<div className="panel-body">
					<div className="note err">{outcome.error.message}</div>
					<div className="small muted mt-8">
						No mover is invented when the market read fails.
					</div>
				</div>
			</Panel>
		);
	}
	const data = outcome.data;
	const sub = data.truncated ? `${data.rows.length} of ${data.total}` : `${data.rows.length}`;
	return (
		<Panel
			title={title}
			sub={sub}
			action={<Badge tone={badge === "UP" ? "ok" : "err"}>{badge}</Badge>}
			flush
		>
			{data.rows.length === 0 ? (
				<div className="panel-body">
					<div className="row">
						<Badge tone="warn">{data.reason ?? "EMPTY"}</Badge>
					</div>
					<EmptyState
						title="No mover qualified"
						links={[{ href: "/markets", label: "Markets" }]}
					>
						Every row of the market read was excluded for this window and direction. The
						table stays empty: an empty result is not a zero change, and no sample mover
						stands in for the missing row.
					</EmptyState>
				</div>
			) : (
				<MoverTable rows={data.rows} chains={chains} />
			)}
		</Panel>
	);
}

export default async function SignalsPage({
	searchParams,
}: {
	searchParams: Promise<SearchParams>;
}) {
	const raw = await searchParams;
	const live = isLiveMode();
	// The fixture states exist so the empty and error surfaces can be seen without a
	// provider. A live build ignores them: the answer is the API's.
	const fixtureState = live ? undefined : one(raw.state);

	const outcomes = await Promise.all(
		PANELS.map((panel) =>
			getSignalMovers({
				window: panel.window,
				direction: panel.direction,
				fixtureState: fixtureState === "empty" || fixtureState === "error" ? fixtureState : undefined,
			})
				.then((data) => ({ ok: true as const, data }))
				.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		),
	);
	const chainsOutcome = await getChains()
		.then((chains) => ({ ok: true as const, chains }))
		.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) }));
	const chains = chainsOutcome.ok ? chainsOutcome.chains : [];

	const failures = outcomes.filter((outcome) => !outcome.ok);
	const allFailed = failures.length === outcomes.length;
	const firstFailure = failures[0];
	const firstOk = outcomes.find((outcome) => outcome.ok);

	return (
		<AppShell active="signals">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Automate / Signals</div>
					<h1>Signals</h1>
					<p className="sub">
						Market movers ranked by the server: the strongest positive and negative 1h and
						24h changes of the tokens that pass the liquidity floor and the freshness
						budget. This is a read-only view of price changes, not a strategy signal
						service and not a safety rating.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={live ? "live" : "fixture"} />
					<Link className="btn" href="/markets">
						Markets
					</Link>
					<Link className="btn" href="/chains">
						Coverage
					</Link>
				</div>
			</header>

			<ModeBanner
				source={firstOk?.ok ? firstOk.data.source.name : null}
				observedAt={firstOk?.ok ? firstOk.data.source.observed_at : null}
				stale={firstOk?.ok ? firstOk.data.stale : false}
				detail="ranked by the API, not in the browser"
			/>

			{allFailed && firstFailure && !firstFailure.ok && (
				<div className="mb-12">
					<Unavailable
						title="Signals unavailable"
						code={firstFailure.error.code}
						message={firstFailure.error.message}
						action={
							<Link className="small" href="/signals">
								Retry →
							</Link>
						}
					>
						No mover is invented when the market read fails.
					</Unavailable>
				</div>
			)}

			{firstOk?.ok && (
				<div className="note mb-12">
					<strong>Population.</strong> {firstOk.data.rows_read} market rows read for the
					first window, {firstOk.data.total} qualified, {excludedSummary(firstOk.data)}{" "}
					excluded. The ranking is server-side: liquidity floor{" "}
					<span className="mono">${firstOk.data.applied.min_liquidity_usd}</span>, marks older
					than <span className="mono">{firstOk.data.applied.max_age_seconds}s</span> are
					dropped, and the answer is cut at{" "}
					<span className="mono">{firstOk.data.applied.limit}</span> rows. Prices are
					discovery marks, not executable quotes, and the API returns no token-safety claim
					here.
				</div>
			)}

			{allFailed ? null : (
				<div className="grid cols-2">
					{PANELS.map((panel, index) => (
						<MoverPanel
							key={panel.id}
							title={panel.title}
							badge={panel.badge}
							outcome={outcomes[index]}
							chains={chains}
						/>
					))}
				</div>
			)}

			<div className="split mt-16">
				<Panel title="What a signal is here" sub="no generic rules engine">
					<div className="list">
						<div className="list-row">
							<Badge tone="ghost">REAL</Badge>
							<span className="grow">
								<span className="title">Server-side price-change ranking</span>
								<span className="sub">
									One row per token, ranked by the window&apos;s own change, with the
									row&apos;s chain, source, observation time and age.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NOT BUILT</Badge>
							<span className="grow">
								<span className="title">Routing, suppression and delivery</span>
								<span className="sub">
									There is no signal rule object, channel delivery or suppression log in this
									build.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NOT CLAIMED</Badge>
							<span className="grow">
								<span className="title">Safety assessment</span>
								<span className="sub">
									The payload carries price, liquidity and volume only. This page does not
									label a token safe or unsafe.
								</span>
							</span>
						</div>
					</div>
				</Panel>

				<Panel title="How to use this" sub="operator note">
					<p className="small muted">
						Use the mover list to find what changed, then open the token page for the pair
						ladder and quote panel. A large change is not an order instruction: the order path
						still applies server-side price, liquidity, pair-age and quote-freshness gates.
					</p>
					<div className="row mt-12">
						<Link className="btn sm" href="/markets">
							Open markets
						</Link>
						<Link className="btn sm" href="/alerts">
							Alerts
						</Link>
					</div>
					{firstOk?.ok && (
						<p className="micro mt-12">
							Source {firstOk.data.source.name} · observed {firstOk.data.source.observed_at}
						</p>
					)}
				</Panel>
			</div>
		</AppShell>
	);
}
