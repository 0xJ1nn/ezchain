import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Fact, FixtureBadge, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
import { formatUsd } from "@/lib/live-format";
import { getPaperSnapshot } from "@/lib/paper-api";
import { apiHostOnly, webMode } from "@/lib/runtime";

export const dynamic = "force-dynamic";

export default async function WorkspacePage() {
	const mode = webMode();
	const snapshot = await getPaperSnapshot();
	const account = snapshot.account.ok ? snapshot.account.data : null;
	const error = !snapshot.account.ok ? snapshot.account.error : null;

	return (
		<AppShell active="workspace">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Workspace</div>
					<h1>Workspace</h1>
					<p className="sub">
						This deployment is single-tenant. There are no members, roles, invitations or
						shared audit in this build, so the page shows the deployment facts and the paper
						account it actually owns.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/settings">
						Settings
					</Link>
					<Link className="btn" href="/security">
						Security
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Paper account unavailable"
						code={error.code}
						message={error.message}
					>
						Workspace facts below still come from the server environment.
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Workspace paper account">
				<Stat
					label="Workspace"
					value="Personal"
					meta="single tenant · one deployment"
				/>
				<Stat
					label="Paper equity"
					value={account ? formatUsd(account.equity_usd) : "—"}
					meta={account ? "PAPER · server snapshot" : "unavailable"}
				/>
				<Stat
					label="Paper cash"
					value={account ? formatUsd(account.cash_usd) : "—"}
					meta={account ? "PAPER · ledger cash" : "unavailable"}
				/>
				<Stat
					label="Members"
					value="—"
					meta="no user model in this build"
				/>
			</section>

			<div className="grid cols-2">
				<Panel title="Deployment" sub="read-only facts">
					<div className="stack-sm">
						<Fact label="Tenancy" value="single-tenant" />
						<Fact label="Web mode" value={<span className="mono">{mode}</span>} />
						<Fact label="API host" value={<span className="mono">{apiHostOnly()}</span>} />
						<Fact label="Execution" value={<Badge tone="ghost">PAPER</Badge>} />
						<Fact
							label="Authentication"
							value="deployment bearer token · no per-user identity"
						/>
					</div>
					<p className="note mt-12">
						One deployment owns one paper account. The token identifies the installation, not
						a person, so there is nothing to invite or assign.
					</p>
				</Panel>

				<Panel
					title="Paper account"
					sub={snapshot.account.ok ? "server view model" : "unavailable"}
					action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">PAPER</Badge>}
				>
					{account ? (
						<div className="stack-sm">
							<div className="between small">
								<span className="muted">Cash</span>
								<span className="mono">{formatUsd(account.cash_usd)}</span>
							</div>
							<div className="between small">
								<span className="muted">Equity</span>
								<span className="mono">{formatUsd(account.equity_usd)}</span>
							</div>
							<div className="between small">
								<span className="muted">Realized</span>
								<span className="mono">{formatUsd(account.realized_usd)}</span>
							</div>
							<div className="between small">
								<span className="muted">Unrealized</span>
								<span className="mono">{formatUsd(account.unrealized_usd)}</span>
							</div>
							<div className="between small">
								<span className="muted">Open positions</span>
								<span className="mono">{account.positions.length}</span>
							</div>
						</div>
					) : (
						<div className="note err">
							{snapshot.account.ok ? "No account returned." : snapshot.account.error.message}
						</div>
					)}
					<div className="row mt-12">
						<ButtonLink href="/positions" size="sm">
							Positions
						</ButtonLink>
						<ButtonLink href="/performance" size="sm">
							Performance
						</ButtonLink>
					</div>
				</Panel>

				<Panel title="Members and roles" sub="not built">
					<div className="list">
						<div className="list-row">
							<Badge tone="warn">NO USERS</Badge>
							<span className="grow">
								<span className="title">No user accounts</span>
								<span className="sub">
									There is no sign-up, login, session store or user table in this branch.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO ROLES</Badge>
							<span className="grow">
								<span className="title">Owner / trader / viewer separation</span>
								<span className="sub">
									Not built. All mutations share the deployment token.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO AUDIT</Badge>
							<span className="grow">
								<span className="title">Shared audit log</span>
								<span className="sub">
									The kill switch stores a reason, but there is no per-operator audit trail.
								</span>
							</span>
						</div>
					</div>
				</Panel>

				<Panel title="What this workspace owns" sub="scope">
					<div className="list">
						<div className="list-row">
							<Badge tone="ok">PAPER</Badge>
							<span className="grow">
								<span className="title">One paper ledger and account</span>
								<span className="sub">Positions, orders, fills and reconciliation.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ok">STRATEGIES</Badge>
							<span className="grow">
								<span className="title">Installed strategy configurations</span>
								<span className="sub">Scheduled by the API engine, not by this page.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO WALLETS</Badge>
							<span className="grow">
								<span className="title">No key or wallet custody</span>
								<span className="sub">Nothing in this workspace can move funds.</span>
							</span>
						</div>
					</div>
				</Panel>
			</div>

			<Panel title="Team features" sub="deliberately absent" className="mt-16">
				<p className="small muted">
					The product targets a solo trader first. Team workspaces, invitations, approval
					policies and plan billing are not in this build; the wireframe sections exist, but
					there is no backend model to render. A fake member list would be worse than this
					honest state.
				</p>
			</Panel>
		</AppShell>
	);
}
