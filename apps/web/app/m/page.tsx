import { AppShell } from "@/components/shell";
import { ModeBadge } from "@/components/ops";
import { Badge, Panel, Stat } from "@/components/ui";
import { getKillSwitch, getPaperFills, getPaperSnapshot, type PaperPosition } from "@/lib/paper-api";
import { markAge } from "@/lib/position-view";

export const dynamic = "force-dynamic";

/** Human duration for an API timestamp. Unknown stays unknown; it is never "0s". */
function ageLabel(iso: string | null | undefined) {
	if (!iso) return "unknown";
	const ms = Date.parse(iso);
	if (Number.isNaN(ms)) return "unknown";
	const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
	if (seconds < 60) return `${seconds}s`;
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.round(minutes / 60);
	if (hours < 24) return `${hours}h`;
	return `${Math.round(hours / 24)}d`;
}

export default async function MobileWorkspacePage() {
	const snapshot = await getPaperSnapshot();
	const fills = await getPaperFills(5);
	const kill = await getKillSwitch();

	const mode = snapshot.mode;
	const account = snapshot.account;
	const positions: PaperPosition[] = snapshot.positions.ok ? snapshot.positions.data : [];
	const accountData = account.ok ? account.data : null;

	return (
		<AppShell active="mobile">
			<div
				className="page"
				data-testid="mobile-workspace"
				style={{ maxWidth: 420, margin: "0 auto" }}
			>
				<header className="page-head">
					<div className="titles">
						<div className="crumbs">Mobile workspace</div>
						<h1>Mobile workspace</h1>
						<p className="sub">
							{mode === "fixture"
								? "Read-only sample snapshot rendered from fixture data. It is not a server response and no number here is live."
								: "Read-only paper snapshot. Every number is computed by the server and carries its mode, source and age."}
						</p>
					</div>
					<div className="page-actions">
						<ModeBadge mode={mode} />
					</div>
				</header>

				<section className="grid" style={{ gap: 12 }}>
					<Panel title="Paper equity" sub={account.ok ? account.data.mode : "unavailable"}>
						{account.ok && accountData ? (
							<>
								<Stat
									label="Equity"
									value={accountData.equity_usd === null ? "—" : `$${accountData.equity_usd}`}
									meta={
										accountData.equity_usd === null
											? "unknown mark, not zero"
											: `mode ${accountData.mode} · source ledger`
									}
								/>
								<div className="micro mt-8">
									Cash ${accountData.cash_usd} · realized ${accountData.realized_usd}
									{accountData.unrealized_usd === null
										? " · unrealized unknown"
										: ` · unrealized ${accountData.unrealized_usd}`}
								</div>
								{accountData.marks_unknown ? (
									<Badge tone="warn">marks unknown</Badge>
								) : null}
							</>
						) : (
							<p className="small muted">
								Paper data unavailable
								{!account.ok ? `: ${account.error.code} ${account.error.message}` : ""}
							</p>
						)}
					</Panel>

					<Panel
						title="Open positions"
						sub={
							snapshot.positions.ok
								? `${positions.length} open`
								: "positions unavailable"
						}
					>
						{!snapshot.positions.ok ? (
							<p className="small muted">
								Positions unavailable: {snapshot.positions.error.code}{" "}
								{snapshot.positions.error.message}
							</p>
						) : positions.length === 0 ? (
							<p className="small muted">No open positions.</p>
						) : (
							<div className="list">
								{positions.map((position) => {
									const age = markAge(position);
									return (
										<div className="list-row" key={`${position.chain}:${position.token_address}`}>
											<span className="grow">
												<span className="title">{position.symbol}</span>
												<span className="sub">
													{position.chain} · qty {position.quantity}
												</span>
											</span>
											<span className="mono">
												{position.mark_usd === null
													? "unknown mark"
													: `$${position.mark_usd}`}
											</span>
											<span className="micro">
												{position.source?.name ?? "no source"} · {age.label}
												{age.stale ? " · stale" : ""}
												{age.unknown ? " · unknown" : ""}
											</span>
										</div>
									);
								})}
							</div>
						)}
					</Panel>

					<Panel title="Last fills" sub={fills.ok ? `${fills.data.length} fills` : "unavailable"}>
						{!fills.ok ? (
							<p className="small muted">
								Fills unavailable: {fills.error.code} {fills.error.message}
							</p>
						) : fills.data.length === 0 ? (
							<p className="small muted">No fills yet.</p>
						) : (
							<div className="list">
								{fills.data.map((fill) => (
									<div className="list-row" key={fill.id}>
										<span className="grow">
											<span className="title">
												{fill.side} {fill.token_address.slice(0, 8)}
											</span>
											<span className="sub">
												qty {fill.quantity} @ ${fill.price_usd}
											</span>
										</span>
										<span className="micro">
											{fill.quote_kind} · {ageLabel(fill.created_at)}
										</span>
									</div>
								))}
							</div>
						)}
					</Panel>

					<Panel title="Kill switch" sub={kill.ok ? "read-only" : "unavailable"}>
						{!kill.ok ? (
							<p className="small muted">
								Kill switch unavailable: {kill.error.code} {kill.error.message}
							</p>
						) : (
							<>
								<Badge tone={kill.data.active ? "err" : "ok"}>
									{kill.data.active ? "armed" : "clear"}
								</Badge>
								<div className="micro mt-8">
									mode paper · scope {kill.data.scopes.join(" + ")} · source system flag
								</div>
								<div className="micro">
									age {ageLabel(kill.data.updated_at)} · reason{" "}
									{kill.data.reason ?? "none"}
								</div>
							</>
						)}
					</Panel>
				</section>
			</div>
		</AppShell>
	);
}
