import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Badge, ButtonLink, Meter, Panel, TokenAvatar } from "@/components/ui";
import {
	asApiError,
	getChains,
	getLaunches,
	type ApiError,
	type Chain,
	type LaunchRow,
} from "@/lib/api";
import { ModeBanner } from "@/lib/live-banner";
import { ageLabel, chainInitial, initials, shortAddress } from "@/lib/live-format";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function one(value: string | string[] | undefined): string {
	return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

function launchesHref(chain: string): string {
	return chain === "all" ? "/launches" : `/launches?chain=${encodeURIComponent(chain)}`;
}

const unknown = (reason: string) => (
	<Badge tone="warn" title={reason}>
		unknown
	</Badge>
);

function CurveCell({ pct }: { pct: number | null }) {
	if (pct === null) {
		return <span className="small muted mono">—</span>;
	}
	const clamped = Math.max(0, Math.min(100, pct));
	return (
		<div>
			<Meter
				width={`${clamped}%`}
				tone={clamped >= 70 ? "warn" : undefined}
				label="Curve progress"
				valueText={`${clamped}%`}
			/>
			<span className="small muted mono">{clamped}%</span>
		</div>
	);
}

function TokenCell({ row }: { row: LaunchRow }) {
	return (
		<Link className="row" href={`/tokens/${row.chain}/${row.base_token}`}>
			<TokenAvatar size="sm">{initials(row.symbol)}</TokenAvatar>
			<span>
				<span className="strong">{row.symbol}</span>
				<span className="sub" style={{ display: "block" }}>
					{row.name} · {shortAddress(row.base_token)}
				</span>
			</span>
		</Link>
	);
}

function ErrorPanel({ error, retryHref }: { error: ApiError; retryHref: string }) {
	const rateLimited = error.code === "RATE_LIMITED" || error.status === 429;
	return (
		<Panel
			title={rateLimited ? "Rate limited" : "Launches unavailable"}
			action={<Badge tone="err">{error.code}</Badge>}
			footer={
				<div className="between">
					<span>Reason code and message come from the API unchanged.</span>
					<Link className="small" href={retryHref}>
						Retry →
					</Link>
				</div>
			}
		>
			<div className={`note ${rateLimited ? "warn" : "err"}`}>{error.message}</div>
			<p className="small muted mt-8">
				{rateLimited
					? "The upstream feed is throttling. No rows are shown rather than inventing a snapshot; wait and retry."
					: "No rows are shown for this request. The page does not substitute fixture data for a failed read."}
			</p>
			{error.retryable && (
				<p className="small muted mt-8">
					The API marks this failure retryable. Nothing was sent and no state changed.
				</p>
			)}
		</Panel>
	);
}

export default async function LaunchesPage({
	searchParams,
}: {
	searchParams: Promise<SearchParams>;
}) {
	const raw = await searchParams;
	const chain = one(raw.chain) || "all";

	const [chainsOutcome, launchesOutcome] = await Promise.all([
		getChains()
			.then((chains) => ({ ok: true as const, chains }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getLaunches({ chain, limit: 5 })
			.then((list) => ({ ok: true as const, list }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
	]);

	const chains: Chain[] = chainsOutcome.ok ? chainsOutcome.chains : [];
	const chainsError = chainsOutcome.ok ? null : chainsOutcome.error;
	const list = launchesOutcome.ok ? launchesOutcome.list : null;
	const error = launchesOutcome.ok ? null : launchesOutcome.error;
	const labelFor = (id: string) => chains.find((item) => item.id === id)?.name ?? id;

	return (
		<AppShell active="launches">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Discovery / Launches</div>
					<h1>Launches</h1>
					<p className="sub">
						Births feed. New tokens with age, curve progress, dev history and safety;
						one-click Trade per row. Paper mode only.
					</p>
				</div>
				<div className="page-actions">
					<Link className="btn" href="/markets">
						Markets
					</Link>
					<Link className="btn primary" href="/">
						Dashboard
					</Link>
				</div>
			</header>

			<ModeBanner
				source={list?.source.name ?? "unavailable"}
				observedAt={list?.source.observed_at}
				stale={list?.stale ?? false}
			/>

			<div className="tabs mb-12" role="group" aria-label="Launch chains">
				<Link
					className={`tab${chain === "all" ? " on" : ""}`}
					href={launchesHref("all")}
					aria-current={chain === "all" ? "page" : undefined}
				>
					All chains
				</Link>
				{chains.map((item) => (
					<Link
						key={item.id}
						className={`tab${chain === item.id ? " on" : ""}`}
						href={launchesHref(item.id)}
						aria-current={chain === item.id ? "page" : undefined}
						title={`${item.name} · ${item.tier} · ${item.capabilities.quote_kind}`}
					>
						{item.name}
					</Link>
				))}
				{chainsError && (
					<span className="small muted" title={chainsError.message}>
						Chain registry unavailable · {chainsError.code}
					</span>
				)}
			</div>

			{error && <ErrorPanel error={error} retryHref={launchesHref(chain)} />}

			{!error && list && (
				<Panel
					flush
					title="New token births"
					sub="newest first · every number carries source and age"
					action={
						list.stale ? (
							<Badge tone="warn">STALE</Badge>
						) : (
							<Badge tone="ghost">PAPER</Badge>
						)
					}
					footer={
						<div className="between">
							<span>
								Births feed · paper mode · unknown is <span className="mono">—</span>,
								never a guess.
							</span>
							<span>
								{list.rows.length} rows · limit 5 · source observed{" "}
								{ageLabel(list.source.observed_at)} ago · page 1/1
							</span>
						</div>
					}
				>
					{list.rows.length === 0 ? (
						<div className="panel-body">
							<div className="row mb-8">
								<Badge tone="warn">EMPTY_RESULT</Badge>
								<span className="small muted">No births matched this request.</span>
							</div>
							<p className="small muted">
								The feed returned no pairs for this chain. That is an empty result,
								not a zero price.
							</p>
							<div className="row mt-12">
								<Link className="btn sm" href={launchesHref("all")}>
									Reset filters
								</Link>
							</div>
						</div>
					) : (
						<div className="table-wrap">
							<table className="table">
								<thead>
									<tr>
										<th scope="col">Token</th>
										<th scope="col">Chain</th>
										<th scope="col" className="num">
											Age
										</th>
										<th scope="col">Curve progress</th>
										<th scope="col">Dev history</th>
										<th scope="col">Safety</th>
										<th scope="col">LP custody</th>
										<th scope="col">Trade</th>
									</tr>
								</thead>
								<tbody>
									{list.rows.map((row) => (
										<tr key={`${row.chain}:${row.base_token}`}>
											<td>
												<TokenCell row={row} />
											</td>
											<td>
												<span className="chain">
													<span className="sq">{chainInitial(labelFor(row.chain))}</span>
													{labelFor(row.chain)}
												</span>
											</td>
											<td className="num mono">{ageLabel(row.pair_created_at)}</td>
											<td>
												<CurveCell pct={row.curve_progress_pct} />
											</td>
											<td>{unknown("Dev history is not implemented in P0")}</td>
											<td>{unknown("Safety is not implemented in P0")}</td>
											<td>{unknown("LP custody is not implemented in P0")}</td>
											<td>
												<ButtonLink href={`/tokens/${row.chain}/${row.base_token}`} size="sm">
													Trade
												</ButtonLink>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</Panel>
			)}

			{!error && list && list.rows.length > 0 && (
				<div className="split mt-16">
					<Panel title="How to read this feed" sub="what each column is allowed to claim">
						<div className="stack-sm small muted">
							<div>
								• <span className="ink">Age</span> is the token birth age from the
								first observed pair, not quote freshness.
							</div>
							<div>
								• <span className="ink">Curve progress</span> is the fill toward
								graduation; <span className="mono">—</span> where the curve does
								not apply.
							</div>
							<div>
								• <span className="ink">Dev history</span> and{" "}
								<span className="ink">Safety</span> are unknown in P0. A miss is
								not a pass; execution gates stay fail-closed.
							</div>
							<div>
								• <span className="ink">Trade</span> opens the token quote panel.
								No row trades from the feed itself.
							</div>
						</div>
					</Panel>
					<Panel title="Source & freshness" sub="reason codes, no silent fallback">
						<div className="stack-sm small muted">
							<div>
								<Badge tone="ghost">PAPER</Badge> paper mode only; there is no live
								trading on this page.
							</div>
							<div>
								<Badge tone="ghost">FIXTURE</Badge> local sample data in fixture
								mode; never presented as live.
							</div>
							<div>
								<Badge tone="warn">unknown</Badge> not checked · no value is
								assumed.
							</div>
							<div>
								<Badge tone="ghost">—</Badge> unknown is an em dash, never zero.
							</div>
						</div>
					</Panel>
				</div>
			)}
		</AppShell>
	);
}
