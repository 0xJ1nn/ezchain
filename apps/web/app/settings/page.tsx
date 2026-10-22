import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Fact, ModeBadge } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import {
	apiHostOnly,
	apiTokenConfigured,
	publicUrlConfigured,
	webMode,
} from "@/lib/runtime";

export const dynamic = "force-dynamic";

const DOCS = "https://github.com/0xJ1nn/ezchain/blob/main";

export default function SettingsPage() {
	const mode = webMode();

	return (
		<AppShell active="settings">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Settings</div>
					<h1>Settings</h1>
					<p className="sub">
						Safe runtime facts for this deployment. Configuration is read from the server
						environment; this page has no controls that would pretend to change a process it
						cannot reach.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/operations">
						Operations
					</Link>
					<Link className="btn" href="/security">
						Security
					</Link>
				</div>
			</header>

			<div className="grid cols-2">
				<Panel title="Runtime" sub="read on the server">
					<div className="stack-sm">
						<Fact label="Web data mode" value={<span className="mono">{mode}</span>} />
						<Fact
							label="API host"
							value={<span className="mono">{apiHostOnly()}</span>}
						/>
						<Fact
							label="API token"
							value={
								apiTokenConfigured() ? (
									<Badge tone="ok">configured · server-side</Badge>
								) : (
									<Badge tone="warn">not configured</Badge>
								)
							}
						/>
						<Fact
							label="Public URL override"
							value={
								publicUrlConfigured() ? (
									<Badge tone="ghost">configured</Badge>
								) : (
									<span className="muted">not set; request host is used</span>
								)
							}
						/>
					</div>
					<p className="note mt-12">
						Only the API host is printed. The token value never reaches this page, the browser
						bundle or any HTML response. Mutations go through the Next proxy, which injects
						the deployment token server-side when one is configured.
					</p>
				</Panel>

				<Panel title="Data source" sub="fixture or live">
					<div className="stack-sm">
						<Fact label="EZCHAIN_WEB_MODE" value={<span className="mono">{mode}</span>} />
						<Fact
							label="Market source"
							value={
								mode === "fixture" ? (
									<span className="muted">sample fixture · no API call</span>
								) : (
									<span className="muted">API · provider named per row</span>
								)
							}
						/>
						<Fact
							label="Execution mode"
							value={<Badge tone="ghost">PAPER</Badge>}
						/>
					</div>
					<p className="note warn mt-12">
						<strong>No silent fallback.</strong> In live mode a failed read renders its reason
						code; it never swaps in fixture rows. Fixture mode labels sample data FIXTURE on
						every panel.
					</p>
				</Panel>

				<Panel title="Server-side configuration" sub="one home">
					<p className="small muted">
						These are process environment keys owned by the API, not editable in the browser.
						They are listed by name so an operator can find them in the deployment docs.
					</p>
					<div className="stack-sm small mono mt-12">
						<div>EZCHAIN_DATABASE_URL</div>
						<div>EZCHAIN_API_TOKEN</div>
						<div>EZCHAIN_DATA_MODE</div>
						<div>EZCHAIN_PAPER_FEE_BPS</div>
						<div>EZCHAIN_MAX_ORDER_USD</div>
						<div>EZCHAIN_MAX_POSITION_USD</div>
						<div>EZCHAIN_MAX_DAILY_LOSS_USD</div>
						<div>EZCHAIN_MAX_QUOTE_AGE_SECONDS</div>
						<div>EZCHAIN_MARKET_SCAN_INTERVAL_SECONDS</div>
					</div>
					<p className="note mt-12">
						Values are validated at API boot and are intentionally not echoed to a public
						browser surface.
					</p>
				</Panel>

				<Panel title="What is not here" sub="honest boundary">
					<div className="list">
						<div className="list-row">
							<Badge tone="warn">NO SAVE</Badge>
							<span className="grow">
								<span className="title">Editable settings form</span>
								<span className="sub">
									No settings mutation endpoint exists; a form would be a dead control.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO APPEARANCE</Badge>
							<span className="grow">
								<span className="title">Theme and density</span>
								<span className="sub">
									The dark visual pass and user theme preference are outside this build.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO RESET</Badge>
							<span className="grow">
								<span className="title">Data reset or danger zone</span>
								<span className="sub">
									A reset is a documented operator action outside this UI, not a browser
									button.
								</span>
							</span>
						</div>
					</div>
					<div className="row mt-12">
						<a className="btn sm" href={`${DOCS}/docs/INFRA.md`} rel="noreferrer">
							Environment docs
						</a>
						<a className="btn sm" href={`${DOCS}/apps/web/README.md`} rel="noreferrer">
							Web README
						</a>
						<ButtonLink href="/developer" size="sm">
							Developer
						</ButtonLink>
					</div>
				</Panel>
			</div>

			<Panel title="Change summary" sub="server process" className="mt-16">
				<p className="small muted">
					Changing any key above requires a deliberate deployment change and a process
					restart. This page cannot show a pending change because it has no write path. Health
					and migration state are on{" "}
					<Link href="/operations">Operations</Link>.
				</p>
			</Panel>
		</AppShell>
	);
}
