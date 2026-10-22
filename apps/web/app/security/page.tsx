import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Fact, ModeBadge } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { asApiError, getChains } from "@/lib/api";
import { apiTokenConfigured, webMode } from "@/lib/runtime";

export const dynamic = "force-dynamic";

const DOCS = "https://github.com/0xJ1nn/ezchain/blob/main";

export default async function SecurityPage() {
	const mode = webMode();
	const chainsOutcome = await getChains()
		.then((chains) => ({ ok: true as const, chains }))
		.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) }));
	const chains = chainsOutcome.ok ? chainsOutcome.chains : [];
	const liveChains = chains.filter((chain) => chain.capabilities.live_execute);

	return (
		<AppShell active="security">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Security</div>
					<h1>Security</h1>
					<p className="sub">
						What actually protects this deployment, and what is deliberately absent. No
						account system, key custody or live signing exists in this build; the page says so
						instead of showing inactive security controls.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/risk">
						Kill switch
					</Link>
					<a className="btn" href={`${DOCS}/SECURITY.md`} rel="noreferrer">
						Security policy
					</a>
				</div>
			</header>

			<div className="grid cols-2">
				<Panel title="Authentication posture" sub="deployment token">
					<div className="stack-sm">
						<Fact label="Read requests" value="open · no bearer required" />
						<Fact
							label="Mutations"
							value={
								apiTokenConfigured()
									? "bearer token required · proxy injects it"
									: "no token in this web process · protected calls will be rejected"
							}
						/>
						<Fact
							label="Token location"
							value={apiTokenConfigured() ? "server environment only" : "not configured"}
						/>
						<Fact label="Browser credential" value="none · injected by the Next proxy" />
						<Fact label="Cross-origin mutation" value="denied by the proxy" />
						<Fact label="Per-user identity" value={<Badge tone="warn">not built</Badge>} />
					</div>
					<p className="note mt-12">
						The token identifies the deployment, not an operator. There are no login sessions,
						passkeys, roles or audit identities. A mutation records its reason where the API
						model has one, but not a person. A production API refuses to boot without a
						token; development with no token allows mutations with a warning, which must
						never be exposed.
					</p>
				</Panel>

				<Panel title="Key custody" sub="not built" action={<Badge tone="warn">NO KEYS</Badge>}>
					<div className="note warn">
						<strong>There is no key material in this build.</strong> The web app never receives
						a private key, seed phrase or signing secret. The architecture reserves custody
						for <span className="mono">services/executor</span>, but this branch has no vault,
						key import, hardware approval or signing path exposed to the UI.
					</div>
					<div className="list mt-12">
						<div className="list-row">
							<Badge tone="ghost">WEB</Badge>
							<span className="grow">
								<span className="title">No key fields</span>
								<span className="sub">Fixtures and API responses contain no key material.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ghost">API</Badge>
							<span className="grow">
								<span className="title">No vault consumer</span>
								<span className="sub">The vault and envelope encryption are not built here.</span>
							</span>
						</div>
					</div>
				</Panel>

				<Panel title="Live execution" sub="not built">
					<div className="stack-sm">
						<Fact
							label="Chains advertising live_execute"
							value={
								chainsOutcome.ok ? (
									<span className="mono">{liveChains.length}</span>
								) : (
									<span className="muted">—</span>
								)
							}
						/>
						<Fact label="Execution mode" value={<Badge tone="ghost">PAPER</Badge>} />
						<Fact label="Signing path" value="none" />
						<Fact label="Withdrawals" value="none" />
					</div>
					<p className="note mt-12">
						{chainsOutcome.ok
							? liveChains.length === 0
								? "No registered chain advertises live execution. Every order in this build is paper."
								: "At least one chain advertises a live capability; the UI still has no signing or key flow, so do not treat this page as a live-execution approval."
							: chainsOutcome.error.message}
					</p>
				</Panel>

				<Panel title="Operator controls" sub="available today">
					<div className="list">
						<div className="list-row">
							<Badge tone="ok">READY</Badge>
							<span className="grow">
								<span className="title">Persisted kill switch</span>
								<span className="sub">
									Strategy-scoped. Requires a bearer-authenticated mutation and a reason.
								</span>
							</span>
							<ButtonLink href="/risk" size="sm">
								Open
							</ButtonLink>
						</div>
						<div className="list-row">
							<Badge tone="ok">READY</Badge>
							<span className="grow">
								<span className="title">Paper-only execution</span>
								<span className="sub">No funds can move because no signing route exists.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NOT BUILT</Badge>
							<span className="grow">
								<span className="title">2FA, sessions, API keys and audit log</span>
								<span className="sub">
									These wireframe sections have no backend model in this branch.
								</span>
							</span>
						</div>
					</div>
				</Panel>
			</div>

			<Panel title="Secret-handling rules" sub="enforced by process" className="mt-16">
				<div className="grid cols-2">
					<div className="small muted">
						<div className="strong ink mb-8">Must hold</div>
						<ul style={{ paddingLeft: 18, margin: 0 }}>
							<li>The API token lives only in the server environment.</li>
							<li>The browser talks to the Next proxy, never to the token.</li>
							<li>Inbound Authorization headers are discarded by the proxy.</li>
							<li>CSV exports contain rows, never credentials.</li>
							<li>Key material belongs only in the executor, and is absent here.</li>
						</ul>
					</div>
					<div className="small muted">
						<div className="strong ink mb-8">Not available</div>
						<ul style={{ paddingLeft: 18, margin: 0 }}>
							<li>No secret entry field and no secret read endpoint.</li>
							<li>No session cookie, 2FA enrollment or recovery phrase.</li>
							<li>No audit identity per operator.</li>
							<li>No live withdrawal or transfer action.</li>
						</ul>
					</div>
				</div>
			</Panel>
		</AppShell>
	);
}
